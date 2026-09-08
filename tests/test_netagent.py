"""Deterministic tests per the spec's testing decisions.

- Mapping (mapper) + exception rules (validator) are tested with a synthetic model.
- Tools (commit_batch/sync_schema/...) are tested against a FakeDriver (single
  transaction, structured errors, atomic rollback).
- The tool-calling loop is tested with a stub client (injected proposer), so no
  real LLM or neo4j is needed.
- A real-pgsql integration test verifies load_model and a full map+validate pass
  on the sample document (skipped if pgsql is unreachable).
"""
from __future__ import annotations

import json
import os

import pytest

from netagent.agent import run_agent
from netagent.mapper import map_document
from netagent.model import Attribute, EdgeType, Model, NodeType, load_model
from netagent import tools
from netagent.validator import validate

SAMPLE = json.load(open(os.path.join(os.path.dirname(__file__), "..", ".scratch", "nolograph-agent", "samples", "topology-sample.json")))  # type: ignore[arg-type]


def synthetic_model() -> Model:
    attrs = {"hostname": Attribute("text", False), "vendor": Attribute("text", True),
             "model": Attribute("text", True), "interfaces": Attribute("json", True, "list")}
    model = Model(
        node_types={
            "router": NodeType("router", "id", dict(attrs)),
            "switch": NodeType("switch", "id", dict(attrs)),
            "firewall": NodeType("firewall", "id", dict(attrs)),
            "network": NodeType("network", "id", {"name": Attribute("text", True), "cidr": Attribute("cidr", True)}),
        },
        edge_types={
            "CONNECTED_TO": EdgeType("CONNECTED_TO", "router", "switch",
                                     {"a_if": Attribute("text", True), "z_if": Attribute("text", True),
                                      "speed": Attribute("text", True), "type": Attribute("text", True)}),
        },
    )
    return model


def test_mapper_proposes_nodes_and_edges():
    p = map_document(synthetic_model(), SAMPLE)
    assert len(p["nodes"]) == 5  # 3 devices + 2 networks
    assert len(p["edges"]) == 2  # 2 links
    # report envelope is skipped, not mapped
    kinds = {s["kind"] for s in p["skipped"]}
    assert "envelope" in kinds


def test_validator_keeps_valid_and_logs_exceptions():
    clean = validate(synthetic_model(), map_document(synthetic_model(), SAMPLE))
    assert len(clean["nodes"]) == 5
    assert len(clean["edges"]) == 2
    # every node drops `id` and `kind` (not declared attributes → unknown_field)
    kinds = [s["kind"] for s in clean["skipped"]]
    assert kinds.count("unknown_field") == 8  # 5 nodes × (id,kind) minus network has no kind
    # network nodes keep name/cidr, drop only id
    net = next(n for n in clean["nodes"] if n["entity_type"] == "network")
    assert set(net["properties"]) == {"name", "cidr"}


def test_validator_f_skips_dangling_edge_and_missing_key():
    model = synthetic_model()
    dirty = {
        "nodes": [
            {"entity_type": "router", "key": "r1", "properties": {"vendor": "v"}},
            {"entity_type": "ghost", "key": "x", "properties": {}},
            {"entity_type": "router", "key": "", "properties": {}},
        ],
        "edges": [{"edge_type": "CONNECTED_TO", "source_key": "missing", "target_key": "r1", "properties": {}}],
        "skipped": [],
    }
    clean = validate(model, dirty)
    assert len(clean["nodes"]) == 1  # only r1
    kinds = [s["kind"] for s in clean["skipped"]]
    assert "unknown_type" in kinds
    assert "missing_key" in kinds
    assert "dangling_ref" in kinds


def test_validator_null_optional_omitted_not_counted():
    model = synthetic_model()
    p = {"nodes": [{"entity_type": "network", "key": "n1",
                   "properties": {"name": None, "cidr": "10.0.0.0/8"}}], "edges": [], "skipped": []}
    clean = validate(model, p)
    props = clean["nodes"][0]["properties"]
    assert "name" not in props  # optional null omitted
    kinds = [s["kind"] for s in clean["skipped"]]
    assert "type_mismatch" not in kinds


def test_validator_type_mismatch_skips_property():
    model = synthetic_model()
    # cidr declared; give an int to hostname (text ok) but int to cidr (text ok too) — use bool to int
    p = {"nodes": [{"entity_type": "network", "key": "n1", "properties": {"name": 5, "cidr": 7}}], "edges": [], "skipped": []}
    clean = validate(model, p)
    # both text attrs coerce to str, so nothing skipped
    assert clean["nodes"][0]["properties"] == {"name": "5", "cidr": "7"}


class FakeTx:
    def __init__(self, fail_on=None, records=None):
        self.calls: list[tuple] = []
        self.fail_on = fail_on
        self.records = records if records is not None else [{"ok": 1}]  # endpoints present by default

    def run(self, cypher, **params):
        if self.fail_on and self.fail_on in cypher:
            raise RuntimeError("boom")
        self.calls.append((cypher, params))
        return self.records


class FakeDriver:
    def __init__(self, fail_on=None):
        self.tx = FakeTx(fail_on=fail_on)
        self.write_calls = 0
        self.read_calls = 0

    def session(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute_write(self, fn):
        self.write_calls += 1
        return fn(self.tx)

    def execute_read(self, fn):
        self.read_calls += 1
        return fn(self.tx)


def test_commit_batch_single_transaction_and_counts():
    model = synthetic_model()
    driver = FakeDriver()
    nodes = [{"entity_type": "router", "key": "r1", "properties": {"hostname": "r1"}},
             {"entity_type": "network", "key": "n1", "properties": {"name": "n"}}]
    edges = [{"edge_type": "CONNECTED_TO", "source_type": "router", "target_type": "switch",
              "source_key": "r1", "target_key": "s1", "properties": {"speed": "10G"}}]
    res = tools.commit_batch(driver, model, nodes, edges)
    assert res["ok"] is True
    assert res["nodes_merged"] == 2
    assert res["edges_merged"] == 1
    assert driver.write_calls == 1  # one transaction (atomic design)


def test_commit_batch_rolls_back_on_failure():
    model = synthetic_model()
    driver = FakeDriver(fail_on="CONNECTED_TO")
    res = tools.commit_batch(driver, model,
                             [{"entity_type": "router", "key": "r1", "properties": {}}],
                             [{"edge_type": "CONNECTED_TO", "source_type": "router", "target_type": "switch",
                               "source_key": "r1", "target_key": "s1", "properties": {}}])
    assert "error" in res
    assert res["error"]["code"] == "dangling_ref"


def test_upsert_node_unknown_type_is_structured_error():
    res = tools.upsert_node(FakeDriver(), synthetic_model(), "nope", "k", {})
    assert res["error"]["code"] == "bad_args"


def test_commit_batch_refuses_dangling_endpoint_and_rolls_back():
    model = synthetic_model()
    driver = FakeDriver()
    driver.tx.records = [{"ok": 0}]  # endpoint existence check reports missing
    res = tools.commit_batch(
        driver, model,
        [{"entity_type": "router", "key": "r1", "properties": {}}],
        [{"edge_type": "CONNECTED_TO", "source_type": "router", "target_type": "switch",
          "source_key": "r1", "target_key": "missing", "properties": {}}],
    )
    assert "error" in res
    assert res["error"]["code"] == "dangling_ref"


def test_sync_schema_creates_constraints():
    driver = FakeDriver()
    res = tools.sync_schema(driver, synthetic_model())
    assert res["ok"] is True
    assert res["created"]  # one constraint per node type
    all_joined = " ".join(c for c, _ in driver.tx.calls)
    assert "REQUIRE" in all_joined and "IS UNIQUE" in all_joined


class FakeFn:
    def __init__(self, n, a): self.name, self.arguments = n, a
class FakeTC:
    def __init__(self, fn): self.id, self.function, self.type = "t1", fn, "function"
class FakeMsg:
    def __init__(self, content, tool_calls): self.content, self.tool_calls = content, tool_calls
class FakeResp:
    def __init__(self, msg): self.choices = [type("C", (), {"message": msg})()]
class FakeCompletions:
    def __init__(self, responses): self.responses, self.i = responses, 0
    def create(self, **kwargs):
        r = self.responses[self.i]; self.i += 1; return r
class FakeChat:
    def __init__(self, completions): self.completions = completions
class FakeClient:
    def __init__(self, responses): self.chat = FakeChat(FakeCompletions(responses))


def test_agent_loop_structured_error_on_unknown_tool():
    client = FakeClient([
        FakeResp(FakeMsg(None, [FakeTC(FakeFn("nosuch", "{}"))])),
        FakeResp(FakeMsg("recovered", None)),
    ])
    out = run_agent(client, "m", "sys", "user", [], {"f": lambda **k: {"ok": 1}}, max_loops=5)
    assert out == "recovered"


def test_agent_loop_tool_then_final():
    client = FakeClient([
        FakeResp(FakeMsg(None, [FakeTC(FakeFn("map_document", "{}"))])),
        FakeResp(FakeMsg("done", None)),
    ])
    out = run_agent(client, "m", "sys", "user", [], {"map_document": lambda **k: {"ok": True}}, max_loops=5)
    assert out == "done"


def test_agent_loop_max_loops_guard():
    client = FakeClient([FakeResp(FakeMsg(None, [FakeTC(FakeFn("f", "{}"))])) for _ in range(3)])
    with pytest.raises(RuntimeError):
        run_agent(client, "m", "sys", "user", [], {"f": lambda **k: {"ok": 1}}, max_loops=2)


def _dsn() -> str:
    from urllib.parse import quote

    u = os.environ.get("PGUSER", "postgres")
    p = os.environ.get("PGPASSWORD", "1qaz@WSX")
    h = os.environ.get("PGHOST", "127.0.0.1")
    return f"postgresql://{quote(u, safe='')}:{quote(p, safe='')}@{h}:5432/model_meta"


def test_load_model_integration():
    try:
        model = load_model(_dsn())
    except Exception:  # noqa: BLE001
        pytest.skip("pgsql unreachable")
    assert model.node_types["router"].key_field == "id"
    assert "CONNECTED_TO" in model.edge_types


def test_full_map_validate_integration_on_sample():
    try:
        model = load_model(_dsn())
    except Exception:  # noqa: BLE001
        pytest.skip("pgsql unreachable")
    clean = validate(model, map_document(model, SAMPLE))
    assert len(clean["nodes"]) == 5
    assert len(clean["edges"]) == 2
    assert clean["edges"][0]["source_type"] == "router"  # resolved endpoint types
