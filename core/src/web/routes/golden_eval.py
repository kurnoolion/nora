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

Team-mode: the page family is whitelisted for gated experts. Draft
samples may be deleted by any expert (UI-confirmed); deleting a promoted
(stage1-ready / golden-ready) sample is admin-only.
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
    "You are helping a telecom requirements expert craft the ideal reference "
    "(golden) answer for this evaluation question:\n\n"
    "QUESTION: {query}\n\n"
    "How to answer:\n"
    "- Ground every statement in the requirement texts below; do not invent "
    "requirements or bring in outside knowledge.\n"
    "- Answer the question directly and cover every provided requirement "
    "that bears on it; note anything the question asks that the "
    "requirements do not address.\n"
    "- Reference requirement ids inline so coverage is checkable.\n"
    "- Where requirements differ across MNOs or releases, state the "
    "difference explicitly.\n"
    "- Plain prose, no filler — the answer is judged on content, not "
    "style.\n\n"
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
        # Shown read-only in the curation chat so experts see exactly what
        # the LLM is told; the same string is rebuilt at each /chat call.
        "chat_system": _CHAT_SYSTEM.format(
            query=sample.query, context=_gt_context(_env_dir(), sample),
        ),
    }


# ─── Page + board ───────────────────────────────────────────────────


@router.get("/eval-studio", response_class=HTMLResponse)
def eval_studio_page(request: Request):
    return _template(request, "eval_studio/index.html", {
        "mnos": sorted({c["mno"] for c in req_tree.list_cells(_env_dir())}),
    })


@router.get("/api/eval-studio/samples", response_class=HTMLResponse)
def sample_board(request: Request):
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
def sample_create(
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
def sample_editor(request: Request, sid: str):
    sample = _load_or_error(sid)
    if isinstance(sample, HTMLResponse):
        return sample
    return _template(request, "eval_studio/_editor.html",
                     _editor_ctx(request, sample))


@router.post("/api/eval-studio/sample/{sid}/meta", response_class=HTMLResponse)
def sample_meta(
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
def sample_status(request: Request, sid: str, status: str = Form(...)):
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
def sample_delete(request: Request, sid: str):
    path = sample_path(_env_dir(), sid)
    if path.exists():
        try:
            status = load_sample(path).status
        except GoldenEvalError:
            status = ""  # unreadable file — cleanup is an admin call
        if status != "draft" and not is_admin(request):
            return HTMLResponse(
                '<div class="alert alert-warning small">Only draft samples '
                "can be deleted — this one is promoted; ask an admin.</div>",
                status_code=403,
            )
        path.unlink()
    return sample_board(request)


# ─── Ground-truth entries ───────────────────────────────────────────


@router.post("/api/eval-studio/sample/{sid}/gt/add", response_class=HTMLResponse)
def gt_add(
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
    # Picker adds arrive fully qualified — validate within that cell only;
    # unqualified direct pastes need the corpus-wide scan.
    matches = req_tree.find_req(env_dir, req_id, mno, release)
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


@router.post("/api/eval-studio/sample/{sid}/gt/add-bulk", response_class=HTMLResponse)
def gt_add_bulk(
    request: Request,
    sid: str,
    req_ids: list[str] = Form([]),
    mno: str = Form(""),
    release: str = Form(""),
    plan: str = Form(""),
):
    """Add every selected picker row in one shot. The ids come from the
    picker's own plan listing (parse trees), so per-id existence checks
    are redundant — only the duplicate guard applies.
    """
    sample = _load_or_error(sid)
    if isinstance(sample, HTMLResponse):
        return sample
    if not (mno and release):
        return _template(request, "eval_studio/_editor.html", {
            **_editor_ctx(request, sample),
            "gt_error": "bulk add needs the picker's MNO/release selection",
        })
    existing = {(e.req_id, e.mno, e.release) for e in sample.ground_truth}
    added = skipped = 0
    for rid in req_ids:
        rid = rid.strip()
        key = (rid, mno, release)
        if not rid or key in existing:
            skipped += bool(rid)
            continue
        sample.ground_truth.append(GroundTruthEntry(
            req_id=rid, mno=mno, release=release, plan=plan, source="picker",
        ))
        existing.add(key)
        added += 1
    if added:
        save_sample(_env_dir(), sample)
    return _template(request, "eval_studio/_editor.html", {
        **_editor_ctx(request, sample),
        "gt_info": (
            f"Added {added} requirement(s)"
            + (f", {skipped} already present" if skipped else "")
        ) if (added or skipped) else "",
        "gt_error": "" if (added or skipped) else "No requirements selected",
    })


@router.post("/api/eval-studio/sample/{sid}/gt/remove", response_class=HTMLResponse)
def gt_remove(
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
def picker_plans(request: Request, mno: str = ""):
    plans = req_tree.plans_for_mno(_env_dir(), mno) if mno else {}
    return _template(request, "eval_studio/_picker_plans.html", {
        "mno": mno,
        "plans": plans,
    })


@router.get("/api/eval-studio/picker/releases", response_class=HTMLResponse)
def picker_releases(request: Request, mno: str = "", plan: str = ""):
    releases: list[str] = []
    if mno and plan:
        releases = req_tree.plans_for_mno(_env_dir(), mno).get(plan, [])
    return _template(request, "eval_studio/_picker_releases.html", {
        # Latest release preselected — the common case.
        "releases": releases,
        "selected": releases[-1] if releases else "",
    })


@router.get("/api/eval-studio/picker/reqs", response_class=HTMLResponse)
def picker_reqs(
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
def stage1_preview(request: Request, sid: str):
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
        # Qualified entries scope the scan to their cell; only legacy
        # unqualified entries pay a corpus-wide lookup.
        matches = req_tree.find_req(
            env_dir, entry.req_id, entry.mno, entry.release,
        )
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


_CHAT_KICKOFF = "Draft the golden answer."


@router.post("/api/eval-studio/sample/{sid}/chat", response_class=HTMLResponse)
def curation_chat(
    request: Request,
    sid: str,
    message: str = Form(""),
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
    message = message.strip()
    if not message:
        if turns:
            # Refinement without guidance is a no-op ask — require text.
            return HTMLResponse(
                '<div class="alert alert-warning small mb-1">Type what to '
                "change — refinements need guidance.</div>"
            )
        # First turn: the system prompt carries the question and the
        # rules, so a bare Send kicks off the initial draft.
        message = _CHAT_KICKOFF
    system = _CHAT_SYSTEM.format(
        query=sample.query, context=_gt_context(_env_dir(), sample),
    )
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
def save_golden(
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
