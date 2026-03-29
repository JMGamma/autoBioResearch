# AutoBioResearch — Project Guide

## Running the project

**API (FastAPI):**
```
uv run autobioresearch/api/main.py
```
Serves the React frontend at `/` and API at `/api/`.

**Frontend (dev mode):**
```
cd frontend && npm run dev
```

**Crawler (data pipeline):**
```
uv run autobioresearch/main.py
```

---

## Architecture

### Two-process design

```
Process 1: Crawler loop
  PubMed / S2 → LLM extractor → autobioresearch.db (SQLite, WAL write)

Process 2: FastAPI
  Read-only: autobioresearch.db (SQLite, WAL read)
  Read-write: explorer_cache.db (perturbation cache, separate file — no lock contention)
```

The crawler and API each have their own SQLite file. WAL mode on both files allows concurrent reads without blocking writes. This design is intentional — do not merge them into one file.

### Why SQLite (not PostgreSQL)

SQLite in WAL mode is sufficient until the graph grows enough to push `/perturb` query times above 2s. It requires zero infrastructure, survives the Fly.io volume mount, and lets the crawler run anywhere without a DB server.

**PostgreSQL migration trigger:** When `/perturb` depth=3 p99 exceeds 2 seconds consistently for 7 days, run the migration. Check by running `uv run scripts/benchmark_queries.py` weekly. The script prints a `MIGRATION SIGNAL` line if the threshold is crossed.

**PostgreSQL migration checklist** (when triggered):
1. Set up Alembic: `uv add alembic && alembic init alembic`
2. Generate migration from current SQLAlchemy models: `alembic revision --autogenerate`
3. Provision PostgreSQL (Fly.io Postgres, Supabase, or self-hosted)
4. Dump SQLite: `sqlite3 autobioresearch.db .dump > dump.sql`
5. Import to Postgres and verify FK integrity
6. Run API against Postgres for 2 weeks while keeping SQLite as a read-replica
7. Only decommission SQLite after Postgres is stable with no regressions

### Why Cytoscape.js (not D3)

Cytoscape.js was chosen in Phase 2 over D3 for three reasons:
1. `fcose` and `dagre` plugins provide production-quality force-directed and DAG layouts — no physics simulation to write from scratch.
2. Cytoscape has native node/edge/stylesheet abstractions built for networks; D3 requires building all of that on top of generic SVG.
3. Cleaner React integration — Cytoscape mounts into a `div` via `useRef` and lives outside React's render cycle; D3 and React fight over DOM ownership.

The "frame-level control" justification for D3 didn't materialise — perturbation overlay uses `cy.$id(nodeId).data(...)` imperatively.

---

## Operational scripts

### Apply DB indexes (one-time, safe to re-run)
```
uv run scripts/add_indexes.py
```
Creates 6 indexes on the hot query paths (synonym search, subgraph expansion, evidence lookups). Safe to re-run — uses `IF NOT EXISTS`.

### Warm the perturbation cache
```
uv run scripts/warm_cache.py          # top 50 entities, depth=3
uv run scripts/warm_cache.py --top 100 --depth 4
```
Precomputes suppress + promote results for the top-N entities and writes them to `explorer_cache.db`. Run after a large batch of new papers is ingested or after a fresh deploy.

### Benchmark query performance
```
uv run scripts/benchmark_queries.py
uv run scripts/benchmark_queries.py --iterations 20
```
Measures p50/p95/p99 for entity search, entity detail, subgraph (1/2/3-hop), and perturbation (depth=2/3) directly against the SQLite database. Reports pass/fail against latency targets and prints a `MIGRATION SIGNAL` if the PostgreSQL migration threshold is crossed.

---

## Deployment (Fly.io)

First deploy:
```
fly launch --no-deploy
fly secrets set ANTHROPIC_API_KEY=<your-key>
fly volumes create autobio_data --size 10
fly deploy
```

Subsequent deploys:
```
fly deploy
```

The `autobio_data` volume persists `autobioresearch.db` and `explorer_cache.db` across deploys. After deploying, warm the cache:
```
fly ssh console -C "uv run scripts/warm_cache.py --top 50"
```

---

## Key config fields (`config.yaml`)

| Field | Default | Notes |
|-------|---------|-------|
| `db_path` | `./autobioresearch.db` | Research DB (crawler writes, API reads) |
| `cache_db_path` | `./explorer_cache.db` | Perturbation cache (API writes) |
| `perturbation_combination_exponent` | `0.55` | α in multi-path score formula. Lower = first strong path dominates. |
| `perturbation_max_depth` | `4` | API enforces this via Pydantic `le=4`. |

---

## Schema versioning

The perturbation API response includes `schema_version: "1.0"`. Increment this in `autobioresearch/api/schemas.py` and `autobioresearch/api/routes/perturbation.py` when the response shape changes in a breaking way.
