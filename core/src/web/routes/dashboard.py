"""Dashboard page and API routes."""

from __future__ import annotations

import logging
import time

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.web.jobs import JobQueue

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/dashboard/stats")
async def dashboard_stats(request: Request):
    job_queue: JobQueue = request.app.state.job_queue

    all_jobs = await job_queue.list_jobs(limit=1000)
    total = len(all_jobs)
    running = sum(1 for j in all_jobs if j.status == "running")
    completed = sum(1 for j in all_jobs if j.status == "completed")
    failed = sum(1 for j in all_jobs if j.status == "failed")

    recent = await job_queue.list_jobs(limit=5)
    recent_list = [
        {
            "id": j.id,
            "status": j.status,
            "job_type": j.job_type,
            "environment": j.environment or "-",
            "created_at": j.created_at[:19].replace("T", " "),
        }
        for j in recent
    ]

    return {
        "total_jobs": total,
        "running_jobs": running,
        "completed_jobs": completed,
        "failed_jobs": failed,
        "recent_jobs": recent_list,
    }


@router.get("/api/dashboard/status", response_class=HTMLResponse)
async def dashboard_status_partial(request: Request):
    from src.web.app import _template_response, config
    import src.web.app as _app_mod

    uptime = time.time() - _app_mod._start_time if _app_mod._start_time else 0.0
    ollama_ok = False
    gpu_info = None

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(config.ollama_url)
            ollama_ok = resp.status_code == 200
    except Exception:
        pass

    if ollama_ok:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{config.ollama_url}/api/ps")
                if resp.status_code == 200:
                    data = resp.json()
                    models = data.get("models", [])
                    if models:
                        details = models[0].get("details", {})
                        gpu_info = details.get("quantization_level", None)
        except Exception:
            pass

    hours, remainder = divmod(int(uptime), 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        uptime_str = f"{hours}h {minutes}m {seconds}s"
    elif minutes > 0:
        uptime_str = f"{minutes}m {seconds}s"
    else:
        uptime_str = f"{seconds}s"

    return _template_response(request, "partials/dashboard_status.html", {
        "ollama_ok": ollama_ok,
        "model_name": config.default_model,
        "uptime_str": uptime_str,
        "gpu_info": gpu_info,
    })


@router.get("/api/dashboard/jobs", response_class=HTMLResponse)
async def dashboard_jobs_partial(request: Request):
    from src.web.app import _template_response

    job_queue: JobQueue = request.app.state.job_queue
    recent = await job_queue.list_jobs(limit=5)

    return _template_response(request, "partials/dashboard_jobs.html", {
        "recent_jobs": recent,
    })
