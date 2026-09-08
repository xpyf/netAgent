"""Mapping rules A-E (ticket 02): turn a JSON document into proposed nodes/edges.

Deterministic realization of the rules, keyed to the sample topology document.
Output feeds validator.py, which applies rule F (exception filtering).

- NODE_KEYS / EDGE_KEYS: top-level array key -> node/edge type name (fallback when no `kind`).
- KIND_FIELD: object field whose value names a node type (rule A, preferred).
- REF_FIELDS: edge element fields that hold source_key / target_key (rule C).
"""
from __future__ import annotations

from typing import Any

from netagent.model import Model

NODE_KEYS: dict[str, str] = {"devices": "device", "networks": "network"}
EDGE_KEYS: dict[str, str] = {"links": "CONNECTED_TO"}
KIND_FIELD = "kind"
REF_FIELDS: dict[str, list[str]] = {"source": ["a", "from", "source"], "target": ["z", "to", "target"]}

Proposal = dict[str, list[dict[str, Any]]]  # nodes / edges / skipped


def _identify_type(model: Model, obj: dict, fallback: str | None) -> str | None:
    kind = obj.get(KIND_FIELD)
    if isinstance(kind, str) and model.node_type(kind):
        return kind
    if fallback and model.node_type(fallback):
        return fallback
    return None


def _build_node(model: Model, obj: dict, fallback: str | None) -> dict[str, Any] | None:
    t = _identify_type(model, obj, fallback)
    if not t:
        return None
    ntype = model.node_types[t]
    key = obj.get(ntype.key_field)
    if key in (None, ""):
        return None
    return {"entity_type": t, "key": key, "properties": {k: v for k, v in obj.items()}}


def _build_edge(model: Model, el: dict, default_edge_type: str) -> dict[str, Any] | None:
    """Rule C: edge type prefers `el.type` matching an edge_type.name, else the key convention."""
    t = el.get("type")
    use_type_as_discriminator = False
    if isinstance(t, str) and model.edge_type(t) is not None:
        edge_type: str = t
        use_type_as_discriminator = True
    else:
        edge_type = default_edge_type
    if not model.edge_type(edge_type):
        return None
    src_field = next((f for f in REF_FIELDS["source"] if f in el), None)
    tgt_field = next((f for f in REF_FIELDS["target"] if f in el), None)
    if not src_field or not tgt_field:
        return None
    props = {
        k: v
        for k, v in el.items()
        if k not in (src_field, tgt_field) and not (use_type_as_discriminator and k == "type")
    }
    return {
        "edge_type": edge_type,
        "source_type": None,
        "target_type": None,
        "source_key": el[src_field],
        "target_key": el[tgt_field],
        "properties": props,
    }


def map_document(model: Model, doc: Any, *, node_keys=None, edge_keys=None) -> Proposal:
    """Apply rules A-E to a JSON document, returning proposed nodes/edges + skipped log."""
    node_keys = node_keys or NODE_KEYS
    edge_keys = edge_keys or EDGE_KEYS
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    if not isinstance(doc, dict):
        skipped.append({"kind": "untyped_object", "reason": "document root is not an object", "ref": ""})
        return {"nodes": nodes, "edges": edges, "skipped": skipped}

    for key, value in doc.items():
        if key in edge_keys:
            if isinstance(value, list):
                for el in value:
                    if not isinstance(el, dict):
                        skipped.append({"kind": "untyped_object", "reason": "edge element not an object", "ref": str(el)})
                        continue
                    edge = _build_edge(model, el, edge_keys[key])
                    if edge:
                        edges.append(edge)
                    else:
                        skipped.append({"kind": "unresolvable", "reason": "edge missing source/target ref", "ref": str(el)})
            continue

        if isinstance(value, list):
            fallback = node_keys.get(key) or (key[:-1] if key.endswith("s") else key)
            for el in value:
                if not isinstance(el, dict):
                    skipped.append({"kind": "untyped_object", "reason": "array element not an object", "ref": str(el)})
                    continue
                if (node := _build_node(model, el, fallback)):
                    nodes.append(node)
                else:
                    skipped.append({"kind": "unidentifiable", "reason": "no node type or missing key", "ref": str(el)})
        elif isinstance(value, dict) and (key in node_keys or isinstance(value.get(KIND_FIELD), str)):
            # a single object that declares a type, or an explicit node collection key
            node = _build_node(model, value, node_keys.get(key))
            if node:
                nodes.append(node)
            else:
                skipped.append({"kind": "unidentifiable", "reason": "no node type or missing key", "ref": str(value)})
        else:
            skipped.append({"kind": "envelope", "reason": f"top-level wrapper or scalar '{key}' not mapped (rule E)", "ref": ""})
    return {"nodes": nodes, "edges": edges, "skipped": skipped}
