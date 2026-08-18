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
import re
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


def _gt_panel_ctx(sample: GoldenSample, *, gt_error: str = "",
                  gt_info: str = "", gt_notices: list[str] | None = None) -> dict:
    """Context for the standalone ground-truth panel (`_gt_panel.html`).

    Returned by the gt/add, gt/add-bulk and gt/remove routes so only the GT
    panel is swapped — the picker column is left untouched (its selection and
    per-row state survive). `oob_count` drives the out-of-band GT-count badge
    refresh in the tab nav. `gt_notices` are subtle under-the-hood lines (e.g.
    a latest-revision auto-pick) shown muted, distinct from the success/warning
    alerts.
    """
    return {
        "sample": sample,
        "stack_url": _stack_url(),
        "oob_count": True,
        "gt_error": gt_error,
        "gt_info": gt_info,
        "gt_notices": gt_notices or [],
    }


# ─── Page + board ───────────────────────────────────────────────────


@router.get("/eval-studio", response_class=HTMLResponse)
def eval_studio_page(request: Request):
    return _template(request, "eval_studio/index.html", {
        "mnos": sorted({c["mno"] for c in req_tree.list_cells(_env_dir())}),
    })


@router.get("/api/eval-studio/samples", response_class=HTMLResponse)
def sample_board(request: Request, mno: str = ""):
    env_dir = _env_dir()
    try:
        samples = load_samples(env_dir)
        load_error = ""
    except GoldenEvalError as exc:
        samples, load_error = [], f"{exc.code}: sample store unreadable"
    if mno:
        samples = [s for s in samples if s.mno == mno]
    return _template(request, "eval_studio/_board.html", {
        "samples": samples,
        "load_error": load_error,
        "mnos": sorted({c["mno"] for c in req_tree.list_cells(env_dir)}),
        "sel_mno": mno,
    })


# ─── Sample CRUD ────────────────────────────────────────────────────


@router.post("/api/eval-studio/sample", response_class=HTMLResponse)
def sample_create(
    request: Request,
    query: str = Form(...),
    area: str = Form(""),
    created_by: str = Form(""),
    mno: str = Form(""),
):
    env_dir = _env_dir()
    sample = GoldenSample(
        sample_id=next_sample_id(env_dir),
        query=query.strip(),
        area=area.strip(),
        mno=mno.strip(),
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
    mno: str = Form(""),
):
    sample = _load_or_error(sid)
    if isinstance(sample, HTMLResponse):
        return sample
    sample.query = query.strip()
    sample.area = area.strip()
    sample.mno = mno.strip()
    save_sample(_env_dir(), sample)
    # Swap only the meta card so the active tab / scroll survive; refresh the
    # board (area shows there). The chat system prompt is rebuilt from the
    # query on the next Send, so no chat refresh is needed here.
    return _template(request, "eval_studio/_meta_card.html", {
        **_editor_ctx(request, sample), "board_refresh": True})


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
        # Partial (meta card + OOB promote slots) so the active tab survives;
        # the error renders in the meta card.
        return _template(request, "eval_studio/_status_result.html", {
            **_editor_ctx(request, sample),
            "status_error": "; ".join(problems),
        })
    save_sample(_env_dir(), sample)
    return _template(request, "eval_studio/_status_result.html",
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
    qualified = bool(mno and release)
    # One field, possibly many ids: a direct paste may carry a list separated
    # by commas / whitespace / newlines. Picker adds send a single id.
    raw_ids = [t for t in re.split(r"[\s,]+", req_id.strip()) if t]

    added = dupes = 0
    not_found: list[str] = []
    notices: list[str] = []
    existing = {(e.req_id, e.mno, e.release) for e in sample.ground_truth}

    for rid in raw_ids:
        # Picker adds arrive fully qualified — validate within that cell only;
        # unqualified direct pastes need the corpus-wide scan.
        matches = req_tree.find_req(env_dir, rid, mno, release)
        if not matches:
            not_found.append(rid)
            continue
        e_mno, e_release, e_plan = mno, release, plan
        if not qualified:
            # Direct entry without qualifiers: auto-qualify. A req_id found in
            # several releases (e.g. Mar vs Jun) resolves to the latest
            # revision — the expert wants the current one, with a note.
            m = matches[0] if len(matches) == 1 else req_tree.latest_match(matches)
            e_mno, e_release, e_plan = m["mno"], m["release"], m["plan"]
            if len(matches) > 1:
                cells = ", ".join(sorted({x["release"] for x in matches}))
                notices.append(
                    f"{rid}: matched {len(matches)} cells ({cells}) — "
                    f"added latest ({e_release})"
                )
        key = (rid, e_mno, e_release)
        if key in existing:
            dupes += 1
            continue
        sample.ground_truth.append(GroundTruthEntry(
            req_id=rid, mno=e_mno, release=e_release, plan=e_plan, source=source,
        ))
        existing.add(key)
        added += 1

    if added:
        save_sample(env_dir, sample)

    # Summary: additions/dupes as the success line; not-found as the warning.
    info_parts: list[str] = []
    if added:
        info_parts.append(f"Added {added}")
    if dupes:
        info_parts.append(f"{dupes} already present")
    gt_info = ", ".join(info_parts)
    gt_error = ""
    if not_found:
        gt_error = f"Not found: {', '.join(not_found)}"
    return _template(request, "eval_studio/_gt_panel.html",
                     _gt_panel_ctx(sample, gt_error=gt_error, gt_info=gt_info,
                                   gt_notices=notices))


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
        return _template(request, "eval_studio/_gt_panel.html",
                         _gt_panel_ctx(sample,
                                       gt_error="bulk add needs the picker's MNO/release selection"))
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
    return _template(request, "eval_studio/_gt_panel.html", _gt_panel_ctx(
        sample,
        gt_info=(
            f"Added {added} requirement(s)"
            + (f", {skipped} already present" if skipped else "")
        ) if (added or skipped) else "",
        gt_error="" if (added or skipped) else "No requirements selected",
    ))


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
    return _template(request, "eval_studio/_gt_panel.html",
                     _gt_panel_ctx(sample))


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


@router.get("/api/eval-studio/picker/jump", response_class=HTMLResponse)
def picker_jump(
    request: Request,
    sid: str = "",
    mno: str = "",
    plan: str = "",
    release: str = "",
):
    """Repopulate the whole picker pre-selected to a cell — driven by a
    ground-truth entry's locate button, so the expert can pull more sibling
    requirements from that plan without re-walking the cascade. Unknown /
    empty release falls back to the plan's latest (matches picker_releases).
    """
    env_dir = _env_dir()
    plans = req_tree.plans_for_mno(env_dir, mno) if mno else {}
    releases = plans.get(plan, []) if plan else []
    if releases and release not in releases:
        release = releases[-1]
    rows = (
        req_tree.reqs_for_plan(env_dir, mno, release, plan)
        if (mno and plan and release) else []
    )
    return _template(request, "eval_studio/_picker.html", {
        "mnos": sorted({c["mno"] for c in req_tree.list_cells(env_dir)}),
        "sid": sid,
        "sel_mno": mno,
        "sel_plan": plan,
        "sel_release": release,
        "plans": plans,
        "releases": releases,
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
    generated: str = Form(""),
):
    sample = _load_or_error(sid)
    if isinstance(sample, HTMLResponse):
        return sample
    new_text = golden_response.strip()
    prev_text = (sample.golden_response or "").strip()
    gen = generated.strip()
    # Flag a manual edit: accepting an in-session LLM draft verbatim is not an
    # edit; a tweak or a manual paste is. An unchanged re-save preserves the
    # prior flag (we can't re-judge without a fresh draft).
    if gen and new_text == gen:
        edited = False
    elif new_text == prev_text:
        edited = bool(sample.golden_meta.get("edited", False))
    else:
        edited = True
    sample.golden_response = new_text
    sample.golden_meta = {
        "chat_turns": chat_turns,
        "model": model,
        "curated_at": "",  # stamped by save_sample's updated_at
        "edited": edited,
    }
    save_sample(_env_dir(), sample)
    sample.golden_meta["curated_at"] = sample.updated_at
    if edited:
        sample.golden_meta["edited_at"] = sample.updated_at
    save_sample(_env_dir(), sample)
    # Swap only the golden card (keeps the expert on the Golden tab); OOB the
    # tab check badge.
    return _template(request, "eval_studio/_golden_card.html",
                     {"sample": sample, "oob": True})
