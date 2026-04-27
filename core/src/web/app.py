"""NORA Web UI — FastAPI application.

Run with:
    python -m src.web.app
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.web.config import load_config
from src.web.jobs import JobQueue
from src.web.metrics import MetricsStore
from src.web.middleware import MetricsMiddleware
from src.web.path_mapper import PathMapper
from src.web.routes.corrections import router as corrections_router
from src.web.routes.dashboard import router as dashboard_router
from src.web.routes.environments import router as environments_router
from src.web.routes.files import router as files_router
from src.web.routes.jobs import router as jobs_router
from src.web.routes.metrics_route import router as metrics_router
from src.web.routes.pipeline import router as pipeline_router
from src.web.routes.query import router as query_router

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"
TEMPLATES_DIR = WEB_DIR / "templates"

config = load_config()

_start_time: float = 0.0


def _duration_filter(job) -> str:
    """Human-readable duration for a Job."""
    if not job.started_at:
        return "--"
    try:
        start = datetime.fromisoformat(job.started_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return "--"
    if job.completed_at:
        try:
            end = datetime.fromisoformat(job.completed_at.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            end = datetime.now(UTC)
    else:
        end = datetime.now(UTC)
    delta = end - start
    total_seconds = int(delta.total_seconds())
    if total_seconds < 1:
        return "< 1s"
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes > 0:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


# -- Lifecycle ----------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _start_time
    _start_time = time.time()
    logger.info("NORA Web UI starting (root_path=%r)", config.root_path)

    job_queue = JobQueue(config.db_path)
    await job_queue.init_db()
    app.state.job_queue = job_queue

    metrics_db = config.db_path.replace(".db", "_metrics.db") if config.db_path.endswith(".db") else config.db_path + "_metrics"
    metrics_store = MetricsStore(metrics_db)
    await metrics_store.init_db()
    app.state.metrics = metrics_store

    path_mapper = PathMapper(config.path_mappings)
    app.state.path_mapper = path_mapper

    # Start resource sampler background task
    from src.web.resource_sampler import start_resource_sampler
    sampler_task = await start_resource_sampler(metrics_store, interval=30, data_dir="data")

    yield

    sampler_task.cancel()
    try:
        await sampler_task
    except asyncio.CancelledError:
        pass
    logger.info("NORA Web UI shutting down")


# -- App ----------------------------------------------------------------------

app = FastAPI(
    title="NORA",
    description="Network Operator Requirements Analyzer",
    root_path=config.root_path,
    lifespan=lifespan,
)

app.add_middleware(MetricsMiddleware)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(dashboard_router)
app.include_router(corrections_router)
app.include_router(environments_router)
app.include_router(files_router)
app.include_router(jobs_router)
app.include_router(metrics_router)
app.include_router(pipeline_router)
app.include_router(query_router)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["duration"] = _duration_filter


def _template_response(
    request: Request,
    name: str,
    context: dict | None = None,
) -> HTMLResponse:
    """Render a template with root_path injected into context."""
    ctx = {"root_path": config.root_path}
    if context:
        ctx.update(context)
    return templates.TemplateResponse(request, name, ctx)


# -- Pages --------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return _template_response(request, "dashboard.html")


# -- API endpoints ------------------------------------------------------------

@app.get("/api/health")
async def health_check():
    uptime = time.time() - _start_time if _start_time else 0.0
    ollama_ok = False
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(config.ollama_url)
            ollama_ok = resp.status_code == 200
    except Exception:
        pass
    return {
        "status": "ok",
        "ollama": ollama_ok,
        "uptime_seconds": round(uptime, 1),
    }


# -- Entry point --------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    )
    uvicorn.run(
        "src.web.app:app",
        host=config.host,
        port=config.port,
        reload=True,
    )
