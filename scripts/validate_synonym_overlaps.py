"""
Audit ambiguous synonym overlaps in the AutoBioResearch database.

Reports synonyms that currently map to more than one entity, with optional
detail rows to help review whether those overlaps are expected or suspicious.

Usage:
    uv run scripts/validate_synonym_overlaps.py
    uv run scripts/validate_synonym_overlaps.py --db-path ./autobioresearch.db --limit 100
    uv run scripts/validate_synonym_overlaps.py --show-rows --limit 20
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text

from autobioresearch.database import init_engine


SUMMARY_SQL = text(
    """
    SELECT
        es.synonym,
        COUNT(DISTINCT es.entity_id) AS entity_count,
        GROUP_CONCAT(DISTINCT e.entity_type) AS entity_types,
        GROUP_CONCAT(DISTINCT COALESCE(e.organism, '(none)')) AS organisms,
        SUM(COALESCE(e.paper_count, 0)) AS total_paper_count
    FROM entity_synonyms es
    JOIN entities e ON e.id = es.entity_id
    GROUP BY es.synonym
    HAVING COUNT(DISTINCT es.entity_id) > 1
    ORDER BY entity_count DESC, total_paper_count DESC, es.synonym ASC
    LIMIT :limit
    """
)

DETAIL_SQL = text(
    """
    SELECT
        es.synonym,
        es.entity_id,
        e.canonical_name,
        e.entity_type,
        e.organism,
        e.paper_count,
        es.source
    FROM entity_synonyms es
    JOIN entities e ON e.id = es.entity_id
    WHERE es.synonym = :synonym
    ORDER BY e.paper_count DESC, e.entity_type ASC, e.canonical_name ASC
    """
)

TOTAL_SQL = text(
    """
    SELECT COUNT(*) FROM (
        SELECT es.synonym
        FROM entity_synonyms es
        GROUP BY es.synonym
        HAVING COUNT(DISTINCT es.entity_id) > 1
    )
    """
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit ambiguous synonym overlaps")
    parser.add_argument("--db-path", default="./autobioresearch.db", help="Path to SQLite database file")
    parser.add_argument("--limit", type=int, default=50, help="Max overlap groups to print")
    parser.add_argument("--show-rows", action="store_true", help="Show all entity rows for each overlapping synonym")
    args = parser.parse_args()

    engine = init_engine(args.db_path)

    with engine.connect() as conn:
        total = conn.execute(TOTAL_SQL).scalar_one()
        rows = conn.execute(SUMMARY_SQL, {"limit": args.limit}).mappings().all()

        print(f"Database: {Path(args.db_path).resolve()}")
        print(f"Overlapping synonyms found: {total}")
        print(f"Showing up to: {args.limit}")

        if not rows:
            print("\nNo overlapping synonyms found.")
            return

        print("\nTop overlaps:")
        for row in rows:
            print(
                f"  {row['synonym']:<30} entities={row['entity_count']:<3} "
                f"types=[{row['entity_types']}] organisms=[{row['organisms']}] "
                f"papers={row['total_paper_count'] or 0}"
            )
            if args.show_rows:
                detail_rows = conn.execute(DETAIL_SQL, {"synonym": row["synonym"]}).mappings().all()
                for detail in detail_rows:
                    organism = detail["organism"] or "(none)"
                    print(
                        "    - "
                        f"{detail['entity_id'][:8]}  {detail['canonical_name']}  "
                        f"type={detail['entity_type']} organism={organism} "
                        f"papers={detail['paper_count']} source={detail['source']}"
                    )


if __name__ == "__main__":
    main()
