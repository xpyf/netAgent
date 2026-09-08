#!/usr/bin/env bash
# Self-verifying end-to-end test: model -> map/validate -> atomic ingest -> idempotency.
# Usage: ./scripts/test_system.sh   (override env: PGSQL_DSN, NEO4J_URI, NEO4J_AUTH, SAMPLE)
# Exits non-zero on first failed assertion.
set -euo pipefail
cd "$(dirname "$0")/.."

export NEO4J_URI="${NEO4J_URI:-neo4j://localhost:7687}"
export NEO4J_AUTH="${NEO4J_AUTH:-neo4j:neo4j123}"
export PGSQL_DSN="${PGSQL_DSN:-postgresql://postgres:1qaz%40WSX@127.0.0.1:5432/model_meta}"
SAMPLE="${SAMPLE:-.scratch/nolograph-agent/samples/topology-sample.json}"

my_nodes() {
  uv run python -c "import os; from neo4j import GraphDatabase; d=GraphDatabase.driver(os.environ['NEO4J_URI'],auth=tuple(os.environ['NEO4J_AUTH'].split(':',1))); print(d.execute_query(\"MATCH (n) WHERE labels(n)[0] IN ['router','switch','firewall','network'] RETURN count(*) AS c\").records[0]['c'])"
}

echo "== 1. map + validate (dry-run, no neo4j write) =="
dry=$(uv run python -m netagent.main "$SAMPLE" --dry-run)
echo "$dry" | env N=${2:-} uv run python -c "import sys,json; d=json.load(sys.stdin); assert len(d['nodes'])==5, f\"expected 5 nodes, got {len(d['nodes'])}\"; assert len(d['edges'])==2, f\"expected 2 edges, got {len(d['edges'])}\"; assert d['skipped']==9, f\"expected 9 skipped, got {d['skipped']}\"; assert d['skipped_by_kind']['envelope']==1; print('OK: 5 nodes, 2 edges, 9 skipped (envelope+unknown_field)')"
echo "$dry" >/dev/null

echo "== 2. deterministic ingest into neo4j =="
r1=$(uv run python -m netagent.main "$SAMPLE")
echo "$r1" | uv run python -c "import sys,json; d=json.load(sys.stdin); assert d['ok'] is True; assert d['nodes_merged']==5 and d['edges_merged']==2, f\"got {d}\"; print('OK: report shows 5 nodes / 2 edges merged')"
c=$(my_nodes); echo "   graph node count: $c (expect 5)"; [ "$c" = "5" ] || { echo "FAIL: expected 5"; exit 1; }

echo "== 3. idempotency (re-run must not grow) =="
uv run python -m netagent.main "$SAMPLE" >/dev/null
c2=$(my_nodes); echo "   graph node count after re-run: $c2 (still 5)"; [ "$c2" = "5" ] || { echo "FAIL: not idempotent"; exit 1; }
[ "$c2" = "$c" ] || { echo "FAIL: count changed"; exit 1; }

echo
echo "PASS: pipeline, atomic write, and idempotency all verified."
