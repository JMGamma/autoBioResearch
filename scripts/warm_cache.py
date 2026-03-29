"""
Perturbation cache warmer. Precomputes suppress + promote results for the
top-N entities by interaction count and stores them in explorer_cache.db.

Cold-start cache warm takes a few minutes depending on graph size.
Run this after deploying or after the database is updated with a large batch
of new papers. The API will serve cached results instantly for common entities.

Usage:
    uv run scripts/warm_cache.py
    uv run scripts/warm_cache.py --top 100
    uv run scripts/warm_cache.py --depth 4
    uv run scripts/warm_cache.py --db-path ./custom.db

Skips entities that already have a fresh cache entry (expires_at in future).
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).parent.parent))

from autobioresearch.database import init_engine
from autobioresearch.perturbation.cache import PerturbationCache
from autobioresearch.perturbation.propagator import run_propagation

_TOP_ENTITIES_SQL = text("""
    SELECT e.id, e.canonical_name,
           COUNT(*) AS degree
    FROM entities e
    JOIN interactions i ON e.id = i.entity_a_id OR e.id = i.entity_b_id
    GROUP BY e.id
    ORDER BY degree DESC
    LIMIT :n
""")


def _load_config(db_override: str | None, cache_override: str | None) -> dict:
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
        "db_path": db_override or data.get("db_path", "./autobioresearch.db"),
        "cache_db_path": cache_override or data.get("cache_db_path", "./explorer_cache.db"),
        "combination_exponent": data.get("perturbation_combination_exponent", 0.55),
    }


def _is_cache_fresh(cache: PerturbationCache, entity_id: str, mode: str, depth: int) -> bool:
    """Return True if a valid cache entry exists for (entity_id, mode) at >= depth."""
    row = cache._lookup(entity_id, mode)
    if row is None:
        return False
    if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
        return False
    return row["max_depth"] >= depth


def warm_cache(db_path: str, cache_db_path: str, top_n: int, depth: int, exponent: float) -> None:
    db = Path(db_path)
    if not db.exists():
        print(f"Error: database not found at {db.resolve()}")
        sys.exit(1)

    engine = init_engine(str(db))
    cache = PerturbationCache(cache_db_path)

    with engine.connect() as conn:
        rows = conn.execute(_TOP_ENTITIES_SQL, {"n": top_n}).mappings().all()
        entities = [(r["id"], r["canonical_name"], r["degree"]) for r in rows]

    if not entities:
        print("No entities found in database.")
        return

    modes = ["suppress", "promote"]
    total = len(entities) * len(modes)
    done = 0
    skipped = 0

    print(f"Warming cache for top {len(entities)} entities × {len(modes)} modes = {total} runs")
    print(f"  DB:    {db.resolve()}")
    print(f"  Cache: {Path(cache_db_path).resolve()}")
    print(f"  Depth: {depth}\n")

    with engine.connect() as conn:
        for entity_id, name, degree in entities:
            for mode in modes:
                done += 1
                prefix = f"[{done:>{len(str(total))}}/{total}]"

                if _is_cache_fresh(cache, entity_id, mode, depth):
                    print(f"  {prefix}  {name} ({mode})  —  skipped (fresh cache)")
                    skipped += 1
                    continue

                t0 = time.perf_counter()
                try:
                    result = run_propagation(
                        conn=conn,
                        seed_entity_id=entity_id,
                        seed_name=name,
                        mode=mode,
                        depth=depth,
                        exponent=exponent,
                    )
                    elapsed_ms = round((time.perf_counter() - t0) * 1000)
                    n_affected = result["stats"]["n_affected"]
                    cache.write(entity_id, mode, depth, result)
                    print(f"  {prefix}  {name} ({mode})  —  {n_affected} affected  ({elapsed_ms}ms)")
                except Exception as exc:
                    elapsed_ms = round((time.perf_counter() - t0) * 1000)
                    print(f"  {prefix}  {name} ({mode})  —  ERROR: {exc}  ({elapsed_ms}ms)")

    computed = total - skipped
    print(f"\nDone. {computed} computed, {skipped} skipped (already fresh).")


def main():
    parser = argparse.ArgumentParser(description="Warm the AutoBioResearch perturbation cache")
    parser.add_argument("--top", type=int, default=50, metavar="N",
                        help="Number of top entities to warm (default: 50)")
    parser.add_argument("--depth", type=int, default=3, choices=[1, 2, 3, 4],
                        help="Propagation depth to precompute (default: 3)")
    parser.add_argument("--db-path", help="Path to research SQLite database (default: from config.yaml)")
    parser.add_argument("--cache-path", help="Path to cache SQLite database (default: from config.yaml)")
    args = parser.parse_args()

    cfg = _load_config(args.db_path, args.cache_path)
    warm_cache(
        db_path=cfg["db_path"],
        cache_db_path=cfg["cache_db_path"],
        top_n=args.top,
        depth=args.depth,
        exponent=cfg["combination_exponent"],
    )


if __name__ == "__main__":
    main()
