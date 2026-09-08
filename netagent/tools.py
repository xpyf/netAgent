"""Neo4j tools (signed in ticket 03), with an injectable driver + structured errors.

Each tool returns a plain dict: either `{"ok": ..., ...}` or `{"error": {"code", "message", "detail?"}}`,
never raising (contract rule 6). The `driver` is a neo4j Driver exposing `execute_read(fn)` /
`execute_write(fn)`; a FakeDriver in tests provides the same surface.
"""
from __future__ import annotations

from typing import Any

from netagent.model import Model


def err(code: str, message: str, detail: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, **({"detail": str(detail)} if detail is not None else {})}}


def _run_write(driver, fn) -> Any:
    """Execute fn(tx)->result inside one write transaction; rollback on any failure."""
    with driver.session() as session:
        return session.execute_write(fn)


def sync_schema(driver, model: Model) -> dict[str, Any]:
    """Rule: uniqueness constraint per node type on key_field (MERGE idempotency)."""
    try:
        created = []
        for t in model.node_types.values():
            cypher = (
                f"CREATE CONSTRAINT uniq_{t.name}_{t.key_field} IF NOT EXISTS "
                f"FOR (n:{t.name}) REQUIRE n.{t.key_field} IS UNIQUE"
            )
            _run_write(driver, lambda tx: tx.run(cypher))
            created.append(f"{t.name}.{t.key_field}")
        return {"ok": True, "created": created}
    except Exception as e:  # noqa: BLE001
        return err("constraint_violation", "sync_schema failed", e)


def commit_batch(driver, model: Model, nodes: list[dict], edges: list[dict]) -> dict[str, Any]:
    """Write all nodes then edges in ONE transaction (ticket 03 / Q12 atomicity)."""
    try:
        by_type: dict[str, list[dict]] = {}
        for n in nodes:
            by_type.setdefault(n["entity_type"], []).append(n)

        def work(tx) -> dict[str, int]:
            merged_nodes = 0
            for tname, rows in by_type.items():
                t = model.node_type(tname)
                if not t:
                    continue
                tx.run(f"UNWIND $rows AS r MERGE (n:{t.name} {{{t.key_field}: r.key}}) ON CREATE SET n += r.properties", rows=rows)
                merged_nodes += len(rows)
            merged_edges = 0
            for e in edges:
                st = model.node_type(e["source_type"])
                tt = model.node_type(e["target_type"])
                if not st or not tt:
                    raise RuntimeError(f"unknown endpoint type in edge {e}")
                sk_f, tk_f = st.key_field, tt.key_field
                ok_rec = list(
                    tx.run(
                        f"OPTIONAL MATCH (a:{e['source_type']} {{{sk_f}: $sk}}), "
                        f"(b:{e['target_type']} {{{tk_f}: $tk}}) "
                        f"RETURN CASE WHEN a IS NULL OR b IS NULL THEN 0 ELSE 1 END AS ok",
                        sk=e["source_key"], tk=e["target_key"],
                    )
                )
                if not ok_rec or not ok_rec[0]["ok"]:
                    raise RuntimeError(f"dangling edge: missing endpoint {e['source_key']}->{e['target_key']}")
                tx.run(
                    f"UNWIND $rows AS e "
                    f"MATCH (a:{e['source_type']} {{{sk_f}: e.source_key}}) "
                    f"MATCH (b:{e['target_type']} {{{tk_f}: e.target_key}}) "
                    f"MERGE (a)-[r:{e['edge_type']}]->(b) ON CREATE SET r += e.properties",
                    rows=[e],
                )
                merged_edges += 1
            return {"nodes_merged": merged_nodes, "edges_merged": merged_edges}

        return {"ok": True, **_run_write(driver, work)}
    except Exception as e:  # noqa: BLE001
        return err("dangling_ref", "commit_batch failed (rolled back)", e)


def upsert_node(driver, model: Model, entity_type: str, key: Any, properties: dict) -> dict[str, Any]:
    t = model.node_type(entity_type)
    if not t:
        return err("bad_args", f"unknown node type '{entity_type}'")
    try:
        _run_write(
            driver,
            lambda tx: tx.run(
                f"MERGE (n:{t.name} {{{t.key_field}: $key}}) ON CREATE SET n += $props",
                key=key,
                props=properties,
            ),
        )
        return {"ok": True, "key": key}
    except Exception as e:  # noqa: BLE001
        return err("constraint_violation", "upsert_node failed", e)


def upsert_edge(
    driver, model: Model, edge_type: str,
    source_type: str, target_type: str, source_key: Any, target_key: Any, properties: dict,
) -> dict[str, Any]:
    et = model.edge_type(edge_type)
    if not et:
        return err("bad_args", f"unknown edge type '{edge_type}'")
    try:
        _run_write(
            driver,
            lambda tx: tx.run(
                f"MERGE (a:{source_type} {{{source_key}: $sk}}) "
                f"MERGE (b:{target_type} {{{target_key}: $tk}}) "
                f"MERGE (a)-[r:{edge_type}]->(b) ON CREATE SET r += $props",
                sk=source_key, tk=target_key, props=properties,
            ),
        )
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        return err("constraint_violation", "upsert_edge failed", e)


def match(driver, label: str, key: Any = None) -> dict[str, Any]:
    try:
        def read(tx):
            if key is None:
                return list(tx.run(f"MATCH (n:{label}) RETURN n"))
            return list(tx.run(f"MATCH (n:{label} {{key: $key}}) RETURN n", key=key))

        with driver.session() as session:
            records = session.execute_read(read)
        return {"ok": True, "records": [dict(r["n"]) for r in records]}
    except Exception as e:  # noqa: BLE001
        return err("bad_args", "match failed", e)
