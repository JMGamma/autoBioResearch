"""
This script validates propagation math. Its output schema is the contract for the Phase 1 API.
Do not add UI logic here.

Usage:
    uv run scripts/perturbation_prototype.py --entity TP53 --mode suppress --depth 3
    uv run scripts/perturbation_prototype.py --entity BRCA2 --mode promote --depth 2 --out results.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from autobioresearch.database import init_engine
from autobioresearch.perturbation.propagator import _DEFAULT_COMBINATION_EXPONENT, run_propagation
from autobioresearch.storage.repositories import EntityRepo

_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


def _load_exponent() -> float:
    """Read perturbation_combination_exponent from config.yaml without instantiating AppConfig."""
    try:
        data = yaml.safe_load(_CONFIG_PATH.read_text()) or {}
        return float(data.get("perturbation_combination_exponent", _DEFAULT_COMBINATION_EXPONENT))
    except Exception:
        return _DEFAULT_COMBINATION_EXPONENT


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Perturbation propagation prototype for AutoBioResearch"
    )
    parser.add_argument("--entity", required=True, help="Entity name or synonym to perturb")
    parser.add_argument(
        "--mode",
        choices=["suppress", "promote"],
        required=True,
        help="Perturbation direction",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=2,
        metavar="DEPTH",
        help="BFS propagation depth (1–4, default 2)",
    )
    parser.add_argument(
        "--db-path",
        default="./autobioresearch.db",
        help="Path to SQLite database (default: ./autobioresearch.db)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output file path (default: stdout)",
    )
    args = parser.parse_args()
    if not (1 <= args.depth <= 4):
        parser.error(f"--depth must be between 1 and 4, got {args.depth}")
    return args


def main() -> None:
    t0 = time.monotonic()
    args = parse_args()

    engine = init_engine(args.db_path)

    with engine.connect() as conn:
        entity_repo = EntityRepo(conn)
        entity_id = entity_repo.find_by_synonym(args.entity)

        if entity_id is None:
            print(
                f"Error: entity '{args.entity}' not found in the database.",
                file=sys.stderr,
            )
            sys.exit(1)

        entity_row = entity_repo.get_by_id(entity_id)
        seed_name: str = entity_row["canonical_name"]

        result = run_propagation(conn, entity_id, seed_name, args.mode, args.depth,
                                 exponent=_load_exponent())

    duration_ms = int((time.monotonic() - t0) * 1000)
    result["stats"]["duration_ms"] = duration_ms

    output = {
        "schema_version": "1.0",
        "query": {
            "entity": args.entity,
            "entity_id": entity_id,
            "mode": args.mode,
            "depth": args.depth,
        },
        "seed": result["seed"],
        "affected": result["affected"],
        "excluded_edges": result["excluded_edges"],
        "stats": result["stats"],
    }

    out_json = json.dumps(output, indent=2)

    if args.out:
        Path(args.out).write_text(out_json, encoding="utf-8")
        print(f"Written to {args.out}", file=sys.stderr)
    else:
        print(out_json)


if __name__ == "__main__":
    main()
