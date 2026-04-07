from __future__ import annotations

import logging
import threading
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
_app_cfg: AppConfig | None = None
_cache_lock = threading.Lock()


def _get_initialized() -> tuple[PerturbationCache, AppConfig]:
    """Return (cache, config), initializing both exactly once (thread-safe)."""
    global _cache, _app_cfg
    if _cache is None:
        with _cache_lock:
            if _cache is None:  # double-check inside lock
                _app_cfg = AppConfig.from_yaml()
                _cache = PerturbationCache(_app_cfg.cache_db_path)
    return _cache, _app_cfg


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

    repo = EntityRepo(conn)
    candidates = repo.find_by_synonym_candidates(body.entity)
    entity_ids = {c["entity_id"] for c in candidates}
    if not entity_ids:
        raise HTTPException(status_code=404, detail=f"Entity not found: {body.entity!r}")
    if len(entity_ids) > 1:
        raise HTTPException(status_code=409, detail={
            "message": f"Ambiguous entity name: {body.entity!r}",
            "candidates": [{"id": c["entity_id"], "canonical_name": c["canonical_name"], "entity_type": c["entity_type"], "organism": c["organism"]} for c in candidates],
        })
    entity_id = next(iter(entity_ids))

    entity = repo.get_by_id(entity_id)
    seed_name = entity["canonical_name"] if entity else body.entity

    cache, cfg = _get_initialized()
    t0 = time.monotonic()

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
