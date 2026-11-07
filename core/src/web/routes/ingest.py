"""Self-serve ingestion: the Ingest page and `POST /api/ingest`
(strand self-serve-ingestion; design §5).

The web validates a request, writes one `ingest` job and its `ingests`
row, and shows progress from the jobs database. The host-side runner
(`core.src.ingest_runner`) does the work. Every route here requires the
ingest cookie (`team_mode.is_ingest_user`): the page says "locked"
without it, the API answers 403.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from core.src.web import team_mode as tm
from core.src.web.jobs import Ingest, JobQueue
from core.src.web.jobs_schema import INGEST_STAGES, RETRY_FROM

logger = logging.getLogger(__name__)

router = APIRouter()

_MNO_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,30}$")
_REL_RE = re.compile(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\d{4}$")
_UNC_RE = re.compile(r"^(\\\\|//)[^\\/]+[\\/][^\\/]+")
_TERMINAL_JOB = {"completed", "failed", "cancelled"}

# What the page shows next to each stage (design §8: measured on the
# current host; enrichment is the long one).
STAGE_HINTS: dict[str, str] = {
    "documents": "seconds", "start": "seconds", "parse": "minutes (only the new cell)",
    "prompts": "seconds", "taxonomy": "minutes (only the new documents)",
    "enrich": "about an hour", "verify": "seconds", "promote": "a minute",
    "convert": "a minute", "publish": "a minute", "eval": "minutes", "gate": "seconds",
}

# Ingest states in which a cell counts as taken.
_TAKEN_STATES = ("queued", "running", "staged", "served")
_RUNNER_FRESH_SECONDS = 120


def _store_url() -> str:
    urls = os.environ.get("NORA_STORE_URLS", "")
    first = urls.split(",")[0].strip() if urls else ""
    return (first or os.environ.get("NORA_STORE_URL", "").strip()).rstrip("/")


async def served_cells() -> list[str] | None:
    """`<mno>__<release>` of every served cell, from the store's healthz;
    None when the store cannot be asked (tests replace this)."""
    url = _store_url()
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{url}/healthz")
        if resp.status_code != 200:
            return None
        cells = resp.json().get("cells") or []
        return [str(c) for c in cells]
    except Exception as exc:  # noqa: BLE001 — a store hiccup is a validation message, not a 500
        logger.warning("ingest: healthz unavailable: %s", exc)
        return None


def _wants_json(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    ctype = request.headers.get("content-type", "")
    return "application/json" in accept or "application/json" in ctype


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _runner_status(runners: list[dict]) -> dict[str, Any]:
    if not runners:
        return {"state": "never", "text": "no runner has reported yet", "runners": []}
    newest = runners[0]
    try:
        seen = datetime.strptime(newest["seen_at"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
        age = (datetime.now(UTC) - seen).total_seconds()
    except (KeyError, ValueError):
        age = float("inf")
    fresh = age <= _RUNNER_FRESH_SECONDS
    busy = bool(newest.get("job_id"))
    text = (f"{newest['name']} {'running a job' if busy else 'idle'}, seen {int(age)}s ago" if fresh
            else f"{newest['name']} not seen for {int(age) if age != float('inf') else '?'}s — jobs wait until it is back")
    return {"state": "busy" if (fresh and busy) else ("idle" if fresh else "stale"),
            "text": text, "runners": runners}


def _ingest_view(ing: Ingest) -> dict[str, Any]:
    d = ing.__dict__.copy()
    st = ing.state
    d["can_flip"] = st == "staged" and ing.verdict == "pass"
    d["can_rollback"] = st == "served" and bool(ing.previous_label)
    d["can_retry"] = st == "failed" and bool(ing.stage)
    d["retry_from"] = RETRY_FROM.get(ing.stage or "", ing.stage) if d["can_retry"] else None
    d["can_abandon"] = st in ("failed", "cancelled", "staged")
    d["can_cancel"] = st in ("queued", "running")
    d["queue_position"] = None
    d["stages"] = [{"name": s, "hint": STAGE_HINTS.get(s, ""),
                    "done": _stage_done(ing, s), "current": ing.stage == s and st == "running"}
                   for s in INGEST_STAGES]
    return d


def _stage_done(ing: Ingest, stage: str) -> bool:
    if ing.state in ("staged", "served", "rolled_back"):
        return True
    if not ing.stage or ing.stage not in INGEST_STAGES:
        return False
    return INGEST_STAGES.index(stage) < INGEST_STAGES.index(ing.stage)


# -- validation -------------------------------------------------------------------

def _callback_ok(url: str) -> bool:
    try:
        u = urlparse(url)
    except ValueError:
        return False
    return u.scheme in ("http", "https") and bool(u.hostname)


async def validate_request(job_queue: JobQueue, mno: str, release: str, source: str,
                           callback_url: str = "") -> str | None:
    """The reason a request is refused, or None. Order: shape, served MNO,
    cell free. Submits queue; the runner runs one at a time."""
    if not _MNO_RE.match(mno):
        return "MNO must be the cell name as served (letters, digits, dashes; upper case)"
    if not _REL_RE.match(release):
        return "release must be MMMYYYY, for example Apr2031"
    if not _UNC_RE.match(source):
        return r"source must be a shared-folder path (\\SERVER\Share\folder)"
    if callback_url and not _callback_ok(callback_url):
        return "callback_url must be an http or https URL"
    cells = await served_cells()
    if cells is None:
        return "the serving store cannot be asked which MNOs it serves; try again or tell the administrator"
    mnos = {c.split("__", 1)[0].upper() for c in cells}
    if mno.upper() not in mnos:
        return f"{mno} is not a served MNO (a new MNO's first release is ingested by the administrator)"
    if f"{mno}__{release}".upper() in {c.upper() for c in cells}:
        return f"{mno} {release} is already served"
    for ing in await job_queue.list_ingests(limit=500):
        if ing.state in _TAKEN_STATES and ing.mno.upper() == mno.upper() and ing.release == release:
            return f"{mno} {release} is already {ing.state} (ingest {ing.id[:8]})"
    # Submits queue: the runner takes them in order, one cycle at a time.
    return None


def queue_positions(ingests: list[Ingest]) -> dict[str, int]:
    """1-based position of every queued ingest, oldest first."""
    queued = sorted((i for i in ingests if i.state == "queued"), key=lambda i: i.created_at)
    return {i.id: n for n, i in enumerate(queued, start=1)}


def blocking_ingests(ingests: list[Ingest]) -> list[Ingest]:
    """Failed or cancelled ingests that opened a build and were not
    abandoned: their cycle may still hold the driver's baton, and the
    next queued ingest fails at `start` until they are retried or
    abandoned."""
    return [i for i in ingests if i.state in ("failed", "cancelled") and i.build]


# -- pages ------------------------------------------------------------------------------

@router.get("/ingest", response_class=HTMLResponse)
async def ingest_page(request: Request):
    from core.src.web.app import _template_response
    if not tm.is_ingest_user(request):
        return _template_response(request, "ingest.html", {"locked": True, "ingests": []})
    job_queue: JobQueue = request.app.state.job_queue
    ingests = await job_queue.list_ingests(limit=50)
    cells = await served_cells()
    mnos = sorted({c.split("__", 1)[0] for c in cells}) if cells else []
    return _template_response(request, "ingest.html", {
        "locked": False,
        "ingests": _views(ingests),
        "blocking": [_ingest_view(i) for i in blocking_ingests(ingests)],
        "mnos": mnos,
        "store_reachable": cells is not None,
        "runner": _runner_status(await job_queue.list_runners()),
        "error": request.query_params.get("error", ""),
    })


@router.get("/ingest/{ingest_id}", response_class=HTMLResponse)
async def ingest_detail_page(request: Request, ingest_id: str):
    from core.src.web.app import _template_response
    if not tm.is_ingest_user(request):
        return _template_response(request, "ingest.html", {"locked": True, "ingests": []})
    job_queue: JobQueue = request.app.state.job_queue
    ing = await job_queue.get_ingest(ingest_id)
    if ing is None:
        return RedirectResponse(f"{_root(request)}/ingest?error=no+such+ingest", status_code=303)
    job = await job_queue.get_meta(ing.job_id) if ing.job_id else None
    view = _ingest_view(ing)
    if ing.state == "queued":
        view["queue_position"] = queue_positions(await job_queue.list_ingests(limit=500)).get(ing.id)
    return _template_response(request, "ingest_detail.html", {
        "ing": view, "job": job,
        "runner": _runner_status(await job_queue.list_runners()),
        "error": request.query_params.get("error", ""),
    })


def _views(ingests: list[Ingest]) -> list[dict[str, Any]]:
    pos = queue_positions(ingests)
    out = []
    for i in ingests:
        v = _ingest_view(i)
        v["queue_position"] = pos.get(i.id)
        out.append(v)
    return out


def _root(request: Request) -> str:
    from core.src.web.app import config
    return config.root_path


# -- API --------------------------------------------------------------------------------

def _forbidden(request: Request):
    if _wants_json(request):
        return JSONResponse({"error": "ingest token required (open /ingest-unlock?token=… first)"}, status_code=403)
    return RedirectResponse(f"{_root(request)}/ingest", status_code=303)


async def _body(request: Request) -> dict[str, str]:
    if "application/json" in request.headers.get("content-type", ""):
        raw = await request.body()
        if not raw.strip():
            return {}
        data = await request.json()
        return {k: str(v).strip() for k, v in (data or {}).items()}
    form = await request.form()
    return {k: str(v).strip() for k, v in form.items()}


@router.get("/api/ingest")
async def api_list(request: Request):
    if not tm.is_ingest_user(request):
        return _forbidden(request)
    job_queue: JobQueue = request.app.state.job_queue
    ingests = await job_queue.list_ingests(limit=100)
    return JSONResponse({"ingests": _views(ingests),
                         "blocking": [i.id for i in blocking_ingests(ingests)],
                         "runner": _runner_status(await job_queue.list_runners())})


@router.post("/api/ingest")
async def api_submit(request: Request):
    """Enqueue one ingest. JSON or form: `source` (share path), `mno`,
    `release` (MMMYYYY), optional `note`, `submitted_by`. 202 with
    `{ingest_id, job_id}`; 400 with `{error}` when refused."""
    if not tm.is_ingest_user(request):
        return _forbidden(request)
    job_queue: JobQueue = request.app.state.job_queue
    data = await _body(request)
    mno = data.get("mno", "").upper()
    release = data.get("release", "")
    if release:
        release = release[:1].upper() + release[1:3].lower() + release[3:]
    source = data.get("source", "")
    note = data.get("note", "")[:500]
    by = data.get("submitted_by", "") or "anonymous"
    callback_url = data.get("callback_url", "")[:1000]
    error = await validate_request(job_queue, mno, release, source, callback_url)
    if error:
        if _wants_json(request):
            return JSONResponse({"error": error}, status_code=400)
        return RedirectResponse(f"{_root(request)}/ingest?error={httpx.QueryParams({'e': error})['e']}",
                                status_code=303)
    ing = await job_queue.create_ingest(mno=mno, release=release, source=source, submitted_by=by,
                                        note=note, callback_url=callback_url or None)
    job = await job_queue.submit("ingest", by, environment=f"ingest:{mno}/{release}",
                                 stages=list(INGEST_STAGES), params={"ingest_id": ing.id})
    await job_queue.update_ingest(ing.id, job_id=job.id)
    if _wants_json(request):
        position = queue_positions(await job_queue.list_ingests(limit=500)).get(ing.id)
        return JSONResponse({"ingest_id": ing.id, "job_id": job.id, "state": "queued",
                             "queue_position": position}, status_code=202)
    return RedirectResponse(f"{_root(request)}/ingest/{ing.id}", status_code=303)


@router.get("/api/ingest/{ingest_id}")
async def api_get(request: Request, ingest_id: str):
    if not tm.is_ingest_user(request):
        return _forbidden(request)
    job_queue: JobQueue = request.app.state.job_queue
    ing = await job_queue.get_ingest(ingest_id)
    if ing is None:
        return JSONResponse({"error": "no such ingest"}, status_code=404)
    job = await job_queue.get_meta(ing.job_id) if ing.job_id else None
    return JSONResponse({"ingest": _ingest_view(ing),
                         "job": {"id": job.id, "status": job.status, "current_stage": job.current_stage,
                                 "progress": job.progress, "heartbeat_at": job.heartbeat_at,
                                 "error_message": job.error_message} if job else None})


@router.get("/api/ingest/{ingest_id}/events")
async def api_events(request: Request, ingest_id: str):
    """Server-sent events for one ingest: `state` on every change of
    state, stage, verdict, label or job status; `done` when no job of
    this ingest is running any more. A client that holds the connection
    needs no polling; `?after=…` is not needed, the first event is the
    current state."""
    if not tm.is_ingest_user(request):
        return _forbidden(request)
    job_queue: JobQueue = request.app.state.job_queue
    if await job_queue.get_ingest(ingest_id) is None:
        return JSONResponse({"error": "no such ingest"}, status_code=404)

    async def gen():
        last: tuple | None = None
        idle = 0
        while True:
            ing = await job_queue.get_ingest(ingest_id)
            if ing is None:
                yield f"event: stream_error\ndata: {json.dumps({'message': 'ingest gone'})}\n\n"
                return
            job = await job_queue.get_meta(ing.job_id) if ing.job_id else None
            key = (ing.state, ing.stage, ing.verdict, ing.label, job.status if job else None,
                   job.current_stage if job else None)
            if key != last:
                last = key
                view = _ingest_view(ing)
                view["job"] = {"id": job.id, "status": job.status, "current_stage": job.current_stage,
                               "progress": job.progress} if job else None
                yield f"event: state\ndata: {json.dumps(view)}\n\n"
            running = job is not None and job.status not in _TERMINAL_JOB
            if not running:
                idle += 1
                if idle >= 2:   # one more read after the terminal status, so a late row update is sent
                    yield f"event: done\ndata: {json.dumps({'state': ing.state, 'verdict': ing.verdict})}\n\n"
                    return
            else:
                idle = 0
            await asyncio.sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                                      "X-Accel-Buffering": "no"})


async def _action(request: Request, ingest_id: str, action: str):
    if not tm.is_ingest_user(request):
        return _forbidden(request)
    job_queue: JobQueue = request.app.state.job_queue
    ing = await job_queue.get_ingest(ingest_id)
    if ing is None:
        return JSONResponse({"error": "no such ingest"}, status_code=404)
    view = _ingest_view(ing)
    by = (await _body(request)).get("submitted_by", "") or "anonymous"
    error = None
    job = None
    if action == "flip":
        if not view["can_flip"]:
            error = f"not servable: state {ing.state}, gate {ing.verdict or '-'}"
        else:
            job = await job_queue.submit("ingest-flip", by, environment=f"ingest:{ing.mno}/{ing.release}",
                                         params={"ingest_id": ing.id})
    elif action == "rollback":
        if not view["can_rollback"]:
            error = f"nothing to roll back: state {ing.state}"
        else:
            job = await job_queue.submit("ingest-rollback", by, environment=f"ingest:{ing.mno}/{ing.release}",
                                         params={"ingest_id": ing.id})
    elif action == "retry":
        if not view["can_retry"]:
            error = f"not retryable: state {ing.state}"
        else:
            job = await job_queue.submit("ingest", by, environment=f"ingest:{ing.mno}/{ing.release}",
                                         stages=list(INGEST_STAGES),
                                         params={"ingest_id": ing.id, "retry_from": view["retry_from"]})
            await job_queue.update_ingest(ing.id, state="queued", error=None)
    elif action == "abandon":
        if not view["can_abandon"]:
            error = f"cannot abandon: state {ing.state}"
        else:
            job = await job_queue.submit("ingest-abandon", by, environment=f"ingest:{ing.mno}/{ing.release}",
                                         params={"ingest_id": ing.id})
    elif action == "cancel":
        if not view["can_cancel"] or not ing.job_id:
            error = f"nothing to cancel: state {ing.state}"
        else:
            await job_queue.cancel(ing.job_id)
            if ing.state == "queued":
                await job_queue.update_ingest(ing.id, state="cancelled")
    else:
        error = f"unknown action {action}"
    if error:
        if _wants_json(request):
            return JSONResponse({"error": error}, status_code=409)
        return RedirectResponse(f"{_root(request)}/ingest/{ing.id}?error={httpx.QueryParams({'e': error})['e']}",
                                status_code=303)
    if job is not None:
        await job_queue.update_ingest(ing.id, job_id=job.id)
    if _wants_json(request):
        return JSONResponse({"ingest_id": ing.id, "job_id": job.id if job else ing.job_id, "action": action},
                            status_code=202)
    return RedirectResponse(f"{_root(request)}/ingest/{ing.id}", status_code=303)


@router.post("/api/ingest/{ingest_id}/flip")
async def api_flip(request: Request, ingest_id: str):
    return await _action(request, ingest_id, "flip")


@router.post("/api/ingest/{ingest_id}/rollback")
async def api_rollback(request: Request, ingest_id: str):
    return await _action(request, ingest_id, "rollback")


@router.post("/api/ingest/{ingest_id}/retry")
async def api_retry(request: Request, ingest_id: str):
    return await _action(request, ingest_id, "retry")


@router.post("/api/ingest/{ingest_id}/abandon")
async def api_abandon(request: Request, ingest_id: str):
    return await _action(request, ingest_id, "abandon")


@router.post("/api/ingest/{ingest_id}/cancel")
async def api_cancel(request: Request, ingest_id: str):
    return await _action(request, ingest_id, "cancel")
