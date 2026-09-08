"""Entry point: JSON file -> model -> map -> validate -> commit -> report.

Default runs the deterministic pipeline (mapper + validator + tools), which needs
no LLM. `--agent` drives the LLM tool-calling loop against the same tools.
`--dry-run` stops after mapping+validation (no neo4j needed). Config is all env
vars (ticket 05 rule 6): PGSQL_DSN, NEO4J_URI/NEO4J_AUTH, LLM_BASE_URL/LLM_API_KEY/LLM_MODEL.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from typing import Any

from neo4j import GraphDatabase

from netagent import tools
from netagent.agent import run_agent
from netagent.mapper import map_document
from netagent.model import load_model, Model
from netagent.validator import validate

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "map_document",
            "description": "Map the JSON document into proposed nodes/edges per the model",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sync_schema",
            "description": "Ensure uniqueness constraints exist for every node type's key_field",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "commit_batch",
            "description": "Atomically write all validated nodes and edges to neo4j in one transaction",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def _report(clean: dict[str, Any], model: Model, res: dict[str, Any] | None = None) -> dict[str, Any]:
    res = res or {}
    skipped = clean.get("skipped", [])
    return {
        "model_node_types": len(model.node_types),
        "model_edge_types": len(model.edge_types),
        "nodes_merged": res.get("nodes_merged", 0),
        "edges_merged": res.get("edges_merged", 0),
        "skipped": len(skipped),
        "skipped_by_kind": dict(Counter(s.get("kind") for s in skipped)),
    }


def _ingest(json_path: str, *, agent_mode: bool, dry_run: bool) -> dict[str, Any]:
    dsn = os.environ.get("PGSQL_DSN")
    if not dsn:
        return {"error": {"code": "bad_args", "message": "PGSQL_DSN not set"}}
    model = load_model(dsn)
    try:
        with open(json_path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return {"error": {"code": "bad_args", "message": f"cannot read '{json_path}': {exc}"}}

    clean = validate(model, map_document(model, doc))
    if dry_run:
        return {"_dry_run": True, **_report(clean, model), "nodes": clean["nodes"], "edges": clean["edges"]}
    if not clean["nodes"] and not clean["edges"]:
        return {"ok": False, "reason": "nothing to write after validation", **_report(clean, model)}

    uri = os.environ.get("NEO4J_URI")
    auth_str = os.environ.get("NEO4J_AUTH")
    if not uri or not auth_str:
        return {"error": {"code": "bad_args", "message": "NEO4J_URI/NEO4J_AUTH not set"}}
    user, _, pw = auth_str.partition(":")
    driver = GraphDatabase.driver(uri, auth=(user, pw))
    try:
        if agent_mode:
            for v in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
                if not os.environ.get(v):
                    return {"error": {"code": "bad_args", "message": f"{v} not set"}}
            # capture the actual write result so the report shows real merge counts
            commit_res: dict[str, Any] = {}

            def commit(**_: Any) -> dict[str, Any]:
                res = tools.commit_batch(driver, model, clean["nodes"], clean["edges"])
                commit_res.update(res)
                return res

            registry = {
                "map_document": lambda **_: map_document(model, doc),
                "sync_schema": lambda **_: tools.sync_schema(driver, model),
                "commit_batch": commit,
            }
            out = run_agent(_client(), os.environ["LLM_MODEL"], _AGENT_SYSTEM, str(doc), TOOL_DEFS, registry)
            return {"agent": out, **_report(clean, model, commit_res or None)}
        sync = tools.sync_schema(driver, model)
        if "error" in sync:
            return sync
        res = tools.commit_batch(driver, model, clean["nodes"], clean["edges"])
        if "error" in res:
            return res
        return {"ok": True, **_report(clean, model, res)}
    finally:
        driver.close()


_AGENT_SYSTEM = "You ingest a network-topology JSON document into a neo4j graph per the provided knowledge-graph model. Use the tools."


def _client():
    from openai import OpenAI

    return OpenAI(base_url=os.environ["LLM_BASE_URL"], api_key=os.environ["LLM_API_KEY"])


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest a JSON topology into neo4j per the model")
    ap.add_argument("json", help="path to the JSON data source")
    ap.add_argument("--agent", action="store_true", help="run the LLM tool-calling loop instead of deterministic pipeline")
    ap.add_argument("--dry-run", action="store_true", help="map+validate only, no neo4j write")
    args = ap.parse_args()
    print(json.dumps(_ingest(args.json, agent_mode=args.agent, dry_run=args.dry_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
