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
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.src.web.enrich_overlay_store import EDIT_OPS, EnrichOverlayStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/enrich-review")


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


@router.post("/edit")
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


@router.get("/labels")
def labels() -> dict[str, Any]:
    store = _store()
    return {"disabled": sorted(store.disabled_labels()),
            "counts": store.label_counts()}


class LabelToggle(BaseModel):
    label: str
    disabled: bool


@router.post("/labels/toggle")
def label_toggle(req: LabelToggle) -> dict[str, Any]:
    store = _store()
    disabled = store.set_label_disabled(req.label, req.disabled)
    return {"disabled": sorted(disabled)}


class LabelDelete(BaseModel):
    label: str


@router.post("/labels/delete")
def label_delete(req: LabelDelete) -> dict[str, Any]:
    """Bulk cleanup once a prompt fix lands (D-DRAFT-5)."""
    store = _store()
    removed = store.delete_label(req.label)
    return {"label": req.label, "records_removed": removed,
            "counts": store.label_counts()}


@router.get("/reasons")
def reasons() -> dict[str, Any]:
    return {"categories": _store().reason_categories()}


class ReasonAdd(BaseModel):
    category: str


@router.post("/reasons")
def reason_add(req: ReasonAdd) -> dict[str, Any]:
    if not req.category.strip():
        raise HTTPException(status_code=422, detail="category is required")
    return {"categories": _store().add_reason_category(req.category)}
