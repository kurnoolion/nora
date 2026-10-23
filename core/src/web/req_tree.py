"""Shared requirement-tree loading (strand golden-eval; Trees store since
strand corp-db-layer S4).

Lifted from ``routes/req_browser.py`` so the Requirement Browser and the
Eval Studio's ground-truth picker read parsed trees through one code
path — duplicating tree loading in two routers is how the two surfaces
would drift apart (D-DRAFT-5).

A thin wrapper over the Trees store (``core.src.stores.trees``): every
function keeps its signature (env dir first) and delegates to
``open_tree_store(env_dir)`` — nora-store's ``/stores/trees/v1`` when
``$NORA_TREES_URL`` is set (S6), else the SQLite cell DBs when
``$NORA_TREES_ROOT`` is set, else the JSON trees under ``out/parse/``. One
store is kept per (env dir, root, url), so the file backend's tree cache
lives across calls. ``parse_dir`` / ``find_tree`` are file-layout helpers
only.
"""

from __future__ import annotations

import os
from pathlib import Path

from core.src.stores.trees import (  # noqa: F401  (re-exports)
    TREES_ROOT_ENV,
    TREES_URL_ENV,
    TreeStore,
    build_tree_hierarchy,
    find_tree,
    latest_match,
    open_tree_store,
    parse_dir,
)

_STORES: dict[tuple[str, str, str], TreeStore] = {}


def tree_store(env_dir_path: Path) -> TreeStore:
    """The cached store for this env dir and the current
    ``$NORA_TREES_URL`` / ``$NORA_TREES_ROOT``."""
    root = os.getenv(TREES_ROOT_ENV, "").strip()
    url = os.getenv(TREES_URL_ENV, "").strip()
    key = (str(env_dir_path), root, url)
    store = _STORES.get(key)
    if store is None:
        store = _STORES[key] = open_tree_store(env_dir_path, root, url)
    return store


def list_cells(env_dir_path: Path) -> list[dict]:
    """Discover ``(mno, release)`` cells that have at least one parsed
    tree. Sorted by MNO then chronological release (MMMYYYY order —
    the same rule the query side uses for "latest").
    """
    return tree_store(env_dir_path).list_cells()


def list_docs(
    env_dir_path: Path, mno: str = "", release: str = ""
) -> list[str]:
    """Doc ids with a parsed tree — scoped to one cell when qualifiers
    are given, whole corpus (flat + per-cell) otherwise.
    """
    return tree_store(env_dir_path).list_docs(mno, release)


def load_tree(
    env_dir_path: Path, doc_id: str, mno: str = "", release: str = ""
) -> dict:
    """Full tree dict (incl. plan_id / plan_name / requirements).
    Empty dict when missing or unreadable. The returned dict is a shared
    cache entry — callers must treat it as read-only.
    """
    return tree_store(env_dir_path).load_tree(doc_id, mno, release)


def load_tree_flat(
    env_dir_path: Path, doc_id: str, mno: str = "", release: str = ""
) -> list[dict]:
    """The tree's flat requirements list (req_browser's historical shape)."""
    return load_tree(env_dir_path, doc_id, mno, release).get("requirements", [])


# ─── Plan discovery (Eval Studio picker cascade) ────────────────────


def plans_for_mno(env_dir_path: Path, mno: str) -> dict[str, list[str]]:
    """``{plan_id: [release, ...]}`` — the union of plans across the
    MNO's releases (picker's Plan dropdown), each with the releases that
    actually contain it (drives the Release dropdown filter). Releases
    chronological per MMMYYYY.
    """
    return tree_store(env_dir_path).plans_for_mno(mno)


def find_req(
    env_dir_path: Path, req_id: str, mno: str = "", release: str = "",
    ignore_case: bool = False,
) -> list[dict]:
    """Locate a req_id across the corpus — direct-entry validation and
    auto-qualification in the Eval Studio picker (an id found in exactly
    one cell auto-fills its qualifiers). Returns
    ``[{req_id, mno, release, plan, plan_name, doc_id, title,
    section_number, parent_section, text}]``, one row per cell the id
    appears in. The file backend falls back to the legacy flat layout
    (empty mno/release) when no per-cell trees exist.

    ``section_number`` / ``parent_section`` / ``plan_name`` are for display
    beside a requirement snippet (strand req-bubble-metadata). Two contracts
    consumers need to know:

    - ``section_number`` is routinely ``""``. Table-anchored and
      leading-id-body Requirements have no section number by design and are
      located through ``parent_section`` instead (see parser MODULE.md); TOC
      pair misses also leave it empty. The key is always present, so branch
      on emptiness, never on absence.
    - ``plan_name`` is populated **only for the tree's primary plan** — the
      requirement whose effective plan equals the tree's own ``plan_id``. A
      document can carry several plans, but the document-level ``plan_name``
      describes just one of them; ``graph/builder.py`` stamps secondary plans
      with an empty name for the same reason. No per-plan-id-to-name mapping
      exists anywhere, so a secondary plan's requirement gets ``""`` rather
      than borrowing a name that belongs to a different plan.

    Passing both qualifiers scopes the scan to that single cell —
    corpus-wide scans are for genuinely unqualified lookups only.

    ``ignore_case`` matches the id case-insensitively (the golden eval's
    m3 rule); each row's ``req_id`` is the tree's own spelling, so a
    caller can store the canonical form.
    """
    return tree_store(env_dir_path).find_req(req_id, mno, release,
                                             ignore_case=ignore_case)


def reqs_for_plan(
    env_dir_path: Path, mno: str, release: str, plan: str
) -> list[dict]:
    """Every requirement in one cell belonging to ``plan``, across all of
    the cell's trees, each row annotated with its source ``doc_id``.
    Ordering: doc_id, then tree order (the parser's document order).
    """
    return tree_store(env_dir_path).reqs_for_plan(mno, release, plan)
