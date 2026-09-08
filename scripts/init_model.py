"""Create DB `model_meta`, the four metadata tables, and seed a sample model.

Sample model matches samples/topology-sample.json:
- device key_field = "id", types router/switch/firewall (kind value = name)
- network key_field = "id", type network
- edge CONNECTED_TO (from links[]), attrs a_if/z_if/speed/type
"""
import os
import sys

import psycopg2
from psycopg2 import sql

HOST = os.environ.get("PGHOST", "127.0.0.1")
USER = os.environ.get("PGUSER", "postgres")
PASSWORD = os.environ.get("PGPASSWORD", "1qaz@WSX")
DB = "model_meta"


def _port() -> int:
    raw = os.environ.get("PGPORT")
    try:
        return int(raw) if raw else 5432
    except ValueError:
        return 5432


PORT = _port()

DDL = """
CREATE TABLE IF NOT EXISTS node_type (
    type_id    text PRIMARY KEY,
    name       text NOT NULL UNIQUE,
    key_field  text NOT NULL
);
CREATE TABLE IF NOT EXISTS node_attribute (
    type_id     text NOT NULL REFERENCES node_type(type_id),
    attr_name   text NOT NULL,
    attr_type   text NOT NULL,
    optional    boolean NOT NULL DEFAULT true,
    cardinality text NOT NULL DEFAULT 'scalar',
    PRIMARY KEY (type_id, attr_name)
);
CREATE TABLE IF NOT EXISTS edge_type (
    edge_id      text PRIMARY KEY,
    name         text NOT NULL UNIQUE,
    source_type  text NOT NULL REFERENCES node_type(type_id),
    target_type  text NOT NULL REFERENCES node_type(type_id)
);
CREATE TABLE IF NOT EXISTS edge_attribute (
    edge_id     text NOT NULL REFERENCES edge_type(edge_id),
    attr_name   text NOT NULL,
    attr_type   text NOT NULL,
    optional    boolean NOT NULL DEFAULT true,
    cardinality text NOT NULL DEFAULT 'scalar',
    PRIMARY KEY (edge_id, attr_name)
);
"""

DEVICE_ATTRS = [
    ("hostname", "text", False, "scalar"),
    ("vendor",   "text", True,  "scalar"),
    ("model",    "text", True,  "scalar"),
    ("interfaces", "json", True, "list"),
]


def main() -> None:
    super_conn = psycopg2.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, dbname="postgres")
    super_conn.autocommit = True
    with super_conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (DB,))
        if not cur.fetchone():
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DB)))
    super_conn.close()

    conn = psycopg2.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, dbname=DB)
    with conn.cursor() as cur:
        cur.execute(DDL)
        # node types: id = type_id
        cur.execute("INSERT INTO node_type(type_id,name,key_field) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                    ("device", "device", "id"))
        cur.execute("""INSERT INTO node_type(type_id,name,key_field)
                       SELECT * FROM (VALUES ('router','router','id'),('switch','switch','id'),
                       ('firewall','firewall','id'),('network','network','id')) AS v(t,n,k)
                       WHERE NOT EXISTS (SELECT 1 FROM node_type WHERE type_id=v.t)""")
        for t in ("router", "switch", "firewall"):
            for (attr, atype, opt, card) in DEVICE_ATTRS:
                cur.execute("""INSERT INTO node_attribute(type_id,attr_name,attr_type,optional,cardinality)
                               VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                            (t, attr, atype, opt, card))
        for (attr, atype, opt) in (("name", "text", True), ("cidr", "cidr", True)):
            cur.execute("""INSERT INTO node_attribute(type_id,attr_name,attr_type,optional,cardinality)
                           VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                        ("network", attr, atype, opt, "scalar"))
        cur.execute("""INSERT INTO edge_type(edge_id,name,source_type,target_type) VALUES (%s,%s,%s,%s)
                       ON CONFLICT DO NOTHING""", ("connected_to", "CONNECTED_TO", "router", "switch"))
        for (attr, atype, opt) in (("a_if", "text", True), ("z_if", "text", True),
                                   ("speed", "text", True), ("type", "text", True)):
            cur.execute("""INSERT INTO edge_attribute(edge_id,attr_name,attr_type,optional,cardinality)
                           VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                        ("connected_to", attr, atype, opt, "scalar"))
    conn.commit()
    conn.close()
    print("model_meta ready: 4 tables; types device/router/switch/firewall/network; edge CONNECTED_TO")


if __name__ == "__main__":
    main()
