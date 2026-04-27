"""Jobs routes -- listing, detail, SSE log streaming, cancel."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from core.src.web.jobs import JobQueue

logger = logging.getLogger(__name__)

router = APIRouter()

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_list(request: Request):
    from core.src.web.app import _template_response

    status_filter = request.query_params.get("status")
    if status_filter and status_filter not in (
        "submitted", "running", "completed", "failed", "cancelled",
    ):
        status_filter = None
    job_queue: JobQueue = request.app.state.job_queue
    jobs = await job_queue.list_jobs(status=status_filter)
    return _template_response(request, "jobs.html", {
        "jobs": jobs,
        "status_filter": status_filter,
    })


@router.get("/jobs/table", response_class=HTMLResponse)
async def jobs_table_partial(request: Request):
    from core.src.web.app import _template_response

    status_filter = request.query_params.get("status")
    if status_filter and status_filter not in (
        "submitted", "running", "completed", "failed", "cancelled",
    ):
        status_filter = None
    job_queue: JobQueue = request.app.state.job_queue
    jobs = await job_queue.list_jobs(status=status_filter)
    return _template_response(request, "partials/jobs_table.html", {
        "jobs": jobs,
    })


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(request: Request, job_id: str):
    from core.src.web.app import _template_response

    job_queue: JobQueue = request.app.state.job_queue
    job = await job_queue.get(job_id)
    if job is None:
        return _template_response(request, "jobs.html", {
            "jobs": [],
            "status_filter": None,
            "error": f"Job {job_id} not found.",
        })
    return _template_response(request, "job_detail.html", {"job": job})


@router.get("/api/jobs/{job_id}/stream")
async def job_log_stream(request: Request, job_id: str):
    job_queue: JobQueue = request.app.state.job_queue
    initial_after = int(request.query_params.get("after", "0"))

    async def event_generator():
        last_line = initial_after
        last_progress = -1
        last_stage = None

        while True:
            job = await job_queue.get_meta(job_id)
            if job is None:
                yield f"event: stream_error\ndata: {json.dumps({'message': 'Job not found'})}\n\n"
                return

            logs = await job_queue.get_logs_with_numbers(job_id, after_line=last_line)
            for line_number, message in logs:
                yield f"data: {json.dumps({'line': message, 'line_number': line_number})}\n\n"
                last_line = line_number

            if job.progress != last_progress or job.current_stage != last_stage:
                last_progress = job.progress
                last_stage = job.current_stage
                yield (
                    f"event: progress\n"
                    f"data: {json.dumps({'progress': job.progress, 'current_stage': job.current_stage or ''})}\n\n"
                )

            if job.status in TERMINAL_STATUSES:
                yield f"event: done\ndata: {json.dumps({'status': job.status})}\n\n"
                return

            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/jobs/{job_id}/cancel")
async def cancel_job(request: Request, job_id: str):
    from core.src.web.app import config

    job_queue: JobQueue = request.app.state.job_queue
    await job_queue.cancel(job_id)
    return RedirectResponse(
        url=f"{config.root_path}/jobs/{job_id}",
        status_code=303,
    )
