"""Resolve-review routes — table-based cross-reference resolution review UI."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from core.src.resolver.resolve_review import generate_compact_report

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/resolve-review", tags=["resolve-review"])

_TEXT_PREVIEW = 220  # chars of source requirement text to show inline


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _resolve_dir(env_dir_path: Path) -> Path:
    return env_dir_path / "out" / "resolve"


def _parse_dir(env_dir_path: Path) -> Path:
    return env_dir_path / "out" / "parse"


def _review_dir(env_dir_path: Path) -> Path:
    return env_dir_path / "reports" / "resolve_review"


def _list_docs(env_dir_path: Path) -> list[str]:
    d = _resolve_dir(env_dir_path)
    if not d.is_dir():
        return []
    return sorted(p.stem.replace("_xrefs", "") for p in d.glob("*_xrefs.json"))


def _load_or_default_review(review_dir: Path, doc_id: str) -> dict[str, Any]:
    p = review_dir / f"{doc_id}_resolve_review.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to load resolve review %s: %s", p, exc)
    return {
        "doc_id": doc_id,
        "reviewer": "",
        "review_date": str(date.today()),
        "overall_verdict": "",
        "corrections": {
            "internal_false_broken": [],
            "internal_wrong_target": [],
            "cross_plan_wrong_id": [],
            "standards_wrong_spec": [],
        },
        "notes": "",
    }


# ---------------------------------------------------------------------------
# Data assembly
# ---------------------------------------------------------------------------

def _build_req_index(env_dir_path: Path, doc_id: str) -> dict[str, dict[str, str]]:
    """Return req_id -> {text, section, title} from the parsed tree."""
    tree_path = _parse_dir(env_dir_path) / f"{doc_id}_tree.json"
    if not tree_path.exists():
        return {}
    try:
        data = json.loads(tree_path.read_text(encoding="utf-8"))
        index: dict[str, dict[str, str]] = {}
        for req in data.get("requirements", []):
            rid = req.get("req_id", "")
            if rid:
                text = (req.get("text") or "").strip()
                index[rid] = {
                    "text": text[:_TEXT_PREVIEW] + ("…" if len(text) > _TEXT_PREVIEW else ""),
                    "section": req.get("section_number", ""),
                    "title": req.get("title", ""),
                }
        return index
    except Exception as exc:
        logger.warning("Failed to build req index for %s: %s", doc_id, exc)
        return {}


def _build_ref_rows(
    xrefs: dict[str, Any],
    req_index: dict[str, dict[str, str]],
    review: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Build enriched ref rows for each of the three ref types.

    Each row has the original resolver fields plus:
      source_text, source_title  — from the requirement tree
      review_status              — 'accepted' | 'corrected' | ''
      correction                 — the matching correction entry, if any
    """
    corr = review.get("corrections", {})
    fb_targets  = {e["target_req_id"] for e in corr.get("internal_false_broken", []) if "target_req_id" in e}
    wt_by_src   = {e["source_req_id"]: e for e in corr.get("internal_wrong_target", []) if "source_req_id" in e}
    wp_by_src   = {e["source_req_id"]: e for e in corr.get("cross_plan_wrong_id", []) if "source_req_id" in e}
    ws_by_src   = {e["source_req_id"]: e for e in corr.get("standards_wrong_spec", []) if "source_req_id" in e}

    def _enrich(ref: dict[str, Any]) -> dict[str, Any]:
        src_id = ref.get("source_req_id", "")
        src_info = req_index.get(src_id, {})
        return {**ref, "source_text": src_info.get("text", ""), "source_title": src_info.get("title", "")}

    internal_rows = []
    for r in xrefs.get("internal_refs", []):
        row = _enrich(r)
        tgt = r.get("target_req_id", "")
        src = r.get("source_req_id", "")
        if tgt in fb_targets:
            row["review_status"] = "corrected"
            row["correction"] = next((e for e in corr.get("internal_false_broken", []) if e.get("target_req_id") == tgt), {})
        elif src in wt_by_src:
            row["review_status"] = "corrected"
            row["correction"] = wt_by_src[src]
        else:
            row["review_status"] = ""
            row["correction"] = {}
        internal_rows.append(row)

    cross_plan_rows = []
    for r in xrefs.get("cross_plan_refs", []):
        row = _enrich(r)
        src = r.get("source_req_id", "")
        if src in wp_by_src:
            row["review_status"] = "corrected"
            row["correction"] = wp_by_src[src]
        else:
            row["review_status"] = ""
            row["correction"] = {}
        cross_plan_rows.append(row)

    standards_rows = []
    for r in xrefs.get("standards_refs", []):
        row = _enrich(r)
        src = r.get("source_req_id", "")
        if src in ws_by_src:
            row["review_status"] = "corrected"
            row["correction"] = ws_by_src[src]
        else:
            row["review_status"] = ""
            row["correction"] = {}
        standards_rows.append(row)

    return {
        "internal": internal_rows,
        "cross_plan": cross_plan_rows,
        "standards": standards_rows,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
async def resolve_review_index(request: Request):
    from core.src.web.app import _template_response, config
    docs = _list_docs(config.env_dir_path())
    return _template_response(request, "resolve_review/index.html", {
        "docs": docs,
        "no_resolve_output": not _resolve_dir(config.env_dir_path()).is_dir(),
    })


@router.get("/{doc_id}/view", response_class=HTMLResponse)
async def resolve_review_view(request: Request, doc_id: str):
    from core.src.web.app import _template_response, config
    env_dir = config.env_dir_path()
    xrefs_path = _resolve_dir(env_dir) / f"{doc_id}_xrefs.json"

    if not xrefs_path.exists():
        return _template_response(request, "resolve_review/_view.html", {
            "doc_id": doc_id, "error": f"xrefs file not found: {xrefs_path.name}",
            "xrefs": {}, "rows": {}, "review": {}, "summary": {},
        })

    xrefs   = json.loads(xrefs_path.read_text(encoding="utf-8"))
    review_dir = _review_dir(env_dir)
    review  = _load_or_default_review(review_dir, doc_id)
    req_idx = _build_req_index(env_dir, doc_id)
    rows    = _build_ref_rows(xrefs, req_idx, review)
    summary = xrefs.get("summary", {})

    return _template_response(request, "resolve_review/_view.html", {
        "doc_id":  doc_id,
        "error":   None,
        "xrefs":   xrefs,
        "rows":    rows,
        "review":  review,
        "summary": summary,
        "has_tree": bool(req_idx),
    })


@router.post("/{doc_id}/save")
async def resolve_review_save(request: Request, doc_id: str):
    from core.src.web.app import config
    body = await request.json()
    review_dir = _review_dir(config.env_dir_path())
    review_path = review_dir / f"{doc_id}_resolve_review.json"
    try:
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review_path.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"ok": True, "path": review_path.name}
    except Exception as exc:
        logger.error("Save resolve review failed %s: %s", review_path, exc)
        return {"ok": False, "error": str(exc)}


@router.post("/{doc_id}/report", response_class=HTMLResponse)
async def resolve_review_report(request: Request, doc_id: str):
    from core.src.web.app import _template_response, config
    body = await request.json()
    env_dir = config.env_dir_path()
    review_dir = _review_dir(env_dir)
    review_path = review_dir / f"{doc_id}_resolve_review.json"
    try:
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review_path.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        return _template_response(request, "resolve_review/_report.html", {"report": f"Save failed: {exc}"})

    xrefs_path = _resolve_dir(env_dir) / f"{doc_id}_xrefs.json"
    try:
        report = generate_compact_report(review_path, xrefs_path=xrefs_path if xrefs_path.exists() else None)
    except Exception as exc:
        report = f"Report generation failed: {exc}"
    return _template_response(request, "resolve_review/_report.html", {"report": report})
