"""
Reset the AutoBioResearch database for testing.

Two modes:
  --soft  (default) Truncates all data rows but keeps the schema in place.
          Fastest; ready to run again immediately.
  --hard  Deletes the .db file entirely and recreates it from scratch.
          Use when you also want to wipe any SQLite metadata / page bloat.

Usage:
    uv run scripts/reset_db.py                        # soft reset, default db path
    uv run scripts/reset_db.py --hard                 # hard reset
    uv run scripts/reset_db.py --db-path ./test.db    # custom path
    uv run scripts/reset_db.py --yes                  # skip confirmation prompt
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


# Truncation order matters — child tables before parents to satisfy FK constraints.
_TRUNCATION_ORDER = [
    "metrics_log",
    "search_queries",
    "conflicts",
    "evidence",
    "interactions",
    "entity_synonyms",
    "entities",
    "papers",
]


def confirm(prompt: str) -> bool:
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("y", "yes")


def soft_reset(db_path: Path, skip_confirm: bool) -> None:
    if not db_path.exists():
        print(f"Database not found at {db_path.resolve()} — nothing to reset.")
        sys.exit(1)

    if not skip_confirm:
        if not confirm(f"Soft-reset {db_path.resolve()}? (ALL DATA will be deleted, schema kept)"):
            print("Aborted.")
            sys.exit(0)

    from sqlalchemy import text
    from autobioresearch.database import init_engine

    engine = init_engine(str(db_path))
    deleted: dict[str, int] = {}

    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys = OFF"))
        for table in _TRUNCATION_ORDER:
            result = conn.execute(text(f"DELETE FROM {table}"))
            deleted[table] = result.rowcount
        conn.execute(text("PRAGMA foreign_keys = ON"))
        conn.execute(text("VACUUM"))

    print(f"Soft reset complete — {db_path.resolve()}")
    print("Rows deleted:")
    for table, n in deleted.items():
        print(f"  {table:<20} {n:>6} rows")


def hard_reset(db_path: Path, skip_confirm: bool) -> None:
    if not skip_confirm:
        msg = (
            f"Hard-reset {db_path.resolve()}? "
            "(DB FILE will be DELETED and recreated from scratch)"
        )
        if not confirm(msg):
            print("Aborted.")
            sys.exit(0)

    # Remove the DB file and any WAL / SHM sidecars
    for suffix in ("", "-shm", "-wal"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()
            print(f"Deleted {p}")

    from autobioresearch.database import create_all, init_engine

    engine = init_engine(str(db_path))
    create_all(engine)
    print(f"Hard reset complete — fresh database at {db_path.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reset the AutoBioResearch database (soft or hard)."
    )
    parser.add_argument(
        "--db-path",
        default="./autobioresearch.db",
        help="Path to SQLite database file (default: ./autobioresearch.db)",
    )
    parser.add_argument(
        "--hard",
        action="store_true",
        help="Delete the DB file entirely and recreate it (default: soft truncate only)",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip confirmation prompt",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path)

    if args.hard:
        hard_reset(db_path, skip_confirm=args.yes)
    else:
        soft_reset(db_path, skip_confirm=args.yes)


if __name__ == "__main__":
    main()
