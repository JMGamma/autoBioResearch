from __future__ import annotations

import logging
import time

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.engine import Connection

from autobioresearch.api.dependencies import get_conn
from autobioresearch.api.limiter import limiter
from autobioresearch.api.schemas import PerturbationRequest, PerturbationResponse
from autobioresearch.config import AppConfig
from autobioresearch.perturbation.cache import PerturbationCache
from autobioresearch.perturbation.propagator import run_propagation
from autobioresearch.storage.repositories import EntityRepo

logger = logging.getLogger("autobioresearch.api.perturbation")
router = APIRouter()

_cache: PerturbationCache | None = None


def _get_cache() -> PerturbationCache:
    global _cache
    if _cache is None:
        cfg = AppConfig.from_yaml()
        _cache = PerturbationCache(cfg.cache_db_path)
    return _cache


@router.post("/perturbation", response_model=PerturbationResponse)
@limiter.limit("30/minute")
def run_perturbation(
    request: Request,
    body: PerturbationRequest,
    background_tasks: BackgroundTasks,
    conn: Connection = Depends(get_conn),
):
    if body.mode not in ("suppress", "promote"):
        raise HTTPException(status_code=422, detail="mode must be 'suppress' or 'promote'")
    if body.depth < 1 or body.depth > 4:
        raise HTTPException(status_code=400, detail="depth must be between 1 and 4")

    repo = EntityRepo(conn)
    entity_id = repo.find_by_synonym(body.entity)
    if not entity_id:
        raise HTTPException(status_code=404, detail=f"Entity not found: {body.entity!r}")

    entity = repo.get_by_id(entity_id)
    seed_name = entity["canonical_name"] if entity else body.entity

    cache = _get_cache()
    t0 = time.monotonic()

    cfg = AppConfig.from_yaml()
    exponent = cfg.perturbation_combination_exponent

    result, cache_hit = cache.get_or_compute(
        entity_id=entity_id,
        mode=body.mode,
        depth=body.depth,
        compute_fn=lambda: run_propagation(
            conn=conn,
            seed_entity_id=entity_id,
            seed_name=seed_name,
            mode=body.mode,
            depth=body.depth,
            exponent=exponent,
        ),
    )

    elapsed_ms = round((time.monotonic() - t0) * 1000)
    result["stats"]["duration_ms"] = elapsed_ms

    if not cache_hit:
        background_tasks.add_task(cache.write, entity_id, body.mode, body.depth, result)

    background_tasks.add_task(
        cache.log_query, seed_name, body.mode, body.depth, elapsed_ms, result["stats"]["n_affected"]
    )

    logger.info(
        "perturbation entity=%s mode=%s depth=%d affected=%d cache_hit=%s duration_ms=%d",
        seed_name, body.mode, body.depth,
        result["stats"]["n_affected"], cache_hit, elapsed_ms,
    )

    return PerturbationResponse(
        schema_version="1.0",
        query={
            "entity": body.entity,
            "entity_id": entity_id,
            "entity_resolved": seed_name,
            "mode": body.mode,
            "depth": body.depth,
            "cache_hit": cache_hit,
        },
        seed=result["seed"],
        affected=result["affected"],
        excluded_edges=result["excluded_edges"],
        stats=result["stats"],
    )
