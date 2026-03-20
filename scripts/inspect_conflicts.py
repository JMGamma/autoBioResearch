"""
CLI tool for reviewing the conflict queue.

Usage:
    uv run scripts/inspect_conflicts.py
    uv run scripts/inspect_conflicts.py --status open --type true_conflict --limit 20
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, text

from autobioresearch.config import AppConfig
from autobioresearch.database import init_engine
from autobioresearch import database as db


def main():
    parser = argparse.ArgumentParser(description="Inspect AutoBioResearch conflict queue")
    parser.add_argument("--db-path", default="./autobioresearch.db")
    parser.add_argument("--status", default="open", help="open|resolved|dismissed|investigating|all")
    parser.add_argument("--type", dest="conflict_type", default="all",
                        help="true_conflict|context_dependent|ambiguous|all")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--show-evidence", action="store_true", help="Show evidence snippets")
    args = parser.parse_args()

    engine = init_engine(args.db_path)

    with engine.connect() as conn:
        q = select(db.conflicts).order_by(db.conflicts.c.penalty_weight.desc())

        if args.status != "all":
            q = q.where(db.conflicts.c.status == args.status)
        if args.conflict_type != "all":
            q = q.where(db.conflicts.c.conflict_type == args.conflict_type)

        q = q.limit(args.limit)
        rows = conn.execute(q).mappings().all()

        if not rows:
            print("No conflicts found matching criteria.")
            return

        # Summary counts
        total_open = conn.execute(
            text("SELECT COUNT(*) FROM conflicts WHERE status='open'")
        ).scalar()
        weighted_sum = conn.execute(
            text("SELECT SUM(penalty_weight) FROM conflicts WHERE status='open'")
        ).scalar() or 0.0

        print(f"\n{'='*70}")
        print(f"CONFLICT QUEUE  |  Open: {total_open}  |  Weighted penalty: {weighted_sum:.1f}")
        print(f"{'='*70}\n")

        for i, row in enumerate(rows, 1):
            print(f"[{i}] ID: {row['id'][:8]}...")
            print(f"    Type: {row['conflict_type']}  |  Status: {row['status']}  |  Weight: {row['penalty_weight']}")
            print(f"    Axis: {row['conflict_axis']}")

            ctx = json.loads(row.get("context_difference") or "{}")
            if ctx:
                print(f"    Context diff: {ctx}")

            # Fetch interaction details
            int_a = conn.execute(
                select(db.interactions).where(db.interactions.c.id == row["interaction_a_id"])
            ).mappings().fetchone()
            int_b = conn.execute(
                select(db.interactions).where(db.interactions.c.id == row["interaction_b_id"])
            ).mappings().fetchone()

            if int_a and int_b:
                def entity_name(eid):
                    r = conn.execute(
                        select(db.entities.c.canonical_name).where(db.entities.c.id == eid)
                    ).fetchone()
                    return r[0] if r else eid[:8]

                a_name = f"{entity_name(int_a['entity_a_id'])} --[{int_a['effect']}]--> {entity_name(int_a['entity_b_id'])}"
                b_name = f"{entity_name(int_b['entity_a_id'])} --[{int_b['effect']}]--> {entity_name(int_b['entity_b_id'])}"
                print(f"    Claim A: {a_name}  (confidence: {int_a['composite_confidence']}, n={int_a['evidence_count']})")
                print(f"    Claim B: {b_name}  (confidence: {int_b['composite_confidence']}, n={int_b['evidence_count']})")

            if args.show_evidence:
                for label, interaction in [("A", int_a), ("B", int_b)]:
                    if interaction:
                        ev_rows = conn.execute(
                            select(db.evidence).where(db.evidence.c.interaction_id == interaction["id"])
                        ).mappings().all()
                        for ev in ev_rows[:3]:
                            print(f"      Evidence {label}: [{ev['evidence_type']}] {ev.get('organism','')} | \"{ev['snippet'][:100]}...\"")

            if row.get("llm_analysis"):
                print(f"    LLM analysis: {row['llm_analysis'][:200]}...")

            print()


if __name__ == "__main__":
    main()
