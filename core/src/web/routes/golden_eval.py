"""Eval Studio — expert one-stop-shop for golden eval samples
(FR-39, strand golden-eval, D-DRAFT-5).

Stage-1 authoring: query + ground-truth picker (MNO → Plan → Release
cascade over parse output via the shared ``req_tree`` loader, direct-paste
with auto-qualification, retrieval-assisted seeding). Stage-2 curation:
free-form chat with the on-prem LLM over the ground-truth texts; only the
final golden response persists.

All sample I/O goes through ``core.src.eval.golden`` — the schema has one
owner (no parallel write path). Sample content is proprietary: it never
appears in logs, metrics, or error messages (NFR-8).

Team-mode: the page family is whitelisted for gated experts; sample
delete is admin-only.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from core.src.eval.golden import (
    STATUSES,
    GoldenEvalError,
    GoldenSample,
    GroundTruthEntry,
    load_sample,
    load_samples,
    next_sample_id,
    sample_path,
    save_sample,
    validate_sample,
)
from core.src.web import req_tree
from core.src.web.team_mode import is_admin

logger = logging.getLogger(__name__)

router = APIRouter(tags=["eval-studio"])

_CHAT_CONTEXT_MAX_CHARS = 30000
_CHAT_SYSTEM = (
    "You are helping a telecom requirements expert craft an ideal reference "
    "answer to a question. The authoritative requirement texts are provided "
    "below — ground every statement in them; do not invent requirements. "
    "Iterate with the expert until they are satisfied.\n\n"
    "REQUIREMENT TEXTS:\n{context}"
)


def _env_dir() -> Path:
    from core.src.web.app import config

    return config.env_dir_path()


def _stack_url() -> str:
    """Primary sira-query stack URL — first of NORA_SIRA_QUERY_URLS,
    falling back to NORA_SIRA_QUERY_URL (same convention as the Test
    page / enrichment-review surfaces).
    """
    urls = os.environ.get("NORA_SIRA_QUERY_URLS", "")
    if urls.strip():
        return urls.split(",")[0].strip()
    return os.environ.get("NORA_SIRA_QUERY_URL", "").strip()


def _template(request: Request, name: str, ctx: dict):
    from core.src.web.app import _template_response

    return _template_response(request, name, ctx)


def _editor_ctx(request: Request, sample: GoldenSample) -> dict:
    return {
        "sample": sample,
        "statuses": STATUSES,
        "problems": validate_sample(sample),
        "admin": is_admin(request),
        "stack_url": _stack_url(),
        "mnos": sorted({c["mno"] for c in req_tree.list_cells(_env_dir())}),
    }


# ─── Page + board ───────────────────────────────────────────────────


@router.get("/eval-studio", response_class=HTMLResponse)
async def eval_studio_page(request: Request):
    return _template(request, "eval_studio/index.html", {
        "mnos": sorted({c["mno"] for c in req_tree.list_cells(_env_dir())}),
    })


@router.get("/api/eval-studio/samples", response_class=HTMLResponse)
async def sample_board(request: Request):
    try:
        samples = load_samples(_env_dir())
        load_error = ""
    except GoldenEvalError as exc:
        samples, load_error = [], f"{exc.code}: sample store unreadable"
    return _template(request, "eval_studio/_board.html", {
        "samples": samples,
        "load_error": load_error,
    })


# ─── Sample CRUD ────────────────────────────────────────────────────


@router.post("/api/eval-studio/sample", response_class=HTMLResponse)
async def sample_create(
    request: Request,
    query: str = Form(...),
    area: str = Form(""),
    created_by: str = Form(""),
):
    env_dir = _env_dir()
    sample = GoldenSample(
        sample_id=next_sample_id(env_dir),
        query=query.strip(),
        area=area.strip(),
        created_by=created_by.strip(),
    )
    try:
        save_sample(env_dir, sample)
    except GoldenEvalError as exc:
        return HTMLResponse(
            f'<div class="alert alert-danger small">{exc.code}: '
            f"sample not saved — check the query field.</div>"
        )
    return _template(request, "eval_studio/_editor.html",
                     _editor_ctx(request, sample))


def _load_or_error(sid: str) -> GoldenSample | HTMLResponse:
    path = sample_path(_env_dir(), sid)
    if not path.exists():
        return HTMLResponse(
            f'<div class="alert alert-warning small">Sample {sid} not found.</div>'
        )
    try:
        return load_sample(path)
    except GoldenEvalError as exc:
        return HTMLResponse(
            f'<div class="alert alert-danger small">{exc.code}: '
            f"sample {sid} unreadable.</div>"
        )


@router.get("/api/eval-studio/sample/{sid}", response_class=HTMLResponse)
async def sample_editor(request: Request, sid: str):
    sample = _load_or_error(sid)
    if isinstance(sample, HTMLResponse):
        return sample
    return _template(request, "eval_studio/_editor.html",
                     _editor_ctx(request, sample))


@router.post("/api/eval-studio/sample/{sid}/meta", response_class=HTMLResponse)
async def sample_meta(
    request: Request, sid: str, query: str = Form(...), area: str = Form(""),
):
    sample = _load_or_error(sid)
    if isinstance(sample, HTMLResponse):
        return sample
    sample.query = query.strip()
    sample.area = area.strip()
    save_sample(_env_dir(), sample)
    return _template(request, "eval_studio/_editor.html",
                     _editor_ctx(request, sample))


@router.post("/api/eval-studio/sample/{sid}/status", response_class=HTMLResponse)
async def sample_status(request: Request, sid: str, status: str = Form(...)):
    sample = _load_or_error(sid)
    if isinstance(sample, HTMLResponse):
        return sample
    previous = sample.status
    sample.status = status
    problems = validate_sample(sample)
    if problems:
        sample.status = previous
        return _template(request, "eval_studio/_editor.html", {
            **_editor_ctx(request, sample),
            "status_error": "; ".join(problems),
        })
    save_sample(_env_dir(), sample)
    return _template(request, "eval_studio/_editor.html",
                     _editor_ctx(request, sample))


@router.post("/api/eval-studio/sample/{sid}/delete", response_class=HTMLResponse)
async def sample_delete(request: Request, sid: str):
    if not is_admin(request):
        return HTMLResponse(
            '<div class="alert alert-warning small">Delete is admin-only.</div>',
            status_code=403,
        )
    path = sample_path(_env_dir(), sid)
    if path.exists():
        path.unlink()
    return await sample_board(request)


# ─── Ground-truth entries ───────────────────────────────────────────


@router.post("/api/eval-studio/sample/{sid}/gt/add", response_class=HTMLResponse)
async def gt_add(
    request: Request,
    sid: str,
    req_id: str = Form(...),
    mno: str = Form(""),
    release: str = Form(""),
    plan: str = Form(""),
    source: str = Form("direct"),
):
    sample = _load_or_error(sid)
    if isinstance(sample, HTMLResponse):
        return sample
    env_dir = _env_dir()
    req_id = req_id.strip()
    gt_error = ""
    matches = req_tree.find_req(env_dir, req_id)
    if not matches:
        gt_error = f"{req_id}: not found in any parsed cell (GEV-E001)"
    elif not (mno and release):
        # Direct entry without qualifiers: auto-qualify a unique match,
        # ask when ambiguous.
        if len(matches) == 1:
            m = matches[0]
            mno, release, plan = m["mno"], m["release"], m["plan"]
        else:
            cells = ", ".join(f"{m['mno']}/{m['release']}" for m in matches)
            gt_error = (
                f"{req_id}: found in {len(matches)} cells ({cells}) — "
                "pick one via the dropdowns"
            )
    if not gt_error:
        entry = GroundTruthEntry(
            req_id=req_id, mno=mno, release=release, plan=plan, source=source,
        )
        key = (entry.req_id, entry.mno, entry.release)
        if any((e.req_id, e.mno, e.release) == key for e in sample.ground_truth):
            gt_error = f"{req_id}: already in the ground-truth list"
        else:
            sample.ground_truth.append(entry)
            save_sample(env_dir, sample)
    return _template(request, "eval_studio/_editor.html", {
        **_editor_ctx(request, sample),
        "gt_error": gt_error,
    })


@router.post("/api/eval-studio/sample/{sid}/gt/remove", response_class=HTMLResponse)
async def gt_remove(
    request: Request,
    sid: str,
    req_id: str = Form(...),
    mno: str = Form(""),
    release: str = Form(""),
):
    sample = _load_or_error(sid)
    if isinstance(sample, HTMLResponse):
        return sample
    key = (req_id, mno, release)
    sample.ground_truth = [
        e for e in sample.ground_truth
        if (e.req_id, e.mno, e.release) != key
    ]
    save_sample(_env_dir(), sample)
    return _template(request, "eval_studio/_editor.html",
                     _editor_ctx(request, sample))


# ─── Picker cascade (MNO → Plan → Release → requirement list) ───────


@router.get("/api/eval-studio/picker/plans", response_class=HTMLResponse)
async def picker_plans(request: Request, mno: str = ""):
    plans = req_tree.plans_for_mno(_env_dir(), mno) if mno else {}
    return _template(request, "eval_studio/_picker_plans.html", {
        "mno": mno,
        "plans": plans,
    })


@router.get("/api/eval-studio/picker/releases", response_class=HTMLResponse)
async def picker_releases(request: Request, mno: str = "", plan: str = ""):
    releases: list[str] = []
    if mno and plan:
        releases = req_tree.plans_for_mno(_env_dir(), mno).get(plan, [])
    return _template(request, "eval_studio/_picker_releases.html", {
        # Latest release preselected — the common case.
        "releases": releases,
        "selected": releases[-1] if releases else "",
    })


@router.get("/api/eval-studio/picker/reqs", response_class=HTMLResponse)
async def picker_reqs(
    request: Request,
    sid: str = "",
    mno: str = "",
    plan: str = "",
    release: str = "",
    filter: str = "",
):
    rows: list[dict] = []
    if mno and plan and release:
        rows = req_tree.reqs_for_plan(_env_dir(), mno, release, plan)
        needle = filter.strip().lower()
        if needle:
            rows = [
                r for r in rows
                if needle in r.get("req_id", "").lower()
                or needle in r.get("title", "").lower()
            ]
    return _template(request, "eval_studio/_picker_reqs.html", {
        "sid": sid,
        "mno": mno,
        "plan": plan,
        "release": release,
        "rows": rows,
    })


# ─── Stage-1 preview ────────────────────────────────────────────────


@router.post("/api/eval-studio/sample/{sid}/preview", response_class=HTMLResponse)
async def stage1_preview(request: Request, sid: str):
    sample = _load_or_error(sid)
    if isinstance(sample, HTMLResponse):
        return sample
    stack = _stack_url()
    if not stack:
        return HTMLResponse(
            '<div class="alert alert-warning small">No sira-query stack '
            "configured (NORA_SIRA_QUERY_URLS).</div>"
        )
    from core.src.eval.golden_runner import run_stage1

    try:
        result = run_stage1(sample, stack)
    except GoldenEvalError as exc:
        return HTMLResponse(
            f'<div class="alert alert-danger small">{exc.code}: '
            f"preview failed — is the stack up?</div>"
        )
    return _template(request, "eval_studio/_preview.html", {
        "sample": sample,
        "result": result,
    })


# ─── Stage-2 curation chat ──────────────────────────────────────────


def _gt_context(env_dir: Path, sample: GoldenSample) -> str:
    """Assemble the ground-truth requirement texts for the curation chat
    from parse trees (no vector store needed). Deterministic order,
    capped at _CHAT_CONTEXT_MAX_CHARS.
    """
    parts: list[str] = []
    total = 0
    for entry in sample.ground_truth:
        matches = req_tree.find_req(env_dir, entry.req_id)
        if entry.mno and entry.release:
            matches = [
                m for m in matches
                if m["mno"] == entry.mno and m["release"] == entry.release
            ]
        for m in matches[:1]:
            block = (
                f"[{entry.req_id}] {m['title']}\n{m['text']}".strip() + "\n"
            )
            if total + len(block) > _CHAT_CONTEXT_MAX_CHARS:
                parts.append("(further requirement texts truncated)")
                return "\n".join(parts)
            parts.append(block)
            total += len(block)
    return "\n".join(parts) if parts else "(no ground-truth texts found)"


@router.post("/api/eval-studio/sample/{sid}/chat", response_class=HTMLResponse)
async def curation_chat(
    request: Request,
    sid: str,
    message: str = Form(...),
    history: str = Form("[]"),
):
    sample = _load_or_error(sid)
    if isinstance(sample, HTMLResponse):
        return sample
    from core.src.web.routes.query import _build_llm_from_env_or_default

    llm = _build_llm_from_env_or_default()
    if llm is None or getattr(llm, "_is_mock", False):
        return HTMLResponse(
            '<div class="alert alert-warning small">No real LLM configured — '
            "curation chat unavailable.</div>"
        )
    try:
        turns = json.loads(history) if history.strip() else []
    except json.JSONDecodeError:
        turns = []
    system = _CHAT_SYSTEM.format(context=_gt_context(_env_dir(), sample))
    convo = "".join(
        f"{'Expert' if t.get('role') == 'user' else 'Assistant'}: "
        f"{t.get('text', '')}\n\n"
        for t in turns
    )
    prompt = f"{convo}Expert: {message.strip()}\n\nAssistant:"
    try:
        answer = llm.complete(prompt, system=system, temperature=0.2)
    except Exception:
        logger.exception("curation chat LLM call failed")  # no content in log
        return HTMLResponse(
            '<div class="alert alert-danger small">LLM call failed — '
            "see server log.</div>"
        )
    return _template(request, "eval_studio/_chat_turn.html", {
        "sid": sid,
        "user_text": message.strip(),
        "assistant_text": answer.strip(),
        "turn_count": len(turns) + 2,
    })


@router.post("/api/eval-studio/sample/{sid}/golden", response_class=HTMLResponse)
async def save_golden(
    request: Request,
    sid: str,
    golden_response: str = Form(...),
    chat_turns: int = Form(0),
    model: str = Form(""),
):
    sample = _load_or_error(sid)
    if isinstance(sample, HTMLResponse):
        return sample
    sample.golden_response = golden_response.strip()
    sample.golden_meta = {
        "chat_turns": chat_turns,
        "model": model,
        "curated_at": "",  # stamped by save_sample's updated_at
    }
    save_sample(_env_dir(), sample)
    sample.golden_meta["curated_at"] = sample.updated_at
    save_sample(_env_dir(), sample)
    return _template(request, "eval_studio/_editor.html",
                     _editor_ctx(request, sample))
