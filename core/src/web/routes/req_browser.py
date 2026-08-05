"""Requirement browser routes — browse, view, and compare requirements.

Tree loading lives in the shared ``core.src.web.req_tree`` module (used by
this router and the Eval Studio picker — strand golden-eval, D-DRAFT-5).
"""

from __future__ import annotations

import ast
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from core.src.web.req_tree import (
    build_tree_hierarchy as _build_tree_hierarchy,
    list_docs as _shared_list_docs,
    load_tree_flat as _load_tree_flat,
    parse_dir as _parse_dir,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/req-browser", tags=["req-browser"])


def _resolve_dir(env_dir_path: Path) -> Path:
    return env_dir_path / "out" / "resolve"


def _list_docs(env_dir_path: Path) -> list[str]:
    return _shared_list_docs(env_dir_path)


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _parse_str_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    try:
        return ast.literal_eval(value) if value else []
    except Exception:
        return []


def _load_req(env_dir_path: Path, doc_id: str, req_id: str) -> dict | None:
    for r in _load_tree_flat(env_dir_path, doc_id):
        if r.get("req_id") == req_id:
            return r
    return None


def _load_xrefs(env_dir_path: Path, doc_id: str) -> dict[str, Any]:
    p = _resolve_dir(env_dir_path) / f"{doc_id}_xrefs.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _refs_for_req(xrefs: dict, req_id: str) -> dict[str, list]:
    """Return refs sourced from req_id, grouped by type."""
    def _f(lst: list) -> list:
        return [r for r in lst if r.get("source_req_id") == req_id]
    return {
        "internal":   _f(xrefs.get("internal_refs",   [])),
        "cross_plan": _f(xrefs.get("cross_plan_refs", [])),
        "standards":  _f(xrefs.get("standards_refs",  [])),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
async def req_browser_index(request: Request):
    from core.src.web.app import _template_response, config
    env_dir = config.env_dir_path()
    docs = _list_docs(env_dir)
    return _template_response(request, "req_browser/index.html", {
        "docs": docs,
        "no_parse_output": not _parse_dir(env_dir).is_dir(),
    })


@router.get("/compare", response_class=HTMLResponse)
async def req_browser_compare(
    request: Request,
    a_doc: str = "",
    a_req: str = "",
    b_doc: str = "",
    b_req: str = "",
):
    from core.src.web.app import _template_response, config
    env_dir = config.env_dir_path()
    req_a = _load_req(env_dir, a_doc, a_req) if a_doc and a_req else None
    req_b = _load_req(env_dir, b_doc, b_req) if b_doc and b_req else None
    return _template_response(request, "req_browser/_compare.html", {
        "a_doc": a_doc, "a_req": a_req, "req_a": req_a,
        "b_doc": b_doc, "b_req": b_req, "req_b": req_b,
    })


@router.get("/{doc_id}/tree", response_class=HTMLResponse)
async def req_browser_tree(request: Request, doc_id: str):
    from core.src.web.app import _template_response, config
    reqs = _load_tree_flat(config.env_dir_path(), doc_id)
    tree = _build_tree_hierarchy(reqs)
    return _template_response(request, "req_browser/_tree.html", {
        "doc_id": doc_id,
        "tree":   tree,
    })


@router.get("/{doc_id}/req/{req_id:path}", response_class=HTMLResponse)
async def req_browser_detail(request: Request, doc_id: str, req_id: str):
    from core.src.web.app import _template_response, config
    env_dir = config.env_dir_path()
    req = _load_req(env_dir, doc_id, req_id)
    if req is None:
        return HTMLResponse(
            f'<div class="alert alert-warning small">Requirement {req_id} not found in {doc_id}.</div>'
        )
    xrefs = _load_xrefs(env_dir, doc_id)
    refs  = _refs_for_req(xrefs, req_id)
    return _template_response(request, "req_browser/_req_detail.html", {
        "doc_id": doc_id,
        "req":    req,
        "refs":   refs,
    })
