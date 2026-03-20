"""
One-time database initialization script.
Run this before starting the autoresearch loop for the first time.

Usage:
    uv run scripts/init_db.py
    uv run scripts/init_db.py --db-path ./custom.db
"""
import argparse
import sys
from pathlib import Path

# Ensure the package root is on the path when run directly
sys.path.insert(0, str(Path(__file__).parent.parent))

from autobioresearch.database import create_all, init_engine


def main():
    parser = argparse.ArgumentParser(description="Initialize AutoBioResearch database")
    parser.add_argument("--db-path", default="./autobioresearch.db", help="Path to SQLite database file")
    args = parser.parse_args()

    db_path = Path(args.db_path)
    print(f"Initializing database at: {db_path.resolve()}")

    engine = init_engine(str(db_path))
    create_all(engine)

    print("Database initialized successfully.")
    print("Tables created:")
    from sqlalchemy import inspect as sa_inspect
    inspector = sa_inspect(engine)
    for table_name in inspector.get_table_names():
        cols = [c["name"] for c in inspector.get_columns(table_name)]
        print(f"  {table_name}: {len(cols)} columns")


if __name__ == "__main__":
    main()
