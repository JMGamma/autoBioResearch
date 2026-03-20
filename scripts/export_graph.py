"""
Export the knowledge graph to JSON or GraphML format.

Usage:
    uv run scripts/export_graph.py --format json --out graph.json
    uv run scripts/export_graph.py --format graphml --out graph.graphml
    uv run scripts/export_graph.py --format json --min-confidence high --min-evidence 2
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select

from autobioresearch.database import init_engine
from autobioresearch import database as db


def export_json(conn, min_confidence: str, min_evidence: int) -> dict:
    """Export graph as a JSON dict with nodes and edges."""
    conf_order = {"high": 3, "medium": 2, "low": 1}
    min_conf_num = conf_order.get(min_confidence, 1)

    # Entities as nodes
    entity_rows = conn.execute(select(db.entities)).mappings().all()
    nodes = []
    entity_ids = set()
    for row in entity_rows:
        nodes.append({
            "id": row["id"],
            "canonical_name": row["canonical_name"],
            "display_name": row["display_name"],
            "entity_type": row["entity_type"],
            "organism": row["organism"],
            "paper_count": row["paper_count"],
            "synonyms": json.loads(row.get("synonyms") or "[]"),
            "external_ids": json.loads(row.get("external_ids") or "{}"),
        })
        entity_ids.add(row["id"])

    # Interactions as edges
    int_rows = conn.execute(select(db.interactions)).mappings().all()
    edges = []
    for row in int_rows:
        conf_num = conf_order.get(row.get("composite_confidence", "low"), 1)
        if conf_num < min_conf_num:
            continue
        if row.get("evidence_count", 0) < min_evidence:
            continue

        # Fetch evidence records
        ev_rows = conn.execute(
            select(db.evidence).where(db.evidence.c.interaction_id == row["id"])
        ).mappings().all()

        edges.append({
            "id": row["id"],
            "source": row["entity_a_id"],
            "target": row["entity_b_id"],
            "interaction_type": row["interaction_type"],
            "direction": row["direction"],
            "effect": row["effect"],
            "evidence_count": row["evidence_count"],
            "composite_confidence": row["composite_confidence"],
            "composite_confidence_score": row["composite_confidence_score"],
            "evidence": [
                {
                    "paper_id": e["paper_id"],
                    "evidence_type": e["evidence_type"],
                    "evidence_subtype": e.get("evidence_subtype"),
                    "confidence": e["confidence"],
                    "confidence_score": e["confidence_score"],
                    "organism": e.get("organism"),
                    "tissue_cell_type": e.get("tissue_cell_type"),
                    "condition": e.get("condition"),
                    "assay_type": e.get("assay_type"),
                    "snippet": e.get("snippet"),
                }
                for e in ev_rows
            ],
        })

    # Open conflicts
    conflict_rows = conn.execute(
        select(db.conflicts).where(db.conflicts.c.status == "open")
    ).mappings().all()
    conflicts = [
        {
            "id": c["id"],
            "interaction_a_id": c["interaction_a_id"],
            "interaction_b_id": c["interaction_b_id"],
            "conflict_type": c["conflict_type"],
            "conflict_axis": c["conflict_axis"],
            "penalty_weight": c["penalty_weight"],
            "status": c["status"],
        }
        for c in conflict_rows
    ]

    return {
        "nodes": nodes,
        "edges": edges,
        "open_conflicts": conflicts,
        "stats": {
            "n_entities": len(nodes),
            "n_interactions": len(edges),
            "n_open_conflicts": len(conflicts),
        }
    }


def export_graphml(conn, min_confidence: str, min_evidence: int) -> str:
    """Export as GraphML XML string."""
    data = export_json(conn, min_confidence, min_evidence)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<graphml xmlns="http://graphml.graphdrawing.org/graphml">',
        '  <key id="name" for="node" attr.name="name" attr.type="string"/>',
        '  <key id="type" for="node" attr.name="type" attr.type="string"/>',
        '  <key id="organism" for="node" attr.name="organism" attr.type="string"/>',
        '  <key id="interaction" for="edge" attr.name="interaction" attr.type="string"/>',
        '  <key id="effect" for="edge" attr.name="effect" attr.type="string"/>',
        '  <key id="confidence" for="edge" attr.name="confidence" attr.type="string"/>',
        '  <key id="evidence_count" for="edge" attr.name="evidence_count" attr.type="int"/>',
        '  <graph id="G" edgedefault="directed">',
    ]

    for node in data["nodes"]:
        lines.append(f'    <node id="{node["id"]}">')
        lines.append(f'      <data key="name">{node["canonical_name"]}</data>')
        lines.append(f'      <data key="type">{node["entity_type"]}</data>')
        if node.get("organism"):
            lines.append(f'      <data key="organism">{node["organism"]}</data>')
        lines.append('    </node>')

    for edge in data["edges"]:
        lines.append(f'    <edge source="{edge["source"]}" target="{edge["target"]}">')
        lines.append(f'      <data key="interaction">{edge["interaction_type"]}</data>')
        if edge.get("effect"):
            lines.append(f'      <data key="effect">{edge["effect"]}</data>')
        lines.append(f'      <data key="confidence">{edge["composite_confidence"]}</data>')
        lines.append(f'      <data key="evidence_count">{edge["evidence_count"]}</data>')
        lines.append('    </edge>')

    lines.extend(['  </graph>', '</graphml>'])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Export AutoBioResearch knowledge graph")
    parser.add_argument("--db-path", default="./autobioresearch.db")
    parser.add_argument("--format", choices=["json", "graphml"], default="json")
    parser.add_argument("--out", help="Output file path (default: stdout for JSON)")
    parser.add_argument("--min-confidence", choices=["low", "medium", "high"], default="low")
    parser.add_argument("--min-evidence", type=int, default=1,
                        help="Minimum evidence records per interaction")
    args = parser.parse_args()

    engine = init_engine(args.db_path)

    with engine.connect() as conn:
        if args.format == "json":
            data = export_json(conn, args.min_confidence, args.min_evidence)
            output = json.dumps(data, indent=2)
        else:
            output = export_graphml(conn, args.min_confidence, args.min_evidence)

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"Exported to {args.out}")
    else:
        print(output)


if __name__ == "__main__":
    main()
