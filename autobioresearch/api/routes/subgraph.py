from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection

from autobioresearch.api.dependencies import get_conn
from autobioresearch.api.schemas import EdgeView, NodeView, SubgraphRequest, SubgraphResponse

router = APIRouter()

_ALL_EDGES_SQL = text("""
    SELECT
        i.id,
        i.entity_a_id AS source,
        i.entity_b_id AS target,
        i.interaction_type,
        i.direction,
        i.effect,
        i.composite_confidence,
        COALESCE(i.composite_confidence_score, 0.0) AS composite_confidence_score,
        i.evidence_count,
        ea.id         AS node_a_id,
        ea.display_name AS node_a_display,
        ea.canonical_name AS node_a_canonical,
        ea.entity_type AS node_a_type,
        ea.organism   AS node_a_organism,
        ea.paper_count AS node_a_paper_count,
        eb.id         AS node_b_id,
        eb.display_name AS node_b_display,
        eb.canonical_name AS node_b_canonical,
        eb.entity_type AS node_b_type,
        eb.organism   AS node_b_organism,
        eb.paper_count AS node_b_paper_count
    FROM interactions i
    JOIN entities ea ON i.entity_a_id = ea.id
    JOIN entities eb ON i.entity_b_id = eb.id
    WHERE (i.entity_a_id IN :ids OR i.entity_b_id IN :ids)
      AND COALESCE(i.composite_confidence_score, 0.0) >= :min_score
    ORDER BY composite_confidence_score DESC
    LIMIT :edge_limit
""").bindparams(bindparam("ids", expanding=True))


@router.post("/subgraph", response_model=SubgraphResponse)
def get_subgraph(body: SubgraphRequest, conn: Connection = Depends(get_conn)):
    if not body.entity_ids:
        raise HTTPException(status_code=422, detail="entity_ids must not be empty")

    edge_limit = body.edge_limit
    min_score = body.min_confidence_score
    entity_type_filter = set(body.entity_type_filter) if body.entity_type_filter else None

    nodes: dict[str, NodeView] = {}
    edges: dict[str, EdgeView] = {}
    frontier: set[str] = set(body.entity_ids)
    visited: set[str] = set(body.entity_ids)
    max_hops_reached = 0

    for hop in range(1, body.hops + 1):
        if not frontier or len(edges) >= edge_limit:
            break

        ids_tuple = tuple(frontier)
        remaining = edge_limit - len(edges)
        rows = conn.execute(_ALL_EDGES_SQL, {
            "ids": ids_tuple,
            "min_score": min_score,
            "edge_limit": remaining,
        }).mappings().all()

        new_frontier: set[str] = set()
        for row in rows:
            if len(edges) >= edge_limit:
                break

            # Apply entity type filter to both endpoints
            if entity_type_filter:
                if row["node_a_type"] not in entity_type_filter and row["node_b_type"] not in entity_type_filter:
                    continue

            edge_id = row["id"]
            if edge_id not in edges:
                edges[edge_id] = EdgeView(
                    id=edge_id,
                    source=row["source"],
                    target=row["target"],
                    interaction_type=row["interaction_type"],
                    direction=row["direction"],
                    effect=row["effect"],
                    composite_confidence=row["composite_confidence"] or "low",
                    composite_confidence_score=row["composite_confidence_score"],
                    evidence_count=row["evidence_count"] or 0,
                )

            # Register both endpoint nodes
            for node_id, display, canonical, etype, organism, pcount in [
                (row["node_a_id"], row["node_a_display"], row["node_a_canonical"],
                 row["node_a_type"], row["node_a_organism"], row["node_a_paper_count"]),
                (row["node_b_id"], row["node_b_display"], row["node_b_canonical"],
                 row["node_b_type"], row["node_b_organism"], row["node_b_paper_count"]),
            ]:
                if node_id not in nodes:
                    nodes[node_id] = NodeView(
                        id=node_id,
                        display_name=display,
                        canonical_name=canonical,
                        entity_type=etype,
                        organism=organism,
                        paper_count=pcount or 0,
                    )
                if node_id not in visited:
                    new_frontier.add(node_id)
                    visited.add(node_id)

        if new_frontier:
            max_hops_reached = hop
        frontier = new_frontier

    return SubgraphResponse(
        nodes=list(nodes.values()),
        edges=list(edges.values()),
        stats={
            "n_nodes": len(nodes),
            "n_edges": len(edges),
            "max_hops_reached": max_hops_reached,
            "truncated": len(edges) >= edge_limit,
        },
    )
