"""History store — release lineage of an MNO's plans, sections and
requirements (store-split-design §3, §6a; strand corp-db-layer S5).

Built from a ``TreeStore`` (``history_build``), one write-once
``history-<mno>.db`` per MNO. Answers: the version history of a requirement
or a plan, when either last changed, and what changed between two releases.

Identity (stable across releases):

- plan        ``plan_id``
- section     ``(plan_id, section_number)`` — a non-requirement node with a
  section number; a renumbered section is a removed node plus an added one
- requirement ``(plan_id, req_id)`` — its section is a location attribute,
  so a move keeps the key and records a new location

A requirement's plan is its effective plan (``req_plan``). Hierarchy is the
Trees store's (``tree_layout``, by ``parent_req_id``); a node whose nearest
keyed ancestor is absent or in another plan hangs under its plan node.
Nodes without an identity (no req_id and no section number) and repeated
keys are skipped; their keyed descendants climb to the next keyed ancestor.

Hashes per node and release (``models.fingerprint``): ``text_fp`` (title +
body), ``node_fp`` (``text_fp`` + priority + applicability; plans and
sections have neither), ``subtree_fp`` and ``child_set_fp``. The child
entries fed to both are each child's *placed* hash — its key, location and
``subtree_fp`` — so a renumbering, a rename in place or a move between
sections changes every ancestor's ``subtree_fp``. That keeps the diff's
descent sound: equal ``subtree_fp`` means nothing at or under the node
changed except, possibly, the node's own location.

Change kinds between two releases: ``added``, ``removed``, ``changed``
(``node_fp``), ``moved`` (a different parent), ``renumbered`` (same parent,
the requirement's own outline number changed — a chapter inserted shifts
every later number; ``renumber`` says ``chapter`` | ``sibling`` | ``other``),
``reordered`` (same ``node_fp`` and ``child_set_fp``, different
``subtree_fp``) and ``likely_renamed`` (an added key whose non-empty
``text_fp`` equals a key removed in the same step, same node kind; the pair
is also counted as added and removed). A plan node added or removed means
the plan is absent from one release; the summary lists it under
``absent_plans`` and keeps its rows out of ``edits``.

Backends: ``SqliteHistoryStore(root_dir)`` — every ``history-<mno>.db``
under ``root_dir``, opened ``mode=ro&immutable=1`` (a file with an unknown
``schema_version`` is refused, ``HIS-E006``; unknown columns are ignored);
``HttpHistoryStore(base_url)`` (``clients.py``) — the same protocol over
nora-store's ``/stores/history/v1`` routes. ``open_history_store`` returns
the client for ``url`` (else ``$NORA_HISTORY_URL``), else SQLite for
``root_dir`` (else ``$NORA_HISTORY_ROOT``), else None.

Stdlib only (the HTTP client is imported lazily).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol, runtime_checkable
from urllib.parse import quote

from core.src.models.fingerprint import (
    FINGERPRINT_VERSION,
    child_set_fp,
    node_fp,
    subtree_fp,
    text_fp,
)
from core.src.stores.trees import TreeStore, req_plan, tree_layout

logger = logging.getLogger(__name__)

HISTORY_ROOT_ENV = "NORA_HISTORY_ROOT"
HISTORY_URL_ENV = "NORA_HISTORY_URL"
SQLITE_SCHEMA_VERSION = 1
DB_PREFIX = "history-"
DB_SUFFIX = ".db"

PLAN, SECTION, REQUIREMENT = "plan", "section", "requirement"
CHANGE_KINDS = ("added", "removed", "changed", "moved", "renumbered",
                "reordered", "likely_renamed")
_EMPTY_TEXT_FP = text_fp("", "")


def node_key(kind: str, plan_id: str, local_id: str = "") -> str:
    """Unambiguous node key (a compact JSON list). Consumers look nodes up
    by ``(kind, plan_id, local_id)``; they never parse the key."""
    return json.dumps([kind, plan_id, local_id], ensure_ascii=False,
                      separators=(",", ":"))


def _placed(key: str, location_json: str, sub_fp: str) -> str:
    """A child entry for its parent's subtree/child-set hash."""
    payload = json.dumps([key, location_json, sub_fp], ensure_ascii=False)
    return FINGERPRINT_VERSION + ":" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def word_diff(a: str, b: str) -> list[dict]:
    """Word-level diff of display text: ``[{"op": "eq"|"del"|"add", "text"}]``."""
    aw, bw = (a or "").split(), (b or "").split()
    ops: list[dict] = []
    sm = difflib.SequenceMatcher(None, aw, bw, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            ops.append({"op": "eq", "text": " ".join(aw[i1:i2])})
            continue
        if i2 > i1:
            ops.append({"op": "del", "text": " ".join(aw[i1:i2])})
        if j2 > j1:
            ops.append({"op": "add", "text": " ".join(bw[j1:j2])})
    return ops


# ─── Build: per-release node states from a TreeStore ────────────────


def _s(v: Any) -> str:
    if v is None:
        return ""
    return v if isinstance(v, str) else str(v)


def _is_requirement(req: dict) -> bool:
    flag = req.get("is_requirement")
    return bool(_s(req.get("req_id"))) if flag is None else bool(flag)


def release_nodes(store: TreeStore, mno: str, release: str) -> dict[str, dict]:
    """Every keyed node of one cell with its hashes, by key; dict order is
    document order (docs by doc_id, plans at first sight).

    Node: ``{kind, plan_id, local_id, parent_key, location_json, title,
    text, attrs, text_fp, node_fp, subtree_fp, child_set_fp, children}``.
    """
    nodes: dict[str, dict] = {}
    children: dict[str, list[str]] = {}

    def add(key: str, **fields) -> None:
        nodes[key] = {**fields, "children": []}

    for doc_id in store.list_docs(mno, release):
        tree = store.load_tree(doc_id, mno, release)
        reqs = tree.get("requirements", []) or []
        layout = tree_layout(reqs)
        canon: dict[str, int] = {}
        for i, r in enumerate(reqs):
            rid = _s(r.get("req_id"))
            if rid:
                canon[rid] = i                  # last occurrence wins
        keys: list["str | None"] = [None] * len(reqs)
        for i, r in enumerate(reqs):
            plan = _s(req_plan(r, tree))
            pkey = node_key(PLAN, plan)
            if pkey not in nodes:
                add(pkey, kind=PLAN, plan_id=plan, local_id="", parent_key="",
                    title="", text="", attrs={})
            if not nodes[pkey]["title"] and plan == _s(tree.get("plan_id")):
                nodes[pkey]["title"] = _s(tree.get("plan_name"))
            rid, sec = _s(r.get("req_id")), _s(r.get("section_number"))
            if _is_requirement(r):
                if not rid or canon.get(rid) != i:
                    continue
                key, kind, local = node_key(REQUIREMENT, plan, rid), REQUIREMENT, rid
                attrs = {"priority": _s(r.get("priority")),
                         "applicability": [_s(a) for a in (r.get("applicability") or [])]}
            elif sec:
                key, kind, local = node_key(SECTION, plan, sec), SECTION, sec
                attrs = {}
            else:
                continue
            if key in nodes:                    # repeated key: first wins
                continue
            add(key, kind=kind, plan_id=plan, local_id=local, parent_key="",
                title=_s(r.get("title")), text=_s(r.get("text")), attrs=attrs,
                section=sec)
            keys[i] = key
        # Parents are linked in a second sweep, since a parent may follow its child.
        for i, key in enumerate(keys):
            if key is None:
                continue
            node = nodes[key]
            p = layout[i][0]
            while p is not None and keys[p] is None:
                p = layout[p][0]
            parent = keys[p] if p is not None else None
            if parent is None or nodes[parent]["plan_id"] != node["plan_id"]:
                parent = node_key(PLAN, node["plan_id"])
            node["parent_key"] = parent
            children.setdefault(parent, []).append(key)

    for key, node in nodes.items():
        node["children"] = children.get(key, [])
        loc: dict[str, str] = {}
        if node["kind"] != PLAN:
            loc["parent"] = node["parent_key"]
        if node["kind"] == REQUIREMENT:
            loc["section"] = node.pop("section")
        else:
            node.pop("section", None)
        node["location_json"] = json.dumps(loc, ensure_ascii=False, sort_keys=True)
        node["text_fp"] = text_fp(node["title"], node["text"])
        node["node_fp"] = node_fp(node["text_fp"], node["attrs"].get("priority", ""),
                                  node["attrs"].get("applicability", []))
    # Bottom-up, children in document order (iterative post-order).
    roots = [k for k, n in nodes.items() if n["kind"] == PLAN]
    for root in roots:
        stack = [(root, False)]
        while stack:
            key, expanded = stack.pop()
            node = nodes[key]
            if not expanded:
                stack.append((key, True))
                stack.extend((c, False) for c in reversed(node["children"]))
                continue
            entries = [_placed(c, nodes[c]["location_json"], nodes[c]["subtree_fp"])
                       for c in node["children"]]
            node["subtree_fp"] = subtree_fp(node["node_fp"], entries)
            node["child_set_fp"] = child_set_fp(entries)
    return nodes


# ─── SQLite schema and writer ───────────────────────────────────────

# Postgres-portable: TEXT / INTEGER only. `version` holds one row per
# interval of releases over which a node's (node_fp, subtree_fp, location)
# held; `from_ord` / `to_ord` mirror the labels for range queries, and
# `valid_to` is '' while the interval reaches the latest release. Content
# is addressed by `text_fp`: each distinct title + body is stored once;
# priority and applicability live on the version (`attrs_json`) because
# they are not part of `text_fp`.
_SQLITE_DDL = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE release (
    mno     TEXT NOT NULL,
    release TEXT NOT NULL,
    ord     INTEGER NOT NULL,
    PRIMARY KEY (mno, release)
);
CREATE TABLE node (
    key        TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    plan_id    TEXT NOT NULL,
    local_id   TEXT NOT NULL,
    parent_key TEXT NOT NULL
);
CREATE UNIQUE INDEX node_identity ON node (kind, plan_id, local_id);
CREATE INDEX node_plan ON node (plan_id);
CREATE TABLE version (
    node_key      TEXT NOT NULL REFERENCES node (key),
    valid_from    TEXT NOT NULL,
    valid_to      TEXT NOT NULL,
    from_ord      INTEGER NOT NULL,
    to_ord        INTEGER NOT NULL,
    node_fp       TEXT NOT NULL,
    subtree_fp    TEXT NOT NULL,
    child_set_fp  TEXT NOT NULL,
    text_fp       TEXT NOT NULL REFERENCES content (text_fp),
    attrs_json    TEXT NOT NULL,
    location_json TEXT NOT NULL,
    PRIMARY KEY (node_key, from_ord)
);
CREATE INDEX version_span ON version (to_ord, from_ord);
CREATE TABLE content (
    text_fp TEXT PRIMARY KEY,
    title   TEXT NOT NULL,
    text    TEXT NOT NULL
);
"""

_VERSION_KEYS = ("node_fp", "subtree_fp", "location_json")


def db_name(mno: str) -> str:
    return f"{DB_PREFIX}{mno}{DB_SUFFIX}"


def write_history_db(db_path: "str | Path", mno: str, releases: list[str],
                     states: list[dict[str, dict]], source: dict) -> dict:
    """Write one MNO's DB (the file must not exist). ``states[i]`` is
    ``release_nodes`` for ``releases[i]`` (oldest first). Returns the
    counts. The caller owns atomicity (temp name, verify, rename)."""
    last = len(releases) - 1
    open_v: dict[str, dict] = {}        # key -> the node's latest interval
    versions: list[dict] = []
    nodes: dict[str, dict] = {}
    content: dict[str, tuple[str, str]] = {}
    for ord_, state in enumerate(states):
        for key, n in state.items():
            nodes[key] = n                  # latest sight: identity, parent
            content.setdefault(n["text_fp"], (n["title"], n["text"]))
            v = open_v.get(key)
            if (v is not None and v["to_ord"] == ord_ - 1
                    and all(v[k] == n[k] for k in _VERSION_KEYS)):
                v["to_ord"] = ord_
                continue
            v = {"node_key": key, "from_ord": ord_, "to_ord": ord_,
                 **{k: n[k] for k in ("node_fp", "subtree_fp", "child_set_fp",
                                      "text_fp", "location_json")},
                 "attrs_json": json.dumps(n["attrs"], ensure_ascii=False,
                                          sort_keys=True)}
            open_v[key] = v
            versions.append(v)
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(_SQLITE_DDL)
        con.executemany("INSERT INTO release (mno, release, ord) VALUES (?, ?, ?)",
                        [(mno, r, i) for i, r in enumerate(releases)])
        con.executemany(
            "INSERT INTO node (key, kind, plan_id, local_id, parent_key)"
            " VALUES (?, ?, ?, ?, ?)",
            [(k, n["kind"], n["plan_id"], n["local_id"], n["parent_key"])
             for k, n in sorted(nodes.items())])
        con.executemany(
            "INSERT INTO content (text_fp, title, text) VALUES (?, ?, ?)",
            [(fp, t, x) for fp, (t, x) in sorted(content.items())])
        con.executemany(
            "INSERT INTO version (node_key, valid_from, valid_to, from_ord, to_ord,"
            " node_fp, subtree_fp, child_set_fp, text_fp, attrs_json, location_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(v["node_key"], releases[v["from_ord"]],
              "" if v["to_ord"] == last else releases[v["to_ord"]],
              v["from_ord"], v["to_ord"], v["node_fp"], v["subtree_fp"],
              v["child_set_fp"], v["text_fp"], v["attrs_json"], v["location_json"])
             for v in versions])
        kinds = [n["kind"] for n in nodes.values()]
        counts = {"n_releases": len(releases), "n_nodes": len(nodes),
                  "n_plans": kinds.count(PLAN), "n_sections": kinds.count(SECTION),
                  "n_requirements": kinds.count(REQUIREMENT),
                  "n_versions": len(versions), "n_content": len(content)}
        meta = {
            "schema_version": str(SQLITE_SCHEMA_VERSION),
            "fingerprint_version": FINGERPRINT_VERSION,
            "mno": mno,
            "releases": json.dumps(releases),
            "source": json.dumps(source, sort_keys=True),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **{k: str(v) for k, v in counts.items()},
        }
        con.executemany("INSERT INTO meta (key, value) VALUES (?, ?)",
                        sorted(meta.items()))
        con.commit()
        return counts
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
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    return con


def _check_schema(name: str, meta: dict) -> None:
    """Refuse an MNO DB written for another schema (design §9: the
    rebuild is the migration)."""
    found = str(meta.get("schema_version", "") or "")
    if found != str(SQLITE_SCHEMA_VERSION):
        from core.src.stores.errors import SchemaVersionError
        raise SchemaVersionError(
            "HIS-E006", "History", name, found, str(SQLITE_SCHEMA_VERSION),
            "rebuild the MNO with core.src.stores.history_build or run a "
            "newer build")


# ─── Protocol ────────────────────────────────────────────────────────


@runtime_checkable
class HistoryStore(Protocol):
    """Read interface over release lineage (write-once per MNO).

    Release bounds default to oldest → latest; an unknown release label
    raises ``ValueError``. Diff ops are ``word_diff`` lists.
    """

    def list_mnos(self) -> list[str]: ...
    def releases(self, mno: str) -> list[str]:
        """The MNO's releases, oldest first; [] when unknown."""
        ...
    def req_history(self, mno: str, plan_id: str, req_id: str,
                    from_rel: str = "", to_rel: str = "") -> list[dict]:
        """Versions overlapping the bounds, oldest first: ``{valid_from,
        valid_to, title, text, priority, applicability, location,
        node_fp, subtree_fp, change: [kinds vs the previous version],
        diff: {title, text} | None}``."""
        ...
    def plan_history(self, mno: str, plan_id: str,
                     from_rel: str = "", to_rel: str = "") -> list[dict]:
        """Per release step whose plan subtree changed: ``{from, to,
        changes}`` (``diff_releases`` change rows, with diffs)."""
        ...
    def last_changed(self, mno: str, plan_id: str, req_id: str) -> "str | None": ...
    def plan_last_changed(self, mno: str, plan_id: str) -> "str | None": ...
    def diff_releases(self, mno: str, rel_a: str, rel_b: str,
                      diffs: bool = False) -> dict: ...
    def latest_changes(self, mno: str, diffs: bool = False) -> "dict | None":
        """``diff_releases`` of the last two releases; None with fewer."""
        ...


# ─── SQLite backend ──────────────────────────────────────────────────

_STATE_SQL = (
    "SELECT n.key, n.kind, n.plan_id, n.local_id, v.node_fp, v.subtree_fp,"
    " v.child_set_fp, v.text_fp, v.attrs_json, v.location_json"
    " FROM version v JOIN node n ON n.key = v.node_key"
    " WHERE v.from_ord <= ? AND v.to_ord >= ?")


class SqliteHistoryStore:
    """Every ``history-<mno>.db`` under ``root_dir`` (or the explicit
    ``db_paths``). The MNO comes from each file's ``meta``. Per-call
    read-only connections; a rebuilt file is picked up by its changed
    mtime/size."""

    def __init__(self, root_dir: "str | Path" = "",
                 db_paths: "Iterable[str | Path] | None" = None):
        self.root = Path(root_dir) if root_dir else None
        self._explicit = [Path(p) for p in db_paths] if db_paths is not None else None
        self._meta_cache: dict[str, tuple[float, int, dict]] = {}

    # -- discovery -------------------------------------------------------

    def _files(self) -> list[Path]:
        if self._explicit is not None:
            return list(self._explicit)
        if self.root is None or not self.root.is_dir():
            return []
        return sorted(self.root.glob(f"{DB_PREFIX}*{DB_SUFFIX}"))

    def _dbs(self) -> dict[str, Path]:
        found: dict[str, Path] = {}
        for p in self._files():
            try:
                st = p.stat()
                hit = self._meta_cache.get(str(p))
                if hit is None or hit[0] != st.st_mtime or hit[1] != st.st_size:
                    hit = (st.st_mtime, st.st_size, read_meta(p))
                    self._meta_cache[str(p)] = hit
            except (OSError, sqlite3.Error) as exc:
                logger.warning("Skipping unreadable history DB %s: %s", p.name,
                               type(exc).__name__)
                continue
            _check_schema(p.name, hit[2])
            mno = hit[2].get("mno", "")
            if mno in found:
                logger.warning("Duplicate history DB for one MNO; keeping %s, "
                               "ignoring %s", found[mno].name, p.name)
                continue
            found[mno] = p
        return found

    def _con(self, mno: str) -> "sqlite3.Connection | None":
        p = self._dbs().get(mno)
        return _connect_ro(p) if p is not None else None

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _releases(con: sqlite3.Connection) -> list[str]:
        return [r[0] for r in con.execute("SELECT release FROM release ORDER BY ord")]

    @staticmethod
    def _bounds(rels: list[str], from_rel: str, to_rel: str) -> tuple[int, int]:
        def pos(label: str, default: int) -> int:
            if not label:
                return default
            if label not in rels:
                raise ValueError(f"unknown release: {label}")
            return rels.index(label)
        return pos(from_rel, 0), pos(to_rel, len(rels) - 1)

    @staticmethod
    def _state(con: sqlite3.Connection, ord_: int, where: str = "",
               args: tuple = ()) -> dict[str, dict]:
        return {r["key"]: dict(r) for r in con.execute(
            _STATE_SQL + where, (ord_, ord_, *args))}

    @staticmethod
    def _content(con: sqlite3.Connection, fps: Iterable[str]) -> dict[str, tuple]:
        fps = sorted(set(fps))
        out: dict[str, tuple] = {}
        for i in range(0, len(fps), 500):
            chunk = fps[i:i + 500]
            marks = ", ".join("?" * len(chunk))
            out.update((r[0], (r[1], r[2])) for r in con.execute(
                f"SELECT text_fp, title, text FROM content WHERE text_fp IN ({marks})",
                chunk))
        return out

    @staticmethod
    def _versions(con: sqlite3.Connection, key: str) -> list[dict]:
        return [dict(r) for r in con.execute(
            "SELECT * FROM version WHERE node_key = ? ORDER BY from_ord", (key,))]

    # -- reads -------------------------------------------------------------

    def list_mnos(self) -> list[str]:
        return sorted(self._dbs())

    def releases(self, mno: str) -> list[str]:
        con = self._con(mno)
        if con is None:
            return []
        try:
            return self._releases(con)
        finally:
            con.close()

    def req_history(self, mno: str, plan_id: str, req_id: str,
                    from_rel: str = "", to_rel: str = "") -> list[dict]:
        return self._node_history(mno, node_key(REQUIREMENT, plan_id, req_id),
                                  from_rel, to_rel)

    def _node_history(self, mno: str, key: str, from_rel: str,
                      to_rel: str) -> list[dict]:
        con = self._con(mno)
        if con is None:
            return []
        try:
            lo, hi = self._bounds(self._releases(con), from_rel, to_rel)
            vs = self._versions(con, key)
            texts = self._content(con, (v["text_fp"] for v in vs))
        finally:
            con.close()
        out: list[dict] = []
        for i, v in enumerate(vs):
            if v["to_ord"] < lo or v["from_ord"] > hi:
                continue
            prev = vs[i - 1] if i else None
            title, text = texts[v["text_fp"]]
            attrs = json.loads(v["attrs_json"])
            row = {"valid_from": v["valid_from"], "valid_to": v["valid_to"],
                   "title": title, "text": text,
                   "priority": attrs.get("priority", ""),
                   "applicability": attrs.get("applicability", []),
                   "location": json.loads(v["location_json"]),
                   "node_fp": v["node_fp"], "subtree_fp": v["subtree_fp"],
                   "change": _version_change(prev, v), "diff": None}
            if prev is not None and prev["text_fp"] != v["text_fp"]:
                pt, px = texts[prev["text_fp"]]
                row["diff"] = {"title": word_diff(pt, title) if pt != title else None,
                               "text": word_diff(px, text) if px != text else None}
            out.append(row)
        return out

    def last_changed(self, mno: str, plan_id: str, req_id: str) -> "str | None":
        """The release that last brought a new ``node_fp``: a requirement
        that never changed reports the release it was added in; a removal
        and identical re-add does not count (``req_history`` marks it
        ``readded``); a move does not count."""
        return self._last_new(mno, node_key(REQUIREMENT, plan_id, req_id), "node_fp")

    def plan_last_changed(self, mno: str, plan_id: str) -> "str | None":
        return self._last_new(mno, node_key(PLAN, plan_id), "subtree_fp")

    def _last_new(self, mno: str, key: str, col: str) -> "str | None":
        """The latest ``valid_from`` that brought a new ``col`` value."""
        con = self._con(mno)
        if con is None:
            return None
        try:
            vs = self._versions(con, key)
        finally:
            con.close()
        found = None
        for i, v in enumerate(vs):
            if i == 0 or vs[i - 1][col] != v[col]:
                found = v["valid_from"]
        return found

    def diff_releases(self, mno: str, rel_a: str, rel_b: str,
                      diffs: bool = False) -> dict:
        con = self._con(mno)
        if con is None:
            raise ValueError(f"unknown MNO: {mno}")
        try:
            rels = self._releases(con)
            a, b = self._bounds(rels, rel_a or rels[0], rel_b or rels[-1])
            changes = self._diff(con, a, b, None, diffs)
        finally:
            con.close()
        return _summary(mno, rels[a], rels[b], changes)

    def latest_changes(self, mno: str, diffs: bool = False) -> "dict | None":
        rels = self.releases(mno)
        if len(rels) < 2:
            return None
        return self.diff_releases(mno, rels[-2], rels[-1], diffs)

    def plan_history(self, mno: str, plan_id: str,
                     from_rel: str = "", to_rel: str = "") -> list[dict]:
        con = self._con(mno)
        if con is None:
            return []
        try:
            rels = self._releases(con)
            lo, hi = self._bounds(rels, from_rel, to_rel)
            steps: list[dict] = []
            for o in range(lo + 1, hi + 1):
                changes = self._diff(con, o - 1, o, plan_id, True)
                if changes:
                    steps.append({"from": rels[o - 1], "to": rels[o],
                                  "changes": changes})
            return steps
        finally:
            con.close()

    # -- the diff ------------------------------------------------------------

    def _diff(self, con: sqlite3.Connection, a: int, b: int,
              plan_id: "str | None", diffs: bool) -> list[dict]:
        """Change rows between ord ``a`` and ord ``b``: plans first, then
        only into nodes whose ``subtree_fp`` differs."""
        where, args = " AND n.kind = ?", (PLAN,)
        if plan_id is not None:
            where, args = where + " AND n.plan_id = ?", (PLAN, plan_id)
        pa, pb = self._state(con, a, where, args), self._state(con, b, where, args)
        changes: list[dict] = []
        for pkey in sorted(set(pa) | set(pb)):
            x, y = pa.get(pkey), pb.get(pkey)
            if x is not None and y is not None and x["subtree_fp"] == y["subtree_fp"]:
                continue
            plan = (x or y)["plan_id"]
            sa = self._state(con, a, " AND n.plan_id = ?", (plan,))
            sb = self._state(con, b, " AND n.plan_id = ?", (plan,))
            changes.extend(_descend(pkey, sa, sb))
        _pair_renames(changes)
        if diffs and changes:
            texts = self._content(con, [fp for c in changes
                                        for fp in (c["_fa"], c["_fb"]) if fp])
            for c in changes:
                c["diff"] = _change_diff(c, texts)
        for c in changes:
            c.pop("_fa", None)
            c.pop("_fb", None)
        return changes


def _location_kinds(loc_a: str, loc_b: str, sa: "dict | None" = None,
                    sb: "dict | None" = None) -> tuple[list[str], str, str]:
    """Kinds for a location change. A different parent is ``moved`` —
    unless the old parent vanished and the new one appeared with the same
    content (a renumbered parent section), which is ``renumbered`` with
    detail ``parent``; that check needs both releases' node states
    (``sa``/``sb``) and is skipped without them. The same parent with a
    different outline number is ``renumbered`` with detail ``chapter``
    (first component changed), ``sibling`` (last) or ``other``. Returns
    (kinds, renumber detail, old section)."""
    if loc_a == loc_b:
        return [], "", ""
    a, b = json.loads(loc_a), json.loads(loc_b)
    pa, pb = a.get("parent"), b.get("parent")
    if pa != pb:
        if sa is not None and sb is not None:
            x, y = sa.get(pa), sb.get(pb)
            if (x and y and pa not in sb and pb not in sa and x["kind"] == y["kind"]
                    and x["text_fp"] == y["text_fp"] and x["text_fp"] != _EMPTY_TEXT_FP):
                return ["renumbered"], "parent", a.get("section", "")
        return ["moved"], "", a.get("section", "")
    sa, sb = str(a.get("section", "")), str(b.get("section", ""))
    if sa == sb:
        return [], "", ""
    pa, pb = sa.split("."), sb.split(".")
    if len(pa) == len(pb) and pa[1:] == pb[1:]:
        detail = "chapter"
    elif len(pa) == len(pb) and pa[:-1] == pb[:-1]:
        detail = "sibling"
    else:
        detail = "other"
    return ["renumbered"], detail, sa


def _version_change(prev: "dict | None", v: dict) -> list[str]:
    if prev is None:
        return ["added"]
    kinds = []
    if prev["to_ord"] < v["from_ord"] - 1:
        kinds.append("readded")
    if prev["node_fp"] != v["node_fp"]:
        kinds.append("changed")
    kinds.extend(_location_kinds(prev["location_json"], v["location_json"])[0])
    if prev["node_fp"] == v["node_fp"] and prev["subtree_fp"] != v["subtree_fp"]:
        # the node itself held; something under it changed
        same_set = prev["child_set_fp"] == v["child_set_fp"]
        kinds.append("reordered" if same_set else "subtree")
    return kinds


def _descend(root: str, sa: dict[str, dict], sb: dict[str, dict]) -> list[dict]:
    kids_a: dict[str, list[str]] = {}
    kids_b: dict[str, list[str]] = {}
    for s, kids in ((sa, kids_a), (sb, kids_b)):
        for k in sorted(s):
            parent = json.loads(s[k]["location_json"]).get("parent")
            if parent:
                kids.setdefault(parent, []).append(k)
    out: list[dict] = []
    seen: set[str] = set()
    stack = [root]
    while stack:
        key = stack.pop()
        if key in seen:
            continue
        seen.add(key)
        x, y = sa.get(key), sb.get(key)
        kinds: list[str] = []
        renumber, old_section = "", ""
        if x is None:
            kinds.append("added")
        elif y is None:
            kinds.append("removed")
        else:
            if x["node_fp"] != y["node_fp"]:
                kinds.append("changed")
            lk, renumber, old_section = _location_kinds(x["location_json"], y["location_json"], sa, sb)
            kinds.extend(lk)
            if (x["node_fp"] == y["node_fp"] and x["subtree_fp"] != y["subtree_fp"]
                    and x["child_set_fp"] == y["child_set_fp"]):
                kinds.append("reordered")
        if kinds:
            n = y or x
            loc = json.loads(n["location_json"])
            parent = loc.get("parent") or ""
            pnode = sb.get(parent) or sa.get(parent)
            out.append({"key": key, "node": n["kind"], "plan_id": n["plan_id"],
                        "local_id": n["local_id"],
                        # what /req/last-changed and req_history take
                        "req_id": n["local_id"] if n["kind"] == REQUIREMENT else "",
                        "section": (n["local_id"] if n["kind"] == SECTION
                                    else loc.get("section", "")),
                        # the containing section: a section is its own;
                        # a requirement's is its parent section ("" = plan level)
                        "parent_section": (n["local_id"] if n["kind"] == SECTION
                                           else (pnode["local_id"]
                                                 if pnode and pnode["kind"] == SECTION
                                                 else "")),
                        "change": kinds,
                        **({"renumber": renumber, "renumbered_from": old_section}
                           if "renumbered" in kinds else {}),
                        **({"moved_from": old_section} if "moved" in kinds else {}),
                        "_fa": x["text_fp"] if x else "",
                        "_fb": y["text_fp"] if y else ""})
        if x is None or y is None or x["subtree_fp"] != y["subtree_fp"]:
            stack.extend(sorted(set(kids_a.get(key, [])) | set(kids_b.get(key, [])),
                                reverse=True))
    return out


def _pair_renames(changes: list[dict]) -> None:
    """Mark an added key whose text_fp equals a removed key's (same node
    kind, non-empty content) as ``likely_renamed``; pairs one-to-one in
    key order."""
    removed: dict[tuple, list[dict]] = {}
    for c in sorted(changes, key=lambda c: c["key"]):
        if "removed" in c["change"] and c["_fa"] != _EMPTY_TEXT_FP:
            removed.setdefault((c["node"], c["_fa"]), []).append(c)
    for c in sorted(changes, key=lambda c: c["key"]):
        if "added" in c["change"] and c["_fb"] != _EMPTY_TEXT_FP:
            pool = removed.get((c["node"], c["_fb"]))
            if pool:
                c["change"].append("likely_renamed")
                c["renamed_from"] = pool.pop(0)["key"]


def _change_diff(c: dict, texts: dict[str, tuple]) -> "dict | None":
    fa, fb = c.get("_fa"), c.get("_fb")
    if fa == fb:
        return None
    ta, xa = texts.get(fa, ("", "")) if fa else ("", "")
    tb, xb = texts.get(fb, ("", "")) if fb else ("", "")
    return {"title": word_diff(ta, tb) if ta != tb else None,
            "text": word_diff(xa, xb) if xa != xb else None}


def _summary(mno: str, rel_a: str, rel_b: str, changes: list[dict]) -> dict:
    """Deterministic counts per kind, per plan and per containing section.

    Kinds overlap on a row (a renamed pair is also added + removed; a row
    can be changed and renumbered), so ``counts`` are kind tallies over
    every row, ``edits`` the same tallies without the rows of plans absent
    from one release (``absent_plans``: a plan node added or removed),
    ``rows`` the number of change rows and ``renamed_pairs`` the number of
    rename annotations. Sections aggregate by the row's containing
    section (``parent_section``) and are ranked by rows, so a plan with
    one requirement per numbered section still ranks its sections."""
    def zero() -> dict[str, int]:
        return {k: 0 for k in CHANGE_KINDS}

    counts = zero()
    edits = zero()
    absent: dict[str, dict] = {}
    for c in changes:
        if c["node"] == PLAN and ("removed" in c["change"] or "added" in c["change"]):
            absent[c["plan_id"]] = {"absent_in": rel_b if "removed" in c["change"] else rel_a,
                                    "rows": 0}
    plans: dict[str, dict] = {}
    for c in changes:
        p = plans.setdefault(c["plan_id"], {"counts": zero(), "rows": 0, "sections": {},
                                            "present": {"from": True, "to": True}})
        p["rows"] += 1
        if c["plan_id"] in absent:
            absent[c["plan_id"]]["rows"] += 1
            p["present"]["to" if absent[c["plan_id"]]["absent_in"] == rel_b else "from"] = False
        else:
            for k in c["change"]:
                edits[k] += 1
        sec = p["sections"].setdefault(c.get("parent_section", c["section"]),
                                       {"rows": 0, **zero()})
        sec["rows"] += 1
        for k in c["change"]:
            counts[k] += 1
            p["counts"][k] += 1
            sec[k] += 1
    ranked = sorted(((v["rows"], plan, sec, v)
                     for plan, p in plans.items() for sec, v in p["sections"].items()),
                    key=lambda t: (-t[0], t[1], t[2]))
    return {
        "mno": mno, "from": rel_a, "to": rel_b,
        "counts": counts,
        "edits": edits,
        "absent_plans": {k: absent[k] for k in sorted(absent)},
        "rows": len(changes),
        "renamed_pairs": sum(1 for c in changes if "likely_renamed" in c["change"]),
        "plans": {k: plans[k] for k in sorted(plans)},
        "most_changed_sections": [
            {"plan_id": p, "section": s, "rows": n,
             "kinds": {k: v[k] for k in CHANGE_KINDS if v[k]}}
            for n, p, s, v in ranked[:10]],
        "changes": sorted(changes, key=lambda c: (c["plan_id"], c["key"])),
    }


def open_history_store(root_dir: "str | Path" = "",
                       url: "str | None" = None) -> "HistoryStore | None":
    """``url`` (None → ``$NORA_HISTORY_URL``) → HTTP client on nora-store;
    else ``root_dir`` (else ``$NORA_HISTORY_ROOT``) → SQLite; else
    ``$NORA_STORE_REGISTRY`` → the registry's served History versions;
    else None (history disabled). Pass ``url=""`` to ignore the URL env
    var."""
    base = (os.getenv(HISTORY_URL_ENV, "") if url is None else url).strip()
    if base:
        from core.src.stores.clients import HttpHistoryStore
        return HttpHistoryStore(base)
    root = str(root_dir or os.getenv(HISTORY_ROOT_ENV, "")).strip()
    if root:
        return SqliteHistoryStore(root)
    from core.src.stores.registry import REGISTRY_ENV, registry_store_paths
    if os.getenv(REGISTRY_ENV, "").strip():
        return SqliteHistoryStore(db_paths=registry_store_paths("history"))
    return None
