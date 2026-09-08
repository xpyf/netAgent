"""Rule F exception filtering (ticket 02): deterministic skip + log of bad items.

Takes a mapper Proposal and returns a clean Proposal {nodes, edges} whose
valid items are still written atomically; skipped items only get logged.
"""
from __future__ import annotations

from typing import Any

from netagent.model import Model

def _coerce(attr_type: str, val: Any) -> tuple[Any, bool]:
    if val is None:
        return None, False
    if attr_type == "text":
        return str(val), True
    if attr_type in ("ip", "cidr", "datetime"):
        return str(val), True
    if attr_type in ("int", "float"):
        if isinstance(val, bool):
            return None, False
        if attr_type == "int":
            if isinstance(val, int):
                return val, True
            try:
                return int(str(val)), True
            except (TypeError, ValueError):
                return None, False
        try:
            return float(val), True
        except (TypeError, ValueError):
            try:
                return float(str(val)), True
            except (TypeError, ValueError):
                return None, False
    if attr_type == "bool":
        if isinstance(val, bool):
            return val, True
        if isinstance(val, str) and val.lower() in ("true", "false"):
            return val.lower() == "true", True
        return None, False
    if attr_type == "json":
        return val, True
    return None, False


def _filter_props(attrs: dict[str, Any], obj: dict, skipped: list[dict[str, str]], ref: str) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for k, v in obj.items():
        attr = attrs.get(k)
        if not attr:
            skipped.append({"kind": "unknown_field", "reason": f"'{k}' not in model", "ref": ref})
            continue
        if v is None:
            if attr.optional:
                continue  # nullable property omitted (neo4j null == absent)
            skipped.append({"kind": "type_mismatch", "reason": f"required '{k}' is null", "ref": ref})
            continue
        cast, ok = _coerce(attr.attr_type, v)
        if not ok:
            skipped.append({"kind": "type_mismatch", "reason": f"'{k}' not {attr.attr_type}", "ref": ref})
            continue
        clean[k] = cast
    return clean


def validate(model: Model, proposal: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """Apply rule F; returns clean proposal with skipped items appended to `skipped`."""
    skipped: list[dict[str, str]] = list(proposal.get("skipped", []))
    clean_nodes: list[dict[str, Any]] = []
    node_keys: set[Any] = set()

    for n in proposal.get("nodes", []):
        t = model.node_type(n.get("entity_type", ""))
        if not t:
            skipped.append({"kind": "unknown_type", "reason": f"no node type '{n.get('entity_type')}'", "ref": str(n)})
            continue
        key = n.get("key")
        if key in (None, ""):
            skipped.append({"kind": "missing_key", "reason": "no key value", "ref": str(n)})
            continue
        props = _filter_props(t.attributes, n.get("properties", {}), skipped, str(n))
        clean_nodes.append({"entity_type": t.name, "key": key, "properties": props})
        node_keys.add(key)

    clean_edges: list[dict[str, Any]] = []
    for e in proposal.get("edges", []):
        et = model.edge_type(e.get("edge_type", ""))
        if not et:
            skipped.append({"kind": "unknown_type", "reason": f"no edge type '{e.get('edge_type')}'", "ref": str(e)})
            continue
        src = next((n for n in clean_nodes if n["key"] == e.get("source_key")), None)
        tgt = next((n for n in clean_nodes if n["key"] == e.get("target_key")), None)
        if not src or not tgt:
            skipped.append({"kind": "dangling_ref", "reason": "edge endpoint not in batch", "ref": str(e)})
            continue
        props = _filter_props(et.attributes, e.get("properties", {}), skipped, str(e))
        clean_edges.append(
            {
                "edge_type": et.name,
                "source_type": src["entity_type"],
                "target_type": tgt["entity_type"],
                "source_key": e["source_key"],
                "target_key": e["target_key"],
                "properties": props,
            }
        )

    return {"nodes": clean_nodes, "edges": clean_edges, "skipped": skipped}
