"""Trees store — parsed requirement trees per (MNO, release) cell
(store-split-design §2, §5, §6; strand corp-db-layer S4).

Holds what the parser writes to ``out/parse/<mno>/<release>/<doc>_tree.json``:
the document-level fields (plan_id, plan_name, version, definitions_map, …)
and the ``requirements`` list (full text, tables, hierarchy fields). Read by
nora-web: the Requirement Browser, ``/api/req/{req_id}`` and the Eval Studio
picker, through ``core.src.web.req_tree``.

Row shapes (identical across backends):

- ``find_req`` / ``find_by_fingerprint`` rows: ``{req_id, mno, release,
  plan, plan_name, doc_id, title, section_number, parent_section, text}``;
  ``plan_name`` only for the tree's primary plan (see ``find_req``).
- ``reqs_for_plan`` and traversal rows: the requirement dict as parsed plus
  ``doc_id``.

Hierarchy: within one document, by ``parent_req_id`` (the rule
``build_tree_hierarchy`` uses). A node whose parent id is not in the
document is a root. When an id occurs more than once in a document, the
LAST occurrence is the addressable node (as in ``build_tree_hierarchy``);
earlier occurrences and blank-id nodes are isolated roots. A parent cycle
is broken at its first node in document order, which becomes a root.
``tree_layout`` computes this once for both backends.

Backends:
- ``FileTreeStore(env_dir)`` — the JSON trees under ``out/parse/``,
  per-cell and legacy flat layouts, with an mtime+size tree cache.
- ``SqliteTreeStore(root_dir)`` — one write-once ``trees-<mno>-<release>.db``
  per cell (built by ``trees_build``), opened ``mode=ro&immutable=1``.
  Per-cell only: the legacy flat layout is not supported.

``open_tree_store`` picks the backend: ``root_dir`` (or ``$NORA_TREES_ROOT``)
→ SQLite; else the file backend over ``env_dir``.

Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol, runtime_checkable
from urllib.parse import quote

from core.src.models.fingerprint import FINGERPRINT_VERSION, text_fp

logger = logging.getLogger(__name__)

TREES_ROOT_ENV = "NORA_TREES_ROOT"
SQLITE_SCHEMA_VERSION = 1
DB_PREFIX = "trees-"
DB_SUFFIX = ".db"

_REQS_KEY = "requirements"
_PATH_WIDTH = 6                 # zero-padded ordinal per path segment


# ─── Pure helpers (shared by both backends and by req_tree) ──────────


def release_sort_key(release: str):
    """Chronological MMMYYYY order; non-conforming labels sort first."""
    from core.src.extraction.release_key import release_order_key

    return release_order_key(release) or (0, 0)


def req_plan(req: dict, tree: dict) -> str:
    """A requirement's effective plan: per-req plan_id (multi-plan docs)
    falling back to the tree-level scalar — same rule the parser stamps.
    """
    return req.get("plan_id") or tree.get("plan_id") or ""


def req_text_fp(req: dict) -> str:
    """``text_fp`` over the requirement's title and body."""
    return text_fp(_s(req.get("title")), _s(req.get("text")))


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


def latest_match(matches: list[dict]) -> dict:
    """Pick the newest cell from ``find_req`` results — the match with the
    latest release/revision. Used for direct-entry auto-qualification when a
    req_id appears across several releases (e.g. Mar vs Jun): the expert wants
    the current revision, not an ambiguity error. Ties (same release, multiple
    docs/plans) resolve to the first for determinism. Empty in → KeyError is
    the caller's bug; callers pass a non-empty list.
    """
    return max(matches, key=lambda m: release_sort_key(m["release"]))


def tree_layout(reqs: list[dict]) -> list[tuple["int | None", str, int]]:
    """Per requirement (by list index): ``(parent_index, path, depth)``.

    ``path`` is a zero-padded ordinal path (``000003.000001``): siblings
    are numbered in document order, so a subtree is a path-prefix range
    and the ancestors are the path's prefixes. Rules in the module
    docstring.
    """
    n = len(reqs)
    canon: dict[str, int] = {}
    for i, r in enumerate(reqs):
        rid = _s(r.get("req_id"))
        if rid:
            canon[rid] = i                  # last occurrence wins
    parent: list["int | None"] = [None] * n
    for rid, i in canon.items():
        pid = _s(reqs[i].get("parent_req_id"))
        j = canon.get(pid) if pid else None
        parent[i] = j if j != i else None
    # Break parent cycles: walk each chain once; a node met twice on the
    # current walk closes a cycle, cut at its smallest index.
    done = [False] * n
    for i in range(n):
        walk: list[int] = []
        on_walk: set[int] = set()
        j: "int | None" = i
        while j is not None and not done[j] and j not in on_walk:
            walk.append(j)
            on_walk.add(j)
            j = parent[j]
        if j is not None and j in on_walk:
            cycle = walk[walk.index(j):]
            parent[min(cycle)] = None
        for k in walk:
            done[k] = True
    kids: list[list[int]] = [[] for _ in range(n)]
    roots: list[int] = []
    for i in range(n):
        (roots if parent[i] is None else kids[parent[i]]).append(i)
    out: list = [None] * n
    stack = [(i, "", 0, k) for k, i in reversed(list(enumerate(roots, 1)))]
    while stack:
        i, prefix, depth, ordinal = stack.pop()
        path = f"{prefix}{ordinal:0{_PATH_WIDTH}d}"
        out[i] = (parent[i], path, depth)
        for k, c in reversed(list(enumerate(kids[i], 1))):
            stack.append((c, path + ".", depth + 1, k))
    return out


def _s(v: Any) -> str:
    if v is None:
        return ""
    return v if isinstance(v, str) else str(v)


def _match_row(req: dict, tree: dict, doc_id: str, mno: str, rel: str) -> dict:
    """One ``find_req`` row (primary-plan-only ``plan_name``)."""
    plan = req_plan(req, tree)
    is_primary = plan == tree.get("plan_id", "")
    return {
        "req_id": req.get("req_id") or "",
        "mno": mno,
        "release": rel,
        "plan": plan,
        "plan_name": tree.get("plan_name", "") if is_primary else "",
        "doc_id": doc_id,
        "title": req.get("title", ""),
        "section_number": req.get("section_number", ""),
        "parent_section": req.get("parent_section", ""),
        "text": req.get("text", ""),
    }


def _sort_cells(cells: list[dict]) -> list[dict]:
    cells.sort(key=lambda c: (c["mno"], release_sort_key(c["release"])))
    return cells


# ─── Protocol ────────────────────────────────────────────────────────


@runtime_checkable
class TreeStore(Protocol):
    """Read interface over parsed trees (write-once per cell)."""

    def list_cells(self) -> list[dict]:
        """``[{mno, release}]`` with at least one tree, by MNO then
        chronological release."""
        ...
    def list_docs(self, mno: str = "", release: str = "") -> list[str]:
        """Doc ids of one cell (both qualifiers) or of every cell (sorted,
        a doc in several cells listed once per cell)."""
        ...
    def load_tree(self, doc_id: str, mno: str = "", release: str = "") -> dict:
        """The full tree dict; {} when missing. Read-only for callers."""
        ...
    def plans_for_mno(self, mno: str) -> dict[str, list[str]]: ...
    def find_req(self, req_id: str, mno: str = "", release: str = "",
                 ignore_case: bool = False) -> list[dict]: ...
    def reqs_for_plan(self, mno: str, release: str, plan: str) -> list[dict]: ...

    # traversal within one cell (rows: requirement dict + doc_id)
    def get_node(self, mno: str, release: str, req_id: str) -> "dict | None": ...
    def children(self, mno: str, release: str, req_id: str) -> list[dict]: ...
    def subtree(self, mno: str, release: str, req_id: str) -> list[dict]:
        """The node and all its descendants, in document order."""
        ...
    def ancestors(self, mno: str, release: str, req_id: str) -> list[dict]:
        """Root first; excludes the node itself."""
        ...
    def find_by_fingerprint(self, fingerprint: str, mno: str = "") -> list[dict]: ...


# ─── File backend ────────────────────────────────────────────────────


def parse_dir(env_dir_path: Path) -> Path:
    return Path(env_dir_path) / "out" / "parse"


def find_tree(
    env_dir_path: Path, doc_id: str, mno: str = "", release: str = ""
) -> "Path | None":
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


class _DocIndex:
    """Derived per-tree data for traversal and fingerprint lookups."""

    __slots__ = ("reqs", "layout", "canon", "kids", "fps")

    def __init__(self, tree: dict):
        self.reqs: list[dict] = tree.get(_REQS_KEY, []) or []
        self.layout = tree_layout(self.reqs)
        self.canon: dict[str, int] = {}
        self.kids: dict[int, list[int]] = {}
        for i, r in enumerate(self.reqs):
            rid = _s(r.get("req_id"))
            if rid:
                self.canon[rid] = i
            p = self.layout[i][0]
            if p is not None:
                self.kids.setdefault(p, []).append(i)
        self.fps: "list[str] | None" = None

    def fingerprints(self) -> list[str]:
        if self.fps is None:
            self.fps = [req_text_fp(r) for r in self.reqs]
        return self.fps


class FileTreeStore:
    """Today's JSON trees under ``<env_dir>/out/parse/``.

    Per-cell trees live at ``out/parse/<mno>/<release>/`` (D-DRAFT-6); a
    legacy flat layout keeps trees directly under ``out/parse/``. Lookups
    accept optional cell qualifiers and fall back to a recursive search.
    """

    def __init__(self, env_dir: "str | Path"):
        self.env_dir = Path(env_dir)
        # path -> [mtime, size, tree, _DocIndex | None]. Parse trees are
        # written by the pipeline, not the web app, so mtime+size staleness
        # checks suffice. Real corpora make repeated JSON parses the
        # dominant page cost (D-DRAFT-6 anticipated this cache).
        self._cache: dict[str, list] = {}

    # -- reads ---------------------------------------------------------

    def list_cells(self) -> list[dict]:
        root = parse_dir(self.env_dir)
        cells: list[dict] = []
        if not root.is_dir():
            return cells
        for mno_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            for rel_dir in sorted(p for p in mno_dir.iterdir() if p.is_dir()):
                if any(rel_dir.glob("*_tree.json")):
                    cells.append({"mno": mno_dir.name, "release": rel_dir.name})
        return _sort_cells(cells)

    def list_docs(self, mno: str = "", release: str = "") -> list[str]:
        root = parse_dir(self.env_dir)
        if mno and release:
            root = root / mno / release
        if not root.is_dir():
            return []
        return sorted(p.stem.replace("_tree", "") for p in root.rglob("*_tree.json"))

    def find_tree(self, doc_id: str, mno: str = "", release: str = "") -> "Path | None":
        return find_tree(self.env_dir, doc_id, mno, release)

    def _entry(self, doc_id: str, mno: str, release: str) -> "list | None":
        p = self.find_tree(doc_id, mno, release)
        if p is None:
            return None
        try:
            st = p.stat()
            key = str(p)
            hit = self._cache.get(key)
            if hit is not None and hit[0] == st.st_mtime and hit[1] == st.st_size:
                return hit
            tree = json.loads(p.read_text(encoding="utf-8"))
            entry = [st.st_mtime, st.st_size, tree, None]
            self._cache[key] = entry
            return entry
        except Exception as exc:
            logger.warning("Failed to load tree %s: %s", p, exc)
            return None

    def load_tree(self, doc_id: str, mno: str = "", release: str = "") -> dict:
        entry = self._entry(doc_id, mno, release)
        return entry[2] if entry is not None else {}

    def _index(self, doc_id: str, mno: str, release: str) -> "_DocIndex | None":
        entry = self._entry(doc_id, mno, release)
        if entry is None or not isinstance(entry[2], dict):
            return None
        if entry[3] is None:
            entry[3] = _DocIndex(entry[2])
        return entry[3]

    def plans_for_mno(self, mno: str) -> dict[str, list[str]]:
        out: dict[str, set[str]] = {}
        for cell in self.list_cells():
            if cell["mno"] != mno:
                continue
            rel = cell["release"]
            for doc_id in self.list_docs(mno, rel):
                tree = self.load_tree(doc_id, mno, rel)
                for req in tree.get(_REQS_KEY, []):
                    plan = req_plan(req, tree)
                    if plan:
                        out.setdefault(plan, set()).add(rel)
        return {
            plan: sorted(rels, key=release_sort_key)
            for plan, rels in sorted(out.items())
        }

    def _scan_cells(self, mno: str = "", release: str = "") -> list[dict]:
        """Both qualifiers → that cell; else every cell, or the legacy flat
        corpus (empty mno/release) when no per-cell trees exist."""
        if mno and release:
            return [{"mno": mno, "release": release}]
        return self.list_cells() or [{"mno": "", "release": ""}]

    def find_req(self, req_id: str, mno: str = "", release: str = "",
                 ignore_case: bool = False) -> list[dict]:
        want = req_id.casefold() if ignore_case else req_id
        matches: list[dict] = []
        for cell in self._scan_cells(mno, release):
            m, rel = cell["mno"], cell["release"]
            for doc_id in self.list_docs(m, rel):
                tree = self.load_tree(doc_id, m, rel)
                for req in tree.get(_REQS_KEY, []):
                    rid = req.get("req_id") or ""
                    if (rid.casefold() if ignore_case else rid) == want:
                        matches.append(_match_row(req, tree, doc_id, m, rel))
        return matches

    def reqs_for_plan(self, mno: str, release: str, plan: str) -> list[dict]:
        rows: list[dict] = []
        for doc_id in self.list_docs(mno, release):
            tree = self.load_tree(doc_id, mno, release)
            for req in tree.get(_REQS_KEY, []):
                if req_plan(req, tree) == plan:
                    rows.append({**req, "doc_id": doc_id})
        return rows

    # -- traversal -----------------------------------------------------

    def _locate(self, mno: str, release: str, req_id: str):
        """(doc_id, index, i) of the addressable node: first doc that has
        the id, last occurrence in that doc."""
        if not req_id:
            return None
        for doc_id in self.list_docs(mno, release):
            idx = self._index(doc_id, mno, release)
            if idx is not None and req_id in idx.canon:
                return doc_id, idx, idx.canon[req_id]
        return None

    def get_node(self, mno: str, release: str, req_id: str) -> "dict | None":
        hit = self._locate(mno, release, req_id)
        if hit is None:
            return None
        doc_id, idx, i = hit
        return {**idx.reqs[i], "doc_id": doc_id}

    def children(self, mno: str, release: str, req_id: str) -> list[dict]:
        hit = self._locate(mno, release, req_id)
        if hit is None:
            return []
        doc_id, idx, i = hit
        return [{**idx.reqs[c], "doc_id": doc_id} for c in idx.kids.get(i, [])]

    def subtree(self, mno: str, release: str, req_id: str) -> list[dict]:
        hit = self._locate(mno, release, req_id)
        if hit is None:
            return []
        doc_id, idx, i = hit
        path = idx.layout[i][1]
        under = path + "."
        return [{**r, "doc_id": doc_id} for k, r in enumerate(idx.reqs)
                if idx.layout[k][1] == path or idx.layout[k][1].startswith(under)]

    def ancestors(self, mno: str, release: str, req_id: str) -> list[dict]:
        hit = self._locate(mno, release, req_id)
        if hit is None:
            return []
        doc_id, idx, i = hit
        chain: list[dict] = []
        p = idx.layout[i][0]
        while p is not None:
            chain.append({**idx.reqs[p], "doc_id": doc_id})
            p = idx.layout[p][0]
        chain.reverse()
        return chain

    def find_by_fingerprint(self, fingerprint: str, mno: str = "") -> list[dict]:
        rows: list[dict] = []
        cells = self.list_cells()
        if mno:
            cells = [c for c in cells if c["mno"] == mno]
        elif not cells:
            cells = [{"mno": "", "release": ""}]
        for cell in cells:
            m, rel = cell["mno"], cell["release"]
            for doc_id in self.list_docs(m, rel):
                idx = self._index(doc_id, m, rel)
                if idx is None:
                    continue
                tree = self.load_tree(doc_id, m, rel)
                for req, fp in zip(idx.reqs, idx.fingerprints()):
                    if fp == fingerprint and (req.get("req_id") or ""):
                        rows.append(_match_row(req, tree, doc_id, m, rel))
        return rows


# ─── SQLite backend ──────────────────────────────────────────────────

# Postgres-portable: TEXT / INTEGER only. `doc.tree_json` is the tree
# without its requirements list; `req_key_pos` is that key's position in
# the original key order (-1 = the tree had no requirements key), so
# `load_tree` reproduces the dict exactly. `node.req_json` is the exact
# requirement dict; the other node columns are query keys derived from it.
_SQLITE_DDL = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE doc (
    doc_id      TEXT PRIMARY KEY,
    plan_id     TEXT NOT NULL,
    plan_name   TEXT NOT NULL,
    req_key_pos INTEGER NOT NULL,
    tree_json   TEXT NOT NULL
);
CREATE TABLE node (
    id             INTEGER PRIMARY KEY,
    doc_id         TEXT NOT NULL REFERENCES doc (doc_id),
    seq            INTEGER NOT NULL,
    parent_id      INTEGER REFERENCES node (id),
    path           TEXT NOT NULL,
    depth          INTEGER NOT NULL,
    req_id         TEXT NOT NULL,
    req_id_cf      TEXT NOT NULL,
    section_number TEXT NOT NULL,
    title          TEXT NOT NULL,
    plan_id        TEXT NOT NULL,
    is_requirement INTEGER,
    fingerprint    TEXT NOT NULL,
    req_json       TEXT NOT NULL,
    UNIQUE (doc_id, seq)
);
CREATE INDEX node_req_id ON node (req_id);
CREATE INDEX node_req_id_cf ON node (req_id_cf);
CREATE INDEX node_fingerprint ON node (fingerprint);
CREATE INDEX node_plan ON node (plan_id);
CREATE INDEX node_path ON node (doc_id, path);
CREATE INDEX node_parent ON node (parent_id);
"""


def db_name(mno: str, release: str) -> str:
    return f"{DB_PREFIX}{mno}-{release}{DB_SUFFIX}"


def write_tree_db(db_path: "str | Path", mno: str, release: str,
                  docs: Iterable[tuple[str, dict]],
                  source_fingerprint: str) -> dict:
    """Write one cell DB (the file must not exist). ``docs`` is
    ``(doc_id, tree)`` pairs. Returns ``{"n_docs", "n_nodes"}``. The caller
    owns atomicity (write to a temp name, verify, rename)."""
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(_SQLITE_DDL)
        n_docs = n_nodes = 0
        next_id = 1
        for doc_id, tree in docs:
            keys = list(tree.keys())
            pos = keys.index(_REQS_KEY) if _REQS_KEY in tree else -1
            head = {k: v for k, v in tree.items() if k != _REQS_KEY}
            reqs = tree.get(_REQS_KEY, []) or []
            con.execute(
                "INSERT INTO doc (doc_id, plan_id, plan_name, req_key_pos, tree_json)"
                " VALUES (?, ?, ?, ?, ?)",
                (doc_id, _s(tree.get("plan_id")), _s(tree.get("plan_name")),
                 pos, json.dumps(head, ensure_ascii=False)))
            layout = tree_layout(reqs)
            base = next_id
            rows = []
            for i, (req, (parent, path, depth)) in enumerate(zip(reqs, layout)):
                rid = _s(req.get("req_id"))
                is_req = req.get("is_requirement")
                rows.append((
                    base + i, doc_id, i,
                    None if parent is None else base + parent,
                    path, depth, rid, rid.casefold(),
                    _s(req.get("section_number")), _s(req.get("title")),
                    _s(req_plan(req, tree)),
                    None if is_req is None else int(bool(is_req)),
                    req_text_fp(req),
                    json.dumps(req, ensure_ascii=False)))
            con.executemany(
                "INSERT INTO node (id, doc_id, seq, parent_id, path, depth, req_id,"
                " req_id_cf, section_number, title, plan_id, is_requirement,"
                " fingerprint, req_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
            next_id += len(reqs)
            n_docs += 1
            n_nodes += len(reqs)
        meta = {
            "schema_version": str(SQLITE_SCHEMA_VERSION),
            "mno": mno,
            "release": release,
            "cell": f"{mno}__{release}",
            "n_docs": str(n_docs),
            "n_nodes": str(n_nodes),
            "source_fingerprint": source_fingerprint,
            "fingerprint_version": FINGERPRINT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        con.executemany("INSERT INTO meta (key, value) VALUES (?, ?)",
                        sorted(meta.items()))
        con.commit()
        return {"n_docs": n_docs, "n_nodes": n_nodes}
    finally:
        con.close()


def read_meta(db_path: "str | Path") -> dict[str, str]:
    con = _connect_ro(Path(db_path))
    try:
        return dict(con.execute("SELECT key, value FROM meta"))
    finally:
        con.close()


def _connect_ro(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path.resolve()))}?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True)


class SqliteTreeStore:
    """Every ``trees-<mno>-<release>.db`` under ``root_dir`` (one per cell,
    write-once). The cell comes from each file's ``meta``, not its name.
    Per-call read-only connections; files replaced by a rebuild (new inode)
    are picked up by their changed mtime/size. ``db_paths`` names the files
    explicitly instead of scanning a root (the builder's verify step).
    """

    _TREE_CACHE_MAX = 64

    def __init__(self, root_dir: "str | Path" = "",
                 db_paths: "Iterable[str | Path] | None" = None):
        self.root = Path(root_dir) if root_dir else None
        self._explicit = [Path(p) for p in db_paths] if db_paths is not None else None
        self._meta_cache: dict[str, tuple[float, int, dict]] = {}
        self._doc_cache: dict[str, tuple[float, int, dict]] = {}
        self._tree_cache: "OrderedDict[tuple, dict]" = OrderedDict()

    # -- cell discovery --------------------------------------------------

    def _files(self) -> list[Path]:
        if self._explicit is not None:
            return list(self._explicit)
        if self.root is None or not self.root.is_dir():
            return []
        return sorted(self.root.glob(f"{DB_PREFIX}*{DB_SUFFIX}"))

    def _cells(self) -> dict[tuple[str, str], Path]:
        """(mno, release) → DB file, in list_cells order."""
        found: dict[tuple[str, str], Path] = {}
        for p in self._files():
            try:
                st = p.stat()
                hit = self._meta_cache.get(str(p))
                if hit is None or hit[0] != st.st_mtime or hit[1] != st.st_size:
                    hit = (st.st_mtime, st.st_size, read_meta(p))
                    self._meta_cache[str(p)] = hit
                meta = hit[2]
            except (OSError, sqlite3.Error) as exc:
                logger.warning("Skipping unreadable trees DB %s: %s", p.name,
                               type(exc).__name__)
                continue
            if int(meta.get("n_docs", "0") or 0) == 0:
                continue
            key = (meta.get("mno", ""), meta.get("release", ""))
            if key in found:
                logger.warning("Duplicate trees DB for one cell; keeping %s, "
                               "ignoring %s", found[key].name, p.name)
                continue
            found[key] = p
        order = _sort_cells([{"mno": m, "release": r} for m, r in found])
        return {(c["mno"], c["release"]): found[(c["mno"], c["release"])]
                for c in order}

    def _docs(self, path: Path) -> dict[str, tuple[dict, int]]:
        """doc_id → (tree head, req_key_pos), cached per file version."""
        st = path.stat()
        hit = self._doc_cache.get(str(path))
        if hit is not None and hit[0] == st.st_mtime and hit[1] == st.st_size:
            return hit[2]
        con = _connect_ro(path)
        try:
            docs = {d: (json.loads(t), pos) for d, t, pos in con.execute(
                "SELECT doc_id, tree_json, req_key_pos FROM doc ORDER BY doc_id")}
        finally:
            con.close()
        self._doc_cache[str(path)] = (st.st_mtime, st.st_size, docs)
        return docs

    def _scope(self, mno: str = "", release: str = "") -> list[tuple[str, str, Path]]:
        cells = self._cells()
        if mno and release:
            p = cells.get((mno, release))
            return [(mno, release, p)] if p is not None else []
        return [(m, r, p) for (m, r), p in cells.items()]

    def _query(self, path: Path, sql: str, args: tuple = ()) -> list[tuple]:
        con = _connect_ro(path)
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()

    # -- reads -----------------------------------------------------------

    def list_cells(self) -> list[dict]:
        return [{"mno": m, "release": r} for m, r in self._cells()]

    def cell_meta(self, mno: str, release: str) -> dict[str, str]:
        """The cell DB's ``meta`` table; {} when the cell is absent."""
        p = self._cells().get((mno, release))
        return dict(self._meta_cache[str(p)][2]) if p is not None else {}

    def list_docs(self, mno: str = "", release: str = "") -> list[str]:
        out: list[str] = []
        for _, _, p in self._scope(mno, release):
            out.extend(self._docs(p))
        return sorted(out)

    def load_tree(self, doc_id: str, mno: str = "", release: str = "") -> dict:
        for _, _, p in self._scope(mno, release):
            docs = self._docs(p)
            if doc_id not in docs:
                continue
            st = p.stat()
            key = (str(p), st.st_mtime, st.st_size, doc_id)
            hit = self._tree_cache.get(key)
            if hit is not None:
                self._tree_cache.move_to_end(key)
                return hit
            head, pos = docs[doc_id]
            items = list(head.items())
            if pos >= 0:
                reqs = [json.loads(j) for (j,) in self._query(
                    p, "SELECT req_json FROM node WHERE doc_id = ? ORDER BY seq",
                    (doc_id,))]
                items.insert(pos, (_REQS_KEY, reqs))
            tree = dict(items)
            self._tree_cache[key] = tree
            if len(self._tree_cache) > self._TREE_CACHE_MAX:
                self._tree_cache.popitem(last=False)
            return tree
        return {}

    def plans_for_mno(self, mno: str) -> dict[str, list[str]]:
        out: dict[str, set[str]] = {}
        for m, rel, p in self._scope():
            if m != mno:
                continue
            for (plan,) in self._query(
                    p, "SELECT DISTINCT plan_id FROM node WHERE plan_id <> ''"):
                out.setdefault(plan, set()).add(rel)
        return {
            plan: sorted(rels, key=release_sort_key)
            for plan, rels in sorted(out.items())
        }

    def _rows(self, mno: str, rel: str, p: Path, where: str,
              args: tuple) -> list[dict]:
        docs = self._docs(p)
        return [_match_row(json.loads(j), docs[d][0], d, mno, rel)
                for d, j in self._query(
                    p, f"SELECT doc_id, req_json FROM node WHERE {where}"
                       " ORDER BY doc_id, seq", args)]

    def find_req(self, req_id: str, mno: str = "", release: str = "",
                 ignore_case: bool = False) -> list[dict]:
        where, arg = (("req_id_cf = ?", req_id.casefold()) if ignore_case
                      else ("req_id = ?", req_id))
        out: list[dict] = []
        for m, rel, p in self._scope(mno, release):
            out.extend(self._rows(m, rel, p, where, (arg,)))
        return out

    def reqs_for_plan(self, mno: str, release: str, plan: str) -> list[dict]:
        out: list[dict] = []
        for _, _, p in self._scope(mno, release):
            out.extend({**json.loads(j), "doc_id": d} for d, j in self._query(
                p, "SELECT doc_id, req_json FROM node WHERE plan_id = ?"
                   " ORDER BY doc_id, seq", (_s(plan),)))
        return out

    # -- traversal -------------------------------------------------------

    def _locate(self, mno: str, release: str, req_id: str):
        """(path to DB, node id, doc_id, node path) of the addressable node."""
        if not req_id:
            return None
        for _, _, p in self._scope(mno, release):
            hit = self._query(
                p, "SELECT id, doc_id, path FROM node WHERE req_id = ?"
                   " ORDER BY doc_id, seq DESC LIMIT 1", (req_id,))
            if hit:
                nid, doc_id, path = hit[0]
                return p, nid, doc_id, path
        return None

    @staticmethod
    def _as_rows(rows: list[tuple]) -> list[dict]:
        return [{**json.loads(j), "doc_id": d} for d, j in rows]

    def get_node(self, mno: str, release: str, req_id: str) -> "dict | None":
        hit = self._locate(mno, release, req_id)
        if hit is None:
            return None
        p, nid, _, _ = hit
        return self._as_rows(self._query(
            p, "SELECT doc_id, req_json FROM node WHERE id = ?", (nid,)))[0]

    def children(self, mno: str, release: str, req_id: str) -> list[dict]:
        hit = self._locate(mno, release, req_id)
        if hit is None:
            return []
        p, nid, _, _ = hit
        return self._as_rows(self._query(
            p, "SELECT doc_id, req_json FROM node WHERE parent_id = ?"
               " ORDER BY seq", (nid,)))

    def subtree(self, mno: str, release: str, req_id: str) -> list[dict]:
        hit = self._locate(mno, release, req_id)
        if hit is None:
            return []
        p, _, doc_id, path = hit
        # '/' sorts right after '.', so [path + '.', path + '/') is exactly
        # the descendants (a range scan on the (doc_id, path) index).
        return self._as_rows(self._query(
            p, "SELECT doc_id, req_json FROM node WHERE doc_id = ? AND"
               " (path = ? OR (path > ? AND path < ?)) ORDER BY seq",
            (doc_id, path, path + ".", path + "/")))

    def ancestors(self, mno: str, release: str, req_id: str) -> list[dict]:
        hit = self._locate(mno, release, req_id)
        if hit is None:
            return []
        p, _, doc_id, path = hit
        parts = path.split(".")
        prefixes = [".".join(parts[:k]) for k in range(1, len(parts))]
        if not prefixes:
            return []
        marks = ", ".join("?" * len(prefixes))
        return self._as_rows(self._query(
            p, f"SELECT doc_id, req_json FROM node WHERE doc_id = ? AND"
               f" path IN ({marks}) ORDER BY depth", (doc_id, *prefixes)))

    def find_by_fingerprint(self, fingerprint: str, mno: str = "") -> list[dict]:
        out: list[dict] = []
        for m, rel, p in self._scope():
            if mno and m != mno:
                continue
            out.extend(self._rows(m, rel, p, "fingerprint = ? AND req_id <> ''",
                                  (fingerprint,)))
        return out


def source_fingerprint(files: Iterable[Path]) -> str:
    """sha256 over the tree files (name + bytes) in sorted name order."""
    h = hashlib.sha256()
    for f in sorted(files, key=lambda p: p.name):
        h.update(f.name.encode("utf-8") + b"\0")
        h.update(f.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def open_tree_store(env_dir: "str | Path", root_dir: "str | Path" = "") -> TreeStore:
    """``root_dir`` (else ``$NORA_TREES_ROOT``) → SQLite cell DBs; else the
    file backend over ``env_dir``."""
    root = str(root_dir or os.getenv(TREES_ROOT_ENV, "")).strip()
    if root:
        return SqliteTreeStore(root)
    return FileTreeStore(env_dir)
