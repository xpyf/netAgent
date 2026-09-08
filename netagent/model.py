"""Knowledge-graph model definition, loaded from the pgsql metadata tables.

`load_model(dsn)` aggregates the four metadata tables into a structured Model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import psycopg2
from psycopg2 import sql


@dataclass
class Attribute:
    attr_type: str
    optional: bool = True
    cardinality: str = "scalar"


@dataclass
class NodeType:
    name: str
    key_field: str
    attributes: dict[str, Attribute] = field(default_factory=dict)


@dataclass
class EdgeType:
    name: str
    source_type: str
    target_type: str
    attributes: dict[str, Attribute] = field(default_factory=dict)


@dataclass
class Model:
    node_types: dict[str, NodeType] = field(default_factory=dict)
    edge_types: dict[str, EdgeType] = field(default_factory=dict)

    def node_type(self, name: str) -> NodeType | None:
        return self.node_types.get(name)

    def edge_type(self, name: str) -> EdgeType | None:
        return self.edge_types.get(name)


def _attrs(cur, id_col_literal: str, table_literal: str) -> dict[str, dict[str, Attribute]]:
    if table_literal == "node_attribute":
        cur.execute("SELECT type_id, attr_name, attr_type, optional, cardinality FROM node_attribute")
    else:
        cur.execute("SELECT edge_id, attr_name, attr_type, optional, cardinality FROM edge_attribute")
    out: dict[str, dict[str, Attribute]] = {}
    for row in cur.fetchall():
        out.setdefault(row[0], {})[row[1]] = Attribute(row[2], row[3], row[4])
    return out


def load_model(dsn: str) -> Model:
    """Read the four metadata tables and assemble a Model."""
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT type_id, name, key_field FROM node_type")
            node_meta = {row[0]: (row[1], row[2]) for row in cur.fetchall()}
            node_attrs = _attrs(cur, "type_id", "node_attribute")
            cur.execute("SELECT edge_id, name, source_type, target_type FROM edge_type")
            edge_meta = {row[0]: (row[1], row[2], row[3]) for row in cur.fetchall()}
            edge_attrs = _attrs(cur, "edge_id", "edge_attribute")
    finally:
        conn.close()

    model = Model()
    for tid, (name, key_field) in node_meta.items():
        model.node_types[name] = NodeType(name, key_field, node_attrs.get(tid, {}))
    for eid, (name, st, tt) in edge_meta.items():
        model.edge_types[name] = EdgeType(name, st, tt, edge_attrs.get(eid, {}))
    return model
