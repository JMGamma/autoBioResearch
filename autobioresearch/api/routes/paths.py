"""
Path exploration endpoint.

Finds all directed chains of interactions between two entities up to max_hops.
Paths are scored by the product of edge confidence scores and sorted descending.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection

from autobioresearch.api.dependencies import get_conn
from autobioresearch.api.schemas import PathsRequest, PathsResponse
from autobioresearch.perturbation.propagator import get_outgoing_edges
from autobioresearch.perturbation.sign_map import effect_to_sign
from autobioresearch.storage.repositories import EntityRepo

router = APIRouter()


def _find_paths(
    conn: Connection,
    source_id: str,
    target_id: str,
    max_hops: int,
    min_confidence: float,
    max_paths: int,
) -> list[dict]:
    """
    DFS with backtracking from source_id to target_id.

    Each path is represented as:
      { nodes: [...], edges: [...], score: float, hop_count: int, net_sign: int }

    Visited set is per-path (stack), not global — this allows alternate routes
    through different intermediaries while preventing cycles within a single path.

    ASCII diagram:
      source ─[e1]─▶ A ─[e2]─▶ target   (path: source→A→target, score=e1.conf*e2.conf)
      source ─[e3]─▶ target              (path: source→target,   score=e3.conf)
    """
    results: list[dict] = []

    # Stack entries: (current_entity_id, current_entity_name, path_nodes, path_edges, path_in_set)
    # path_nodes: list of {id, display_name, entity_type}
    # path_edges: list of {id, source, target, effect, confidence_score, direction}
    # path_in_set: set of entity_ids already in this path (cycle guard)
    initial_node = {"id": source_id, "display_name": "", "entity_type": ""}
    stack: list[tuple[str, list[dict], list[dict], set[str]]] = [
        (source_id, [initial_node], [], {source_id})
    ]

    while stack and len(results) < max_paths:
        current_id, path_nodes, path_edges, path_in_set = stack.pop()

        if len(path_edges) >= max_hops:
            continue  # don't expand past the hop limit

        for edge in get_outgoing_edges(conn, current_id):
            if edge["confidence_score"] < min_confidence:
                continue

            next_id: str = edge["target_id"]

            if next_id == target_id:
                # Found a complete path — record it.
                target_node = {"id": next_id, "display_name": edge["target_name"], "entity_type": ""}
                complete_nodes = path_nodes + [target_node]
                complete_edges = path_edges + [_make_edge(current_id, edge)]

                # Backfill display_names on source node from its first outgoing edge name
                # (nodes carry name from the edge that discovered them except the seed).
                results.append(_score_path(complete_nodes, complete_edges))
                if len(results) >= max_paths:
                    break
            elif next_id not in path_in_set:
                # Intermediate node — continue DFS.
                next_node = {"id": next_id, "display_name": edge["target_name"], "entity_type": ""}
                stack.append((
                    next_id,
                    path_nodes + [next_node],
                    path_edges + [_make_edge(current_id, edge)],
                    path_in_set | {next_id},
                ))

    results.sort(key=lambda p: p["score"], reverse=True)
    return results


def _make_edge(source_id: str, edge: dict) -> dict:
    return {
        "id": edge["interaction_id"],
        "source": source_id,
        "target": edge["target_id"],
        "effect": edge["effect"],
        "confidence_score": edge["confidence_score"],
        "direction": "A_TO_B",  # get_outgoing_edges already resolves direction semantics
    }


def _score_path(nodes: list[dict], edges: list[dict]) -> dict:
    """
    Score = product of edge confidence scores (penalises long/low-confidence chains).
    net_sign = product of effect_to_sign() for each edge (+1 activating, -1 inhibitory, 0 mixed).
    """
    score = 1.0
    net_sign = 1
    for e in edges:
        score *= e["confidence_score"]
        sign = effect_to_sign(e["effect"])
        if sign == 0:
            net_sign = 0  # mixed/unknown chain
        elif net_sign != 0:
            net_sign *= sign

    return {
        "nodes": nodes,
        "edges": edges,
        "score": round(score, 6),
        "hop_count": len(edges),
        "net_sign": net_sign,
    }


def _enrich_source_node(node: dict, entity: dict | None) -> dict:
    """Fill in display_name and entity_type for the seed node from the DB record."""
    if entity:
        node = dict(node)
        node["display_name"] = entity.get("display_name") or entity.get("canonical_name", "")
        node["entity_type"] = entity.get("entity_type", "")
    return node


@router.post("/paths", response_model=PathsResponse)
def find_paths(body: PathsRequest, conn: Connection = Depends(get_conn)):
    repo = EntityRepo(conn)

    source_candidates = repo.find_by_synonym_candidates(body.source_entity)
    source_ids = {c["entity_id"] for c in source_candidates}
    if not source_ids:
        raise HTTPException(status_code=404, detail=f"Source entity not found: {body.source_entity!r}")
    if len(source_ids) > 1:
        raise HTTPException(status_code=409, detail={
            "message": f"Ambiguous entity name: {body.source_entity!r}",
            "candidates": [{"id": c["entity_id"], "canonical_name": c["canonical_name"], "entity_type": c["entity_type"], "organism": c["organism"]} for c in source_candidates],
        })
    source_id = next(iter(source_ids))

    target_candidates = repo.find_by_synonym_candidates(body.target_entity)
    target_ids = {c["entity_id"] for c in target_candidates}
    if not target_ids:
        raise HTTPException(status_code=404, detail=f"Target entity not found: {body.target_entity!r}")
    if len(target_ids) > 1:
        raise HTTPException(status_code=409, detail={
            "message": f"Ambiguous entity name: {body.target_entity!r}",
            "candidates": [{"id": c["entity_id"], "canonical_name": c["canonical_name"], "entity_type": c["entity_type"], "organism": c["organism"]} for c in target_candidates],
        })
    target_id = next(iter(target_ids))

    if source_id == target_id:
        raise HTTPException(status_code=400, detail="Source and target must be different entities")

    source_entity = repo.get_by_id(source_id)
    target_entity = repo.get_by_id(target_id)

    source_name = (source_entity or {}).get("canonical_name", body.source_entity)
    target_name = (target_entity or {}).get("canonical_name", body.target_entity)

    t0 = time.monotonic()
    paths = _find_paths(
        conn=conn,
        source_id=source_id,
        target_id=target_id,
        max_hops=body.max_hops,
        min_confidence=body.min_confidence,
        max_paths=body.max_paths,
    )
    elapsed_ms = round((time.monotonic() - t0) * 1000)

    # Batch-backfill display_name and entity_type for all nodes in all paths
    node_ids = list({n["id"] for path in paths for n in path["nodes"]})
    if node_ids:
        _meta_sql = text(
            "SELECT id, display_name, entity_type FROM entities WHERE id IN :ids"
        ).bindparams(bindparam("ids", expanding=True))
        meta = {
            row["id"]: row
            for row in conn.execute(_meta_sql, {"ids": tuple(node_ids)}).mappings()
        }
        for path in paths:
            for node in path["nodes"]:
                if node["id"] in meta:
                    node["display_name"] = meta[node["id"]]["display_name"]
                    node["entity_type"]  = meta[node["id"]]["entity_type"]

    # Backfill seed node display_name/entity_type in all paths
    for path in paths:
        if path["nodes"]:
            path["nodes"][0] = _enrich_source_node(path["nodes"][0], source_entity)

    return PathsResponse(
        source={"id": source_id, "display_name": source_name},
        target={"id": target_id, "display_name": target_name},
        paths=paths,
        stats={
            "paths_found": len(paths),
            "max_hops_searched": body.max_hops,
            "duration_ms": elapsed_ms,
        },
    )
