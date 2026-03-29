"""
Performance benchmark for all core API query patterns.

Runs each pattern against the real database and reports p50/p95/p99 latency.
Use this to track performance over time and trigger the PostgreSQL migration
when /perturb depth=3 p99 exceeds 2s consistently for 7 days.

Usage:
    uv run scripts/benchmark_queries.py
    uv run scripts/benchmark_queries.py --db-path ./custom.db
    uv run scripts/benchmark_queries.py --iterations 20

Targets (from abstract-wandering-gadget.md Phase 6):
    search          p99 < 100ms
    entity detail   p99 < 500ms
    subgraph 1-hop  p99 < 1s
    subgraph 2-hop  p99 < 2s
    subgraph 3-hop  p99 < 4s
    perturb depth=2 p99 < 1s
    perturb depth=3 p99 < 3s   ← PostgreSQL trigger if > 2s for 7 days
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

import yaml
from sqlalchemy import bindparam, text

sys.path.insert(0, str(Path(__file__).parent.parent))

from autobioresearch.database import init_engine
from autobioresearch.perturbation.propagator import run_propagation

# ── Config ─────────────────────────────────────────────────────────────────────

def _load_config(db_path_override: str | None, cache_db_override: str | None) -> dict:
    yaml_path = Path("config.yaml")
    if not yaml_path.exists():
        yaml_path = Path(__file__).parent.parent / "config.yaml"
    data: dict = {}
    if yaml_path.exists():
        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if "db_path" in data:
            data["db_path"] = str((yaml_path.parent / data["db_path"]).resolve())
        if "cache_db_path" in data:
            data["cache_db_path"] = str((yaml_path.parent / data["cache_db_path"]).resolve())
    return {
        "db_path": db_path_override or data.get("db_path", "./autobioresearch.db"),
        "cache_db_path": cache_db_override or data.get("cache_db_path", "./explorer_cache.db"),
        "combination_exponent": data.get("perturbation_combination_exponent", 0.55),
    }


# ── Queries ────────────────────────────────────────────────────────────────────

_SEARCH_SQL = text("""
    SELECT DISTINCT e.id, e.canonical_name, e.display_name, e.entity_type, e.organism,
                    e.paper_count, es.synonym AS matching_synonym
    FROM entity_synonyms es
    JOIN entities e ON es.entity_id = e.id
    WHERE LOWER(es.synonym) LIKE :q
    ORDER BY e.paper_count DESC
    LIMIT 20
""")

_ENTITY_DETAIL_SQL = text("""
    SELECT e.id, e.canonical_name, e.display_name, e.entity_type, e.organism, e.paper_count,
           (SELECT COUNT(*) FROM interactions i WHERE i.entity_a_id = e.id OR i.entity_b_id = e.id) AS degree_total,
           (SELECT COUNT(*) FROM interactions i WHERE i.entity_b_id = e.id) AS degree_in,
           (SELECT COUNT(*) FROM interactions i WHERE i.entity_a_id = e.id) AS degree_out,
           (SELECT COUNT(*) FROM evidence ev
            JOIN interactions i ON ev.interaction_id = i.id
            WHERE i.entity_a_id = e.id OR i.entity_b_id = e.id) AS evidence_total,
           e.has_conflicts
    FROM entities e
    WHERE e.id = :entity_id
""")

_SUBGRAPH_SQL = text("""
    SELECT i.id, i.entity_a_id, i.entity_b_id, i.composite_confidence_score
    FROM interactions i
    WHERE (i.entity_a_id IN :ids OR i.entity_b_id IN :ids)
      AND COALESCE(i.composite_confidence_score, 0.0) >= 0.0
    ORDER BY composite_confidence_score DESC
    LIMIT 200
""").bindparams(bindparam("ids", expanding=True))

_TOP_ENTITIES_SQL = text("""
    SELECT e.id, e.canonical_name,
           COUNT(*) AS degree
    FROM entities e
    JOIN interactions i ON e.id = i.entity_a_id OR e.id = i.entity_b_id
    GROUP BY e.id
    ORDER BY degree DESC
    LIMIT :n
""")


# ── Timing ─────────────────────────────────────────────────────────────────────

def _time_fn(fn) -> float:
    """Return elapsed time in milliseconds."""
    t0 = time.perf_counter()
    fn()
    return (time.perf_counter() - t0) * 1000


def _percentile(data: list[float], p: int) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * p / 100)
    idx = min(idx, len(sorted_data) - 1)
    return sorted_data[idx]


def _report(label: str, times_ms: list[float], target_ms: float) -> bool:
    p50 = _percentile(times_ms, 50)
    p95 = _percentile(times_ms, 95)
    p99 = _percentile(times_ms, 99)
    passed = p99 <= target_ms
    status = "PASS" if passed else "FAIL"
    print(f"  {status:4s}  {label:<28s}  p50={p50:6.0f}ms  p95={p95:6.0f}ms  p99={p99:6.0f}ms  (target <{target_ms:.0f}ms)")
    return passed


# ── Main ───────────────────────────────────────────────────────────────────────

def run_benchmark(db_path: str, n_iterations: int, exponent: float) -> bool:
    db = Path(db_path)
    if not db.exists():
        print(f"Error: database not found at {db.resolve()}")
        print("Run the crawler or init_db.py first.")
        return False

    engine = init_engine(str(db))
    all_passed = True

    with engine.connect() as conn:
        # Get top-5 entities as representative test subjects
        rows = conn.execute(_TOP_ENTITIES_SQL, {"n": 5}).mappings().all()
        if not rows:
            print("Error: no entities found in database — nothing to benchmark.")
            return False

        top_entities = [(r["id"], r["canonical_name"], r["degree"]) for r in rows]
        entity_ids = [e[0] for e in top_entities]
        entity_names = [e[1] for e in top_entities]
        print(f"\nBenchmarking against {db.resolve()}")
        print(f"Test subjects: {', '.join(entity_names[:3])}{'...' if len(entity_names) > 3 else ''}")
        print(f"Iterations: {n_iterations}\n")

        # 1. Entity search
        search_times: list[float] = []
        for name in entity_names[:3]:
            prefix = name[:4].lower()
            for _ in range(n_iterations):
                search_times.append(_time_fn(
                    lambda p=prefix: conn.execute(_SEARCH_SQL, {"q": f"{p}%"}).fetchall()
                ))
        all_passed &= _report("entity search", search_times, target_ms=100)

        # 2. Entity detail
        detail_times: list[float] = []
        for eid in entity_ids[:3]:
            for _ in range(n_iterations):
                detail_times.append(_time_fn(
                    lambda e=eid: conn.execute(_ENTITY_DETAIL_SQL, {"entity_id": e}).fetchone()
                ))
        all_passed &= _report("entity detail", detail_times, target_ms=500)

        # 3. Subgraph hops 1/2/3
        for hops, target_ms in [(1, 1000), (2, 2000), (3, 4000)]:
            sg_times: list[float] = []
            seed = entity_ids[:1]
            for _ in range(n_iterations // 2 or 1):
                frontier = set(seed)
                visited = set(seed)
                t0 = time.perf_counter()
                for _ in range(hops):
                    if not frontier:
                        break
                    rows_sg = conn.execute(_SUBGRAPH_SQL, {"ids": tuple(frontier), "min_score": 0.0}).fetchall()
                    new_frontier: set[str] = set()
                    for r in rows_sg:
                        for nid in (r[1], r[2]):
                            if nid not in visited:
                                new_frontier.add(nid)
                                visited.add(nid)
                    frontier = new_frontier
                sg_times.append((time.perf_counter() - t0) * 1000)
            all_passed &= _report(f"subgraph {hops}-hop", sg_times, target_ms=target_ms)

        # 4. Perturbation depth=2 and depth=3
        seed_id, seed_name = entity_ids[0], entity_names[0]
        for depth, target_ms in [(2, 1000), (3, 3000)]:
            p_times: list[float] = []
            iters = max(n_iterations // 3, 3)
            for _ in range(iters):
                p_times.append(_time_fn(
                    lambda d=depth: run_propagation(
                        conn=conn,
                        seed_entity_id=seed_id,
                        seed_name=seed_name,
                        mode="suppress",
                        depth=d,
                        exponent=exponent,
                    )
                ))
            all_passed &= _report(f"perturbation depth={depth}", p_times, target_ms=target_ms)

    print()
    if all_passed:
        print("All targets met. No action required.")
    else:
        print("One or more targets missed.")

    # PostgreSQL migration signal
    perturb_3_times: list[float] = []
    with engine.connect() as conn:
        seed_id, seed_name = entity_ids[0], entity_names[0]
        for _ in range(5):
            perturb_3_times.append(_time_fn(
                lambda: run_propagation(conn, seed_id, seed_name, "suppress", 3, exponent)
            ))
    p99_perturb_3 = _percentile(perturb_3_times, 99)
    if p99_perturb_3 > 2000:
        print(
            f"\n  MIGRATION SIGNAL: /perturb depth=3 p99={p99_perturb_3:.0f}ms > 2000ms.\n"
            "  If this persists for 7 days, execute the PostgreSQL migration (see CLAUDE.md)."
        )

    return all_passed


def main():
    parser = argparse.ArgumentParser(description="Benchmark AutoBioResearch query performance")
    parser.add_argument("--db-path", help="Path to SQLite database (default: from config.yaml)")
    parser.add_argument("--iterations", type=int, default=10, help="Iterations per pattern (default: 10)")
    args = parser.parse_args()

    cfg = _load_config(args.db_path, None)
    ok = run_benchmark(cfg["db_path"], args.iterations, cfg["combination_exponent"])
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
