"""Shared requirement-tree loading over parse output (strand golden-eval).

Lifted from ``routes/req_browser.py`` so the Requirement Browser and the
Eval Studio's ground-truth picker read ``out/parse/`` through one code
path — duplicating tree loading in two routers is how the two surfaces
would drift apart (D-DRAFT-5).

Layout awareness: per-cell trees live at ``out/parse/<mno>/<release>/``
(D-DRAFT-6); a legacy flat layout keeps trees directly under
``out/parse/``. All helpers accept optional cell qualifiers and fall back
to a recursive search.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def parse_dir(env_dir_path: Path) -> Path:
    return env_dir_path / "out" / "parse"


def _release_sort_key(release: str):
    from core.src.extraction.release_key import release_order_key

    return release_order_key(release) or (0, 0)


def list_cells(env_dir_path: Path) -> list[dict]:
    """Discover ``(mno, release)`` cells that have at least one parsed
    tree. Sorted by MNO then chronological release (MMMYYYY order —
    the same rule the query side uses for "latest").
    """
    root = parse_dir(env_dir_path)
    cells: list[dict] = []
    if not root.is_dir():
        return cells
    for mno_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for rel_dir in sorted(p for p in mno_dir.iterdir() if p.is_dir()):
            if any(rel_dir.glob("*_tree.json")):
                cells.append({"mno": mno_dir.name, "release": rel_dir.name})
    cells.sort(key=lambda c: (c["mno"], _release_sort_key(c["release"])))
    return cells


def list_docs(
    env_dir_path: Path, mno: str = "", release: str = ""
) -> list[str]:
    """Doc ids with a parsed tree — scoped to one cell when qualifiers
    are given, whole corpus (flat + per-cell) otherwise.
    """
    root = parse_dir(env_dir_path)
    if mno and release:
        root = root / mno / release
    if not root.is_dir():
        return []
    return sorted(p.stem.replace("_tree", "") for p in root.rglob("*_tree.json"))


def find_tree(
    env_dir_path: Path, doc_id: str, mno: str = "", release: str = ""
) -> Path | None:
    """Resolve a doc's tree file. Cell-qualified path first, flat legacy
    path second, recursive search last (first match wins).
    """
    root = parse_dir(env_dir_path)
    if mno and release:
        p = root / mno / release / f"{doc_id}_tree.json"
        return p if p.exists() else None
    flat = root / f"{doc_id}_tree.json"
    if flat.exists():
        return flat
    if root.is_dir():
        for p in root.rglob(f"{doc_id}_tree.json"):
            return p
    return None


def load_tree(
    env_dir_path: Path, doc_id: str, mno: str = "", release: str = ""
) -> dict:
    """Full tree dict (incl. plan_id / plan_name / requirements).
    Empty dict when missing or unreadable.
    """
    p = find_tree(env_dir_path, doc_id, mno, release)
    if p is None:
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to load tree %s: %s", p, exc)
        return {}


def load_tree_flat(
    env_dir_path: Path, doc_id: str, mno: str = "", release: str = ""
) -> list[dict]:
    """The tree's flat requirements list (req_browser's historical shape)."""
    return load_tree(env_dir_path, doc_id, mno, release).get("requirements", [])


def build_tree_hierarchy(reqs: list[dict]) -> list[dict]:
    """Convert flat requirement list into nested tree (child_nodes
    populated). Reqs with an empty req_id are skipped (parser artefacts).
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


# ─── Plan discovery (Eval Studio picker cascade) ────────────────────


def _req_plan(req: dict, tree: dict) -> str:
    """A requirement's effective plan: per-req plan_id (multi-plan docs)
    falling back to the tree-level scalar — same rule the parser stamps.
    """
    return req.get("plan_id") or tree.get("plan_id") or ""


def plans_for_mno(env_dir_path: Path, mno: str) -> dict[str, list[str]]:
    """``{plan_id: [release, ...]}`` — the union of plans across the
    MNO's releases (picker's Plan dropdown), each with the releases that
    actually contain it (drives the Release dropdown filter). Releases
    chronological per MMMYYYY.
    """
    out: dict[str, set[str]] = {}
    for cell in list_cells(env_dir_path):
        if cell["mno"] != mno:
            continue
        rel = cell["release"]
        for doc_id in list_docs(env_dir_path, mno, rel):
            tree = load_tree(env_dir_path, doc_id, mno, rel)
            for req in tree.get("requirements", []):
                plan = _req_plan(req, tree)
                if plan:
                    out.setdefault(plan, set()).add(rel)
    return {
        plan: sorted(rels, key=_release_sort_key)
        for plan, rels in sorted(out.items())
    }


def find_req(env_dir_path: Path, req_id: str) -> list[dict]:
    """Locate a req_id across the corpus — direct-entry validation and
    auto-qualification in the Eval Studio picker (an id found in exactly
    one cell auto-fills its qualifiers). Returns
    ``[{mno, release, plan, doc_id, title, text}]``, one row per cell the
    id appears in. Falls back to the legacy flat layout (empty mno/release)
    when no per-cell trees exist.
    """
    matches: list[dict] = []
    cells = list_cells(env_dir_path) or [{"mno": "", "release": ""}]
    for cell in cells:
        mno, rel = cell["mno"], cell["release"]
        for doc_id in list_docs(env_dir_path, mno, rel):
            tree = load_tree(env_dir_path, doc_id, mno, rel)
            for req in tree.get("requirements", []):
                if req.get("req_id") == req_id:
                    matches.append({
                        "mno": mno,
                        "release": rel,
                        "plan": _req_plan(req, tree),
                        "doc_id": doc_id,
                        "title": req.get("title", ""),
                        "text": req.get("text", ""),
                    })
    return matches


def reqs_for_plan(
    env_dir_path: Path, mno: str, release: str, plan: str
) -> list[dict]:
    """Every requirement in one cell belonging to ``plan``, across all of
    the cell's trees, each row annotated with its source ``doc_id``.
    Ordering: doc_id, then tree order (the parser's document order).
    """
    rows: list[dict] = []
    for doc_id in list_docs(env_dir_path, mno, release):
        tree = load_tree(env_dir_path, doc_id, mno, release)
        for req in tree.get("requirements", []):
            if _req_plan(req, tree) == plan:
                rows.append({**req, "doc_id": doc_id})
    return rows
