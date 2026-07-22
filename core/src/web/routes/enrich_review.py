"""Enrichment-review edit API (strand sira-enrichment-review, slice 2).

The web app is the overlay's sole WRITER (D-DRAFT-4): these endpoints
mutate `<corrections_root>/sira-enrich/` via `EnrichOverlayStore`. Reads of
cell data (req text, LLM words, held verdicts) are proxied from sira-query
and land with the page route in a later slice; this module is the
persistence surface only.

All endpoints return JSON. A missing `corrections_root` config surfaces as
HTTP 503 with a pointed message — a misconfigured volume must be loud, not
a silent no-op (fail-loud house rule)."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from core.src.web.enrich_overlay_store import EDIT_OPS, EnrichOverlayStore

logger = logging.getLogger(__name__)

router = APIRouter()
api = APIRouter(prefix="/api/enrich-review")

# sira-query endpoints (same wiring as the test page's SIRA lane). Apply
# loops over every URL in NORA_SIRA_QUERY_URLS (comma-separated, e.g. both
# a/b stacks); reads use the first.
_SIRA_URLS = [u.strip().rstrip("/") for u in os.getenv(
    "NORA_SIRA_QUERY_URLS",
    os.getenv("NORA_SIRA_QUERY_URL", "http://127.0.0.1:8040"),
).split(",") if u.strip()]


def _service_get(path: str, params: dict | None = None) -> dict:
    """GET from the primary sira-query; service errors surface as 502."""
    try:
        r = httpx.get(f"{_SIRA_URLS[0]}{path}", params=params or {}, timeout=15.0)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502,
                            detail=f"sira-query unreachable: {exc}") from exc


def _store() -> EnrichOverlayStore:
    from core.src.web.app import config  # late import — app wiring order
    store = EnrichOverlayStore(config.corrections_root)
    if not store.enabled:
        raise HTTPException(
            status_code=503,
            detail="corrections_root is not configured (config/web.json "
                   "corrections_root, $NORA_CORRECTIONS_ROOT, or config/env.json) "
                   "— the enrichment-review surface is disabled.")
    return store


# ── Page + HTMX partials ───────────────────────────────────────────


def _row_view(service_row: dict, entry: dict | None,
              disabled: set[str], viewed_release: str) -> dict:
    """Merge a service row with the CURRENT overlay entry into template
    state. The web side always renders its own latest edits (the service's
    effective view lags until Apply/reload) — D-DRAFT-4."""
    entry = entry or {}
    active = lambda rec: (rec.get("label") or "") not in disabled or not rec.get("label")  # noqa: E731
    removes = {r["word"]: r for r in entry.get("remove", []) if active(r)}
    adds = [r for r in entry.get("add", []) if active(r)]
    sup = entry.get("suppress_all")
    suppressed = bool(isinstance(sup, dict) and sup.get("value") and active(sup))

    llm_words = service_row.get("llm_words", [])
    chips = [{"word": w, "removed": w in removes,
              "record": removes.get(w)} for w in llm_words]
    add_words = {r["word"] for r in adds}
    # held: service verdicts, filtered to records still present with a
    # non-current origin (discard/reaffirm must vanish instantly)
    still = {("remove", w) for w in removes} \
        | {("add", r["word"]) for r in adds} \
        | ({("suppress_all", "")} if isinstance(sup, dict) else set())
    held = [h for h in service_row.get("held", [])
            if (h.get("direction"), h.get("word", "")) in still
            and (h.get("origin") or {}).get("release", "") != viewed_release]

    return {"req_id": service_row.get("req_id", ""),
            # ids like "doc:..." / "section:..." are invalid in CSS selectors
            # (hx-target) — sanitize for DOM use, keep req_id verbatim in forms
            "dom_id": re.sub(r"[^A-Za-z0-9_-]", "_", service_row.get("req_id", "")),
            "text": service_row.get("text", ""),
            "plan": service_row.get("plan", ""),
            "chips": chips,
            "adds": [r for r in adds if r["word"] not in removes],
            "suppressed": suppressed,
            "held": held,
            "n_llm": len(llm_words),
            "n_added": len(add_words - set(removes))}


def _cell_mno_release(cell: str) -> tuple[str, str]:
    if "__" not in cell:
        raise HTTPException(status_code=422, detail=f"bad cell name: {cell}")
    mno, release = cell.split("__", 1)
    return mno, release


def _pending_ctx(cell: str, store: EnrichOverlayStore,
                 loaded_at: float) -> dict:
    mno, _ = _cell_mno_release(cell)
    pending = store.overlay_mtime(mno) > loaded_at > 0
    return {"cell": cell, "pending": pending, "loaded_at": loaded_at}


@router.get("/enrichment-review", response_class=HTMLResponse)
async def page(request: Request):
    from core.src.web.app import _template_response
    return _template_response(request, "enrich_review/index.html", {
        "categories": _store().reason_categories(),
    })


@api.get("/cells")
def cells() -> dict[str, Any]:
    """Proxied cell list for the MNO/Release cascade."""
    return _service_get("/cells")


@api.get("/plans")
def plans(cell: str) -> dict[str, Any]:
    return _service_get(f"/cells/{cell}/plans")


# rows rendered per chunk — the next chunk lazy-loads when its sentinel
# scrolls into view, so 400+-req plans never freeze the browser
_TABLE_PAGE = 100


@api.get("/table", response_class=HTMLResponse)
async def table(request: Request, cell: str, plan: str = "", offset: int = 0):
    """Server-rendered rows: service data ⊕ current overlay. `offset` > 0
    returns a rows-only fragment (the infinite-scroll continuation)."""
    from core.src.web.app import _template_response
    store = _store()
    mno, release = _cell_mno_release(cell)
    data = _service_get(f"/cells/{cell}/enrichments", {"plan": plan})
    all_rows = data.get("rows", [])
    total = len(all_rows)
    offset = max(0, offset)
    overlay = store.get_overlay(mno)
    disabled = store.disabled_labels()
    rows = [_row_view(r, overlay.get(r["req_id"]), disabled, release)
            for r in all_rows[offset:offset + _TABLE_PAGE]]
    ctx = {
        "cell": cell, "plan": plan, "rows": rows,
        "categories": store.reason_categories(),
        "total": total, "shown": offset + len(rows),
        "next_offset": (offset + _TABLE_PAGE
                        if offset + _TABLE_PAGE < total else None),
    }
    if offset:
        return _template_response(request, "enrich_review/_rows_page.html", ctx)
    return _template_response(request, "enrich_review/_table.html", {
        **ctx, **_pending_ctx(cell, store, data.get("loaded_at", 0.0)),
    })


@api.post("/row-edit", response_class=HTMLResponse)
async def row_edit(request: Request,
                   cell: str = Form(...), plan: str = Form(""),
                   req_id: str = Form(...), op: str = Form(...),
                   word: str = Form(""), words: str = Form(""),
                   direction: str = Form(""),
                   label: str = Form(""), reason_category: str = Form(""),
                   reason_note: str = Form(""), by: str = Form("")):
    """One chip/box interaction → store edit → re-rendered row partial."""
    from core.src.web.app import _template_response
    if op not in EDIT_OPS:
        raise HTTPException(status_code=422, detail=f"unknown op: {op}")
    store = _store()
    mno, release = _cell_mno_release(cell)

    # an enrichment is ONE free-form phrase (spaces/punctuation legal —
    # "attach retry timer, per plan" is a single enrichment); never split
    phrase = (words or word).strip()
    word_list = [phrase] if phrase else []
    pairs = ([{"direction": direction or "remove", "word": word}]
             if op in ("reaffirm", "discard") else [])
    if op in ("reaffirm", "discard") and direction == "__all_held__":
        # act on every held record of the req (the banner's bulk buttons):
        svc = _service_get(f"/cells/{cell}/enrichments",
                           {"req_id": req_id})
        rows = svc.get("rows", [])
        pairs = [{"direction": h.get("direction"), "word": h.get("word", "")}
                 for h in (rows[0].get("held", []) if rows else [])]

    store.edit(mno, req_id, op, words=word_list, pairs=pairs, label=label,
               reason={"category": reason_category, "note": reason_note},
               by=by, origin_release=release)

    svc = _service_get(f"/cells/{cell}/enrichments", {"req_id": req_id})
    rows = svc.get("rows", [])
    if not rows:
        raise HTTPException(status_code=404, detail=f"unknown req: {req_id}")
    view = _row_view(rows[0], store.get_entry(mno, req_id),
                     store.disabled_labels(), release)
    return _template_response(request, "enrich_review/_row.html", {
        "cell": cell, "plan": plan, "row": view, "oob_pending": True,
        "categories": store.reason_categories(),
        **_pending_ctx(cell, store, svc.get("loaded_at", 0.0))})


@api.get("/export")
def export(label: str = "", mno: str = "", mode: str = "report",
           download: int = 0):
    """The label x category pivot report / prompt-fix scorecard
    (D-DRAFT-5). Plans + current LLM words come from each MNO's LATEST
    loaded release; report mode degrades gracefully when sira-query is
    down, scorecard mode requires it (it measures against live output)."""
    from datetime import datetime, timezone

    from fastapi.responses import PlainTextResponse

    from core.src.extraction.release_key import release_order_key
    from core.src.web.enrich_report import build_report, build_scorecard

    if mode not in ("report", "scorecard"):
        raise HTTPException(status_code=422, detail=f"unknown mode: {mode}")
    store = _store()
    mnos = [mno] if mno else store.list_mnos()
    overlays = {m: store.get_overlay(m) for m in mnos}

    # latest loaded release per MNO -> plans + current llm words
    plans: dict = {}
    current_llm: dict = {}
    service_note = ""
    try:
        cells = _service_get("/cells").get("cells", [])
        latest: dict[str, str] = {}
        for c in cells:
            m, rel = c.get("mno", ""), c.get("release", "")
            if m in overlays and (m not in latest or
                    (release_order_key(rel) or (0, 0)) >
                    (release_order_key(latest[m]) or (0, 0))):
                latest[m] = rel
        for m, rel in latest.items():
            data = _service_get(f"/cells/{m}__{rel}/enrichments")
            for row in data.get("rows", []):
                key = (m, row["req_id"])
                plans[key] = row.get("plan", "")
                current_llm[key] = set(row.get("llm_words", []))
    except HTTPException as exc:
        if mode == "scorecard":
            raise
        service_note = f"sira-query unavailable — plan counts omitted ({exc.detail})"

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    if mode == "report":
        text = build_report(overlays, label=label, mno=mno,
                            disabled=store.disabled_labels(), plans=plans,
                            generated=generated, service_note=service_note)
    else:
        text = build_scorecard(overlays, label=label, mno=mno,
                               current_llm=current_llm, generated=generated)
    headers = {}
    if download:
        fname = f"enrich-{mode}-{generated[:10]}.txt"
        headers["Content-Disposition"] = f'attachment; filename="{fname}"'
    return PlainTextResponse(text, headers=headers)


@api.get("/pending", response_class=HTMLResponse)
async def pending(request: Request, cell: str):
    from core.src.web.app import _template_response
    store = _store()
    data = _service_get(f"/cells/{cell}/enrichments", {"req_id": "__none__"})
    return _template_response(request, "enrich_review/_pending.html",
                              _pending_ctx(cell, store, data.get("loaded_at", 0.0)))


@api.post("/apply", response_class=HTMLResponse)
async def apply(request: Request, cell: str = Form(...)):
    """The expert's Apply: reload the cell on EVERY configured sira-query
    (both stacks when a/b are live) — D-DRAFT-4 self-service loop."""
    from core.src.web.app import _template_response
    store = _store()
    results, loaded_at = [], 0.0
    for base in _SIRA_URLS:
        try:
            r = httpx.post(f"{base}/cells/{cell}/reload", timeout=120.0)
            r.raise_for_status()
            loaded_at = max(loaded_at, r.json().get("loaded_at", 0.0))
            results.append(f"{base}: ok")
        except httpx.HTTPError as exc:
            results.append(f"{base}: FAILED ({exc})")
    ctx = _pending_ctx(cell, store, loaded_at)
    ctx["apply_results"] = results
    return _template_response(request, "enrich_review/_pending.html", ctx)


class EditRequest(BaseModel):
    mno: str
    req_id: str
    op: str
    words: list[str] = Field(default_factory=list)
    pairs: list[dict] = Field(default_factory=list)   # reaffirm/discard refs
    label: str = ""
    reason: dict = Field(default_factory=dict)        # {category, note}
    by: str = ""
    origin_release: str = ""                          # release being VIEWED


@api.post("/edit")
def edit(req: EditRequest) -> dict[str, Any]:
    if req.op not in EDIT_OPS:
        raise HTTPException(status_code=422, detail=f"unknown op: {req.op}")
    if not req.mno or not req.req_id:
        raise HTTPException(status_code=422, detail="mno and req_id are required")
    store = _store()
    entry = store.edit(
        req.mno, req.req_id, req.op,
        words=req.words, pairs=req.pairs, label=req.label,
        reason=req.reason, by=req.by, origin_release=req.origin_release)
    return {"mno": req.mno, "req_id": req.req_id, "entry": entry,
            "overlay_mtime": store.overlay_mtime(req.mno)}


@api.get("/labels")
def labels() -> dict[str, Any]:
    store = _store()
    return {"disabled": sorted(store.disabled_labels()),
            "counts": store.label_counts()}


class LabelToggle(BaseModel):
    label: str
    disabled: bool


@api.post("/labels/toggle")
def label_toggle(req: LabelToggle) -> dict[str, Any]:
    store = _store()
    disabled = store.set_label_disabled(req.label, req.disabled)
    return {"disabled": sorted(disabled)}


class LabelDelete(BaseModel):
    label: str


@api.post("/labels/delete")
def label_delete(req: LabelDelete) -> dict[str, Any]:
    """Bulk cleanup once a prompt fix lands (D-DRAFT-5)."""
    store = _store()
    removed = store.delete_label(req.label)
    return {"label": req.label, "records_removed": removed,
            "counts": store.label_counts()}


@api.get("/reasons")
def reasons() -> dict[str, Any]:
    return {"categories": _store().reason_categories()}


class ReasonAdd(BaseModel):
    category: str


@api.post("/reasons")
def reason_add(req: ReasonAdd) -> dict[str, Any]:
    if not req.category.strip():
        raise HTTPException(status_code=422, detail="category is required")
    return {"categories": _store().add_reason_category(req.category)}

router.include_router(api)
