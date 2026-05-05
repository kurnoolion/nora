"""Requirement browser routes — browse, view, and compare requirements."""

from __future__ import annotations

import ast
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/req-browser", tags=["req-browser"])


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _parse_dir(env_dir_path: Path) -> Path:
    return env_dir_path / "out" / "parse"


def _resolve_dir(env_dir_path: Path) -> Path:
    return env_dir_path / "out" / "resolve"


def _list_docs(env_dir_path: Path) -> list[str]:
    d = _parse_dir(env_dir_path)
    if not d.is_dir():
        return []
    return sorted(p.stem.replace("_tree", "") for p in d.glob("*_tree.json"))


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


def _load_tree_flat(env_dir_path: Path, doc_id: str) -> list[dict]:
    p = _parse_dir(env_dir_path) / f"{doc_id}_tree.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("requirements", [])
    except Exception as exc:
        logger.warning("Failed to load tree %s: %s", p, exc)
        return []


def _build_tree_hierarchy(reqs: list[dict]) -> list[dict]:
    """Convert flat requirement list into nested tree (child_nodes populated).

    Reqs with an empty req_id are skipped (parser artefacts).
    """
    nodes: dict[str, dict] = {}
    for r in reqs:
        rid = r.get("req_id", "")
        if rid:
            nodes[rid] = {**r, "child_nodes": []}

    roots: list[dict] = []
    for node in nodes.values():
        parent_id = node.get("parent_req_id", "")
        if parent_id and parent_id in nodes:
            nodes[parent_id]["child_nodes"].append(node)
        else:
            roots.append(node)

    return roots


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
