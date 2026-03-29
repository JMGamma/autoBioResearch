"""
One-time index migration script. Safe to re-run — all statements use IF NOT EXISTS.

Adds indexes on the columns that receive the most query traffic:
  - entity_synonyms.synonym  (every search and synonym lookup)
  - interactions.entity_a_id / entity_b_id  (subgraph expansion hot path)
  - evidence.interaction_id  (evidence drawer lookups)
  - conflicts.status         (open-conflict filtering)
  - entities.canonical_name  (LIKE prefix search)

Usage:
    uv run scripts/add_indexes.py
    uv run scripts/add_indexes.py --db-path ./custom.db
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from autobioresearch.database import init_engine

_INDEXES = [
    (
        "idx_entity_synonyms_synonym",
        "CREATE INDEX IF NOT EXISTS idx_entity_synonyms_synonym "
        "ON entity_synonyms (LOWER(synonym));",
    ),
    (
        "idx_interactions_entity_a",
        "CREATE INDEX IF NOT EXISTS idx_interactions_entity_a "
        "ON interactions (entity_a_id);",
    ),
    (
        "idx_interactions_entity_b",
        "CREATE INDEX IF NOT EXISTS idx_interactions_entity_b "
        "ON interactions (entity_b_id);",
    ),
    (
        "idx_evidence_interaction",
        "CREATE INDEX IF NOT EXISTS idx_evidence_interaction "
        "ON evidence (interaction_id);",
    ),
    (
        "idx_conflicts_status",
        "CREATE INDEX IF NOT EXISTS idx_conflicts_status "
        "ON conflicts (status);",
    ),
    (
        "idx_entities_canonical_name",
        "CREATE INDEX IF NOT EXISTS idx_entities_canonical_name "
        "ON entities (canonical_name COLLATE NOCASE);",
    ),
]


def main():
    parser = argparse.ArgumentParser(description="Add performance indexes to the AutoBioResearch database")
    parser.add_argument("--db-path", default="./autobioresearch.db", help="Path to SQLite database file")
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"Error: database not found at {db_path.resolve()}")
        print("Run scripts/init_db.py first.")
        sys.exit(1)

    print(f"Adding indexes to: {db_path.resolve()}")

    engine = init_engine(str(db_path))
    with engine.connect() as conn:
        for name, sql in _INDEXES:
            conn.execute(__import__("sqlalchemy").text(sql))
            print(f"  OK  {name}")
        conn.commit()

    print(f"\nDone — {len(_INDEXES)} indexes applied.")


if __name__ == "__main__":
    main()
