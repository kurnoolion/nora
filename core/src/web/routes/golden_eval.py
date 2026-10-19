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
    KIND_STANDARD,
    KIND_VERSION,
    KINDS,
    STATUSES,
    GoldenEvalError,
    GoldenSample,
    GroundTruthEntry,
    golden_root,
    is_relative_release,
    load_sample,
    load_samples,
    names_release,
    needs_review,
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


def _release_map():
    """The golden set's release map, or None (no map yet, or unreadable —
    authoring must not break on it; the runner fails loud instead)."""
    from core.src.eval.release_map import load_release_map

    try:
        return load_release_map(golden_root(_env_dir()))
    except GoldenEvalError:
        return None


def _as_relative(rmap, mno: str, release: str) -> str | None:
    """``N`` / ``N-<k>`` for a release the map lists for ``mno``."""
    rels = rmap.mnos.get(mno, []) if rmap else []
    if release not in rels:
        return None
    k = len(rels) - 1 - rels.index(release)
    return "N" if k == 0 else f"N-{k}"


def _resolved_release(rmap, entry: GroundTruthEntry) -> str:
    """The absolute release an entry points at ("" when a relative entry
    cannot resolve)."""
    if not is_relative_release(entry.release):
        return entry.release
    if rmap is None:
        return ""
    try:
        return rmap.resolve(entry.mno, entry.release)
    except GoldenEvalError:
        return ""


def _rel_on(sample: GoldenSample) -> bool:
    """Default for the relative toggle: on when the sample already uses
    relative releases (a version sample does not mix the two)."""
    return any(is_relative_release(e.release) for e in sample.ground_truth)


def _binding(rmap):
    """Template helper: how an entry's release reads — ``N → Apr2098`` for
    a relative entry, the label for an absolute one."""
    def bind(entry: GroundTruthEntry) -> str:
        if not is_relative_release(entry.release):
            return entry.release
        return f"{entry.release} → {_resolved_release(rmap, entry) or 'unresolved'}"
    return bind


def _qc_warnings(sample: GoldenSample, rmap) -> list[str]:
    """Authoring warnings for the release-handling rules. Advisory — they
    never block a save (validation does that)."""
    out: list[str] = []
    if sample.kind == KIND_STANDARD and names_release(sample.query):
        out.append(
            "The question names a release, so it is probably a version "
            "sample. As a standard sample it scores without the release, "
            "and the scope check reports the named release as off-N."
        )
    if any(is_relative_release(e.release) for e in sample.ground_truth):
        out.append(
            "Relative releases need serving to map relative wording "
            "(\"recent\", \"previous\") to N and N-1. Until it does, "
            "N-1 entries score as scope misses."
        )
        if rmap is None:
            out.append("No release map: relative entries cannot resolve "
                       "(GEV-E005 at run time).")
    if needs_review(sample):
        out.append(
            "A release move flagged this sample for review. Stage 2 skips "
            "it until the flag is cleared (Ground truth tab)."
        )
    return out


def _template(request: Request, name: str, ctx: dict):
    from core.src.web.app import _template_response

    return _template_response(request, name, ctx)


def _editor_ctx(request: Request, sample: GoldenSample) -> dict:
    rmap = _release_map()
    return {
        "sample": sample,
        "kinds": KINDS,
        "qc_warnings": _qc_warnings(sample, rmap),
        "bind": _binding(rmap),
        "has_map": rmap is not None,
        "rel_on": _rel_on(sample),
        "review_rows": _review_rows(sample),
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
    rmap = _release_map()
    return {
        "sample": sample,
        "bind": _binding(rmap),
        "has_map": rmap is not None,
        "rel_on": _rel_on(sample),
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
def sample_board(request: Request, mno: str = "", author: str = ""):
    env_dir = _env_dir()
    try:
        samples = load_samples(env_dir)
        load_error = ""
    except GoldenEvalError as exc:
        samples, load_error = [], f"{exc.code}: sample store unreadable"
    # Author dropdown lists every author in the store (computed before filtering
    # so a selection never empties its own option list).
    authors = sorted({s.created_by for s in samples if s.created_by})
    if mno:
        samples = [s for s in samples if s.mno == mno]
    if author:
        samples = [s for s in samples if s.created_by == author]
    return _template(request, "eval_studio/_board.html", {
        "samples": samples,
        "flagged": {s.sample_id for s in samples if needs_review(s)},
        "load_error": load_error,
        "mnos": sorted({c["mno"] for c in req_tree.list_cells(env_dir)}),
        "sel_mno": mno,
        "authors": authors,
        "sel_author": author,
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
    mno: str = Form(""), kind: str = Form(""),
):
    sample = _load_or_error(sid)
    if isinstance(sample, HTMLResponse):
        return sample
    sample.query = query.strip()
    sample.area = area.strip()
    sample.mno = mno.strip()
    previous_kind = sample.kind
    if kind:
        sample.kind = kind
    problems = validate_sample(sample)
    if problems and sample.kind != previous_kind:
        # A kind switch that the entries do not support (e.g. relative
        # releases on a standard sample): keep the old kind, say why.
        sample.kind = previous_kind
        return _template(request, "eval_studio/_meta_card.html", {
            **_editor_ctx(request, sample),
            "status_error": "Kind not changed: " + "; ".join(problems),
        })
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
    relative: str = Form(""),
):
    sample = _load_or_error(sid)
    if isinstance(sample, HTMLResponse):
        return sample
    env_dir = _env_dir()
    rmap = _release_map()
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
        # Case-insensitive (the eval matches ids under m3); the entry stores
        # the parse tree's own spelling.
        matches = req_tree.find_req(env_dir, rid, mno, release,
                                    ignore_case=True)
        if not matches:
            not_found.append(rid)
            continue
        canon = matches[0].get("req_id") or rid
        if canon != rid:
            notices.append(f"{rid}: stored as {canon} (the tree's spelling)")
            rid = canon
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
        e_release, note = _entry_release(sample, rmap, e_mno, e_release,
                                         relative)
        if note:
            notices.append(f"{rid}: {note}")
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


def _entry_release(sample: GoldenSample, rmap, mno: str, release: str,
                   relative: str) -> tuple[str, str]:
    """The release to store for a new entry, plus a notice. A version
    sample with the relative toggle on stores ``N`` / ``N-<k>`` when the
    map lists the release; everything else stores the label as picked."""
    if not (relative and sample.kind == KIND_VERSION):
        return release, ""
    ref = _as_relative(rmap, mno, release)
    if ref is None:
        return release, (f"{release} is not in the release map for {mno} — "
                         "stored as an absolute release")
    return ref, ""


@router.post("/api/eval-studio/sample/{sid}/gt/add-bulk", response_class=HTMLResponse)
def gt_add_bulk(
    request: Request,
    sid: str,
    req_ids: list[str] = Form([]),
    mno: str = Form(""),
    release: str = Form(""),
    plan: str = Form(""),
    relative: str = Form(""),
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
    stored, note = _entry_release(sample, _release_map(), mno, release,
                                  relative)
    existing = {(e.req_id, e.mno, e.release) for e in sample.ground_truth}
    added = skipped = 0
    for rid in req_ids:
        rid = rid.strip()
        key = (rid, mno, stored)
        if not rid or key in existing:
            skipped += bool(rid)
            continue
        sample.ground_truth.append(GroundTruthEntry(
            req_id=rid, mno=mno, release=stored, plan=plan, source="picker",
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
        gt_notices=[note] if note else [],
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
    req_id: str = "",
):
    """Repopulate the whole picker pre-selected to a cell — driven by a
    ground-truth entry's locate button, so the expert can pull more sibling
    requirements from that plan without re-walking the cascade. Unknown /
    empty release falls back to the plan's latest (matches picker_releases).

    When ``req_id`` is given the picker's filter box is pre-filled with it and
    the list is filtered to that requirement (clear the box to see siblings).
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
    needle = req_id.strip().lower()
    if needle and rows:
        # Same id/title match as picker_reqs so editing the box behaves the same.
        rows = [
            r for r in rows
            if needle in r.get("req_id", "").lower()
            or needle in r.get("title", "").lower()
        ]
    return _template(request, "eval_studio/_picker.html", {
        "mnos": sorted({c["mno"] for c in req_tree.list_cells(env_dir)}),
        "sid": sid,
        "sel_mno": mno,
        "sel_plan": plan,
        "sel_release": release,
        "plans": plans,
        "releases": releases,
        "rows": rows,
        "sel_filter": req_id,
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
        result = run_stage1(sample, stack, release_map=_release_map())
    except GoldenEvalError as exc:
        hint = ("relative release cannot resolve — check the release map"
                if exc.code == "GEV-E005" else "is the stack up?")
        return HTMLResponse(
            f'<div class="alert alert-danger small">{exc.code}: '
            f"preview failed — {hint}</div>"
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
    rmap = _release_map()
    for entry in sample.ground_truth:
        # Qualified entries scope the scan to their cell; only legacy
        # unqualified entries pay a corpus-wide lookup. A relative entry
        # reads its resolved release's text.
        matches = req_tree.find_req(
            env_dir, entry.req_id, entry.mno, _resolved_release(rmap, entry),
            ignore_case=True,
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

    # Curation chat is not the Ask flow: it must not follow an asker's endpoint
    # choice, and a golden response curated against whichever endpoint happened
    # to be the roster default would not be reproducible. use_roster=False is
    # required — passing no provider_id would still resolve the default entry.
    llm = _build_llm_from_env_or_default(use_roster=False)
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
    # A release-move review flag survives a re-save: clearing it is an
    # explicit expert act in the review panel (field-found risk — the
    # meta was rebuilt wholesale, so saving the response silently dropped
    # the flag and the Stage-2 skip with it).
    review = sample.golden_meta.get("review")
    sample.golden_meta = {
        "chat_turns": chat_turns,
        "model": model,
        "curated_at": "",  # stamped by save_sample's updated_at
        "edited": edited,
    }
    if review:
        sample.golden_meta["review"] = review
    save_sample(_env_dir(), sample)
    sample.golden_meta["curated_at"] = sample.updated_at
    if edited:
        sample.golden_meta["edited_at"] = sample.updated_at
    save_sample(_env_dir(), sample)
    # Swap only the golden card (keeps the expert on the Golden tab); OOB the
    # tab check badge.
    return _template(request, "eval_studio/_golden_card.html",
                     {"sample": sample, "oob": True})


# ─── Release-move review ────────────────────────────────────────────


_REVIEW_CLASSES = ("changed", "absent", "unverified")


def _review_rows(sample: GoldenSample) -> list[dict]:
    """One block per flagged MNO — built from ``golden_meta.review`` itself,
    so a block (and its clear button) stays when every entry has been
    ruled on (field-found: blocks were grouped from the entry rows, so
    ruling the last entry removed the only way to clear the flag and left
    the sample stuck out of Stage 2). Each block lists its still-open
    entries: class plus the requirement text in the old and the new
    release (case-insensitive, as the text check looks ids up)."""
    review = sample.golden_meta.get("review") or {}
    if not review:
        return []
    env_dir = _env_dir()
    blocks: list[dict] = []
    for mno, r in sorted(review.items()):
        old_rel = (r.get("from") or [""])[0]
        new_rel = r.get("to", "")
        ids = [(cls, rid) for cls in _REVIEW_CLASSES for rid in r.get(cls, [])]
        rows: list[dict] = []
        blocks.append({"mno": mno, "from": old_rel, "to": new_rel,
                       "relative": bool(r.get("relative")), "rows": rows})
        for cls, rid in ids:
            def text(rel: str) -> str:
                if not rel:
                    return ""
                m = req_tree.find_req(env_dir, rid, mno, rel, ignore_case=True)
                return f"{m[0]['title']}\n{m[0]['text']}".strip() if m else ""
            rows.append({
                "req_id": rid, "cls": cls,
                "old_text": text(old_rel), "new_text": text(new_rel),
            })
    return blocks


def _drop_from_review(sample: GoldenSample, mno: str, req_id: str) -> None:
    r = (sample.golden_meta.get("review") or {}).get(mno)
    if not r:
        return
    for cls in _REVIEW_CLASSES:
        r[cls] = [x for x in r.get(cls, [])
                  if x.casefold() != req_id.casefold()]


@router.post("/api/eval-studio/sample/{sid}/review/entry",
             response_class=HTMLResponse)
def review_entry(
    request: Request,
    sid: str,
    mno: str = Form(...),
    req_id: str = Form(...),
    action: str = Form(...),
    new_id: str = Form(""),
):
    """Rule on one flagged entry: ``keep`` (move it to the new release),
    ``replace`` (a successor id in the new release) or ``remove``. The
    sample's flag stays until the expert clears it — the golden response
    still needs a look."""
    sample = _load_or_error(sid)
    if isinstance(sample, HTMLResponse):
        return sample
    r = (sample.golden_meta.get("review") or {}).get(mno)
    if not r:
        return _template(request, "eval_studio/_editor.html",
                         _editor_ctx(request, sample))
    to = r.get("to", "")
    entry = next((e for e in sample.ground_truth if e.mno == mno
                  and e.req_id.casefold() == req_id.casefold()), None)
    error = ""
    if entry is None and action != "remove":
        error = f"{req_id} is no longer in the ground truth"
    elif action == "keep":
        entry.release = to
    elif action == "remove":
        sample.ground_truth = [e for e in sample.ground_truth if e is not entry]
    elif action == "replace":
        m = req_tree.find_req(_env_dir(), new_id.strip(), mno, to,
                              ignore_case=True)
        if not new_id.strip() or not m:
            error = f"{new_id or '(empty)'} not found in {mno} {to}"
        else:
            entry.req_id, entry.release = m[0]["req_id"], to
            entry.plan = m[0].get("plan", entry.plan)
    else:
        error = f"unknown action {action!r}"
    if not error:
        _drop_from_review(sample, mno, req_id)
        problems = validate_sample(sample)
        if problems:
            error = "; ".join(problems)
        else:
            save_sample(_env_dir(), sample)
    ctx = _editor_ctx(request, sample)
    ctx["review_error"] = error
    return _template(request, "eval_studio/_editor.html", ctx)


@router.post("/api/eval-studio/sample/{sid}/review/clear",
             response_class=HTMLResponse)
def review_clear(request: Request, sid: str, mno: str = Form(...)):
    """Clear one MNO's flag — the expert confirms the entries and the
    golden response hold for the new release. Stage 2 judges the sample
    again once no MNO is flagged."""
    sample = _load_or_error(sid)
    if isinstance(sample, HTMLResponse):
        return sample
    review = dict(sample.golden_meta.get("review") or {})
    review.pop(mno, None)
    if review:
        sample.golden_meta["review"] = review
    else:
        sample.golden_meta.pop("review", None)
    save_sample(_env_dir(), sample)
    return _template(request, "eval_studio/_editor.html",
                     _editor_ctx(request, sample))
