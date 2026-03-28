"""
AutoBioResearch FastAPI service.

Run with:
    uv run uvicorn autobioresearch.api.main:app --reload --port 8000

Or as a module:
    uv run -m autobioresearch.api
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from autobioresearch.api.limiter import limiter
from autobioresearch.api.routes import entities, evidence, paths, perturbation, stats, subgraph
from autobioresearch.config import AppConfig
from autobioresearch.utils.logging_config import setup_logging

logger = logging.getLogger("autobioresearch.api")


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = AppConfig.from_yaml()
    setup_logging(cfg.log_level, cfg.log_file)
    logger.info("AutoBioResearch API starting (host=%s port=%s)", cfg.api_host, cfg.api_port)
    yield
    logger.info("AutoBioResearch API shut down")


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="AutoBioResearch API",
    description="Read-only knowledge graph explorer for biological entities and interactions.",
    version="1.0.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ── Request logging middleware ─────────────────────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    t0 = time.monotonic()
    response = await call_next(request)
    elapsed_ms = round((time.monotonic() - t0) * 1000)
    logger.info(
        "%s %s → %d (%dms)",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response


# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(entities.router, prefix="/api", tags=["entities"])
app.include_router(subgraph.router, prefix="/api", tags=["subgraph"])
app.include_router(perturbation.router, prefix="/api", tags=["perturbation"])
app.include_router(paths.router, prefix="/api", tags=["paths"])
app.include_router(evidence.router, prefix="/api", tags=["evidence"])
app.include_router(stats.router, prefix="/api", tags=["stats"])


# ── Frontend SPA serving (production) ─────────────────────────────────────────
# In dev, Vite handles the frontend (localhost:5173) and proxies /api/* to this server.
# We avoid mounting at "/" to prevent Starlette from shadowing API routes.
# Instead: mount /assets explicitly and serve index.html via a catch-all route.
_DIST_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "frontend_dist"))
if os.path.isdir(_DIST_DIR):
    _assets_dir = os.path.join(_DIST_DIR, "assets")
    if os.path.isdir(_assets_dir):
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        candidate = os.path.join(_DIST_DIR, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(_DIST_DIR, "index.html"))


if __name__ == "__main__":
    cfg = AppConfig.from_yaml()
    uvicorn.run("autobioresearch.api.main:app", host=cfg.api_host, port=cfg.api_port, reload=True)
