"""Publish registry (strand corp-db-layer phase 4b; publish-design.md §3).

One SQLite file per serving root, ``<root>/registry.db``. It records
which **store versions** exist (one write-once file per unit and version:
``cells/cell-<mno>-<rel>-v<N>.db``, ``trees/trees-<mno>-<rel>-v<N>.db``,
``history/history-<mno>-v<N>.db``), groups them into **release sets**
(named by the build label), records **gate runs** per set, and holds the
one row set serving reads at start: **active**. Every flip or rollback is
one transaction over ``active`` plus a ``flip`` row.

Serving reads it (``NORA_STORE_REGISTRY``; ``NORA_STORE_RELEASE_SET``
serves a staged set instead of ``active``); only the publish CLI
(``core.src.stores.publish``) writes it. Stdlib only.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

REGISTRY_ENV = "NORA_STORE_REGISTRY"
RELEASE_SET_ENV = "NORA_STORE_RELEASE_SET"
REGISTRY_NAME = "registry.db"
SCHEMA_VERSION = "1"

STORES = ("cells", "trees", "history")
GATE_KINDS = ("golden-stage1", "golden-stage2", "manual")
VERDICTS = ("pass", "fail", "waived")
FLIP_KINDS = ("flip", "rollback")
FLIP_MODES = ("staged", "expedited", "override")

# Postgres-portable: TEXT / INTEGER only, no AUTOINCREMENT, no triggers.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS store_version(
  store          TEXT    NOT NULL,
  unit           TEXT    NOT NULL,
  version        INTEGER NOT NULL,
  fingerprint    TEXT    NOT NULL,
  file           TEXT    NOT NULL,
  sha256         TEXT    NOT NULL,
  bytes          INTEGER NOT NULL,
  schema_version TEXT    NOT NULL DEFAULT '',
  fingerprint_version TEXT NOT NULL DEFAULT '',
  staged_at      TEXT    NOT NULL,
  staged_by      TEXT    NOT NULL DEFAULT '',
  removed_at     TEXT    NOT NULL DEFAULT '',
  PRIMARY KEY (store, unit, version)
);
CREATE INDEX IF NOT EXISTS store_version_fp
  ON store_version(store, unit, fingerprint);
CREATE TABLE IF NOT EXISTS release_set(
  label         TEXT PRIMARY KEY,
  created_at    TEXT NOT NULL,
  created_by    TEXT NOT NULL DEFAULT '',
  note          TEXT NOT NULL DEFAULT '',
  code_version  TEXT NOT NULL DEFAULT '',
  prompt_scheme TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS release_set_member(
  label   TEXT    NOT NULL,
  store   TEXT    NOT NULL,
  unit    TEXT    NOT NULL,
  version INTEGER NOT NULL,
  PRIMARY KEY (label, store, unit)
);
CREATE TABLE IF NOT EXISTS gate_run(
  id          INTEGER PRIMARY KEY,
  label       TEXT NOT NULL,
  kind        TEXT NOT NULL,
  run_ref     TEXT NOT NULL DEFAULT '',
  verdict     TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  recorded_by TEXT NOT NULL DEFAULT '',
  note        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS gate_run_label ON gate_run(label);
CREATE TABLE IF NOT EXISTS active(
  store   TEXT    NOT NULL,
  unit    TEXT    NOT NULL,
  version INTEGER NOT NULL,
  label   TEXT    NOT NULL,
  since   TEXT    NOT NULL,
  PRIMARY KEY (store, unit)
);
CREATE TABLE IF NOT EXISTS flip(
  id         INTEGER PRIMARY KEY,
  label      TEXT NOT NULL,
  kind       TEXT NOT NULL,
  from_label TEXT NOT NULL DEFAULT '',
  at         TEXT NOT NULL,
  by         TEXT NOT NULL DEFAULT '',
  mode       TEXT NOT NULL DEFAULT '',
  note       TEXT NOT NULL DEFAULT ''
);
"""


class RegistryError(Exception):
    """A registry operation that cannot proceed (coded, content-free)."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"[{code}] {detail}")


@dataclass(frozen=True)
class StoreVersion:
    store: str
    unit: str
    version: int
    fingerprint: str
    file: str            # relative to the serving root
    sha256: str
    bytes: int
    schema_version: str = ""
    staged_at: str = ""
    staged_by: str = ""
    removed_at: str = ""
    fingerprint_version: str = ""   # canonical-form version of the file's hashes

    @property
    def key(self) -> tuple[str, str]:
        return (self.store, self.unit)


@dataclass(frozen=True)
class NewUnit:
    """A unit the publisher stages: its identity and the staged file."""
    store: str
    unit: str
    fingerprint: str
    file: str
    sha256: str
    bytes: int
    schema_version: str = ""
    fingerprint_version: str = ""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def unit_name(mno: str, release: str = "") -> str:
    """Cells / Trees units are ``<mno>__<release>``; History units ``<mno>``."""
    return f"{mno}__{release}" if release else mno


def split_unit(unit: str) -> tuple[str, str]:
    mno, _, rel = unit.partition("__")
    return mno, rel


def history_fingerprint(trees_fps: dict[str, str]) -> str:
    """Identity of a History unit: a hash over its input Trees versions
    (release → Trees ``source_fingerprint``), release order independent."""
    h = hashlib.sha256()
    for rel in sorted(trees_fps):
        h.update(rel.encode("utf-8") + b"\0" + trees_fps[rel].encode("utf-8") + b"\n")
    return h.hexdigest()


def registry_path(serve_root: "str | Path") -> Path:
    return Path(serve_root) / REGISTRY_NAME


class Registry:
    """Reader and (for the publish CLI) writer of one ``registry.db``.

    Per-call connections; ``read_only=True`` opens with ``mode=ro`` and
    never creates the file (a missing registry raises ``RegistryError``
    ``REG-E001`` on first use). Writers call ``ensure()`` once."""

    def __init__(self, path: "str | Path", read_only: bool = True,
                 busy_timeout_ms: int = 5000):
        self.path = Path(path)
        self.read_only = read_only
        self._busy = busy_timeout_ms

    @property
    def root(self) -> Path:
        """The serving root: the directory holding the registry file."""
        return self.path.parent

    # -- connections -----------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if self.read_only:
            if not self.path.is_file():
                raise RegistryError("REG-E001", f"registry not found: {self.path}")
            uri = f"file:{quote(str(self.path.resolve()))}?mode=ro"
            con = sqlite3.connect(uri, uri=True, timeout=self._busy / 1000)
        else:
            con = sqlite3.connect(str(self.path), timeout=self._busy / 1000,
                                  isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute(f"PRAGMA busy_timeout={int(self._busy)}")
        return con

    def ensure(self) -> None:
        """Create the schema (writer only; idempotent)."""
        if self.read_only:
            raise RegistryError("REG-E002", "registry opened read-only")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            for stmt in _SCHEMA.split(";"):      # executescript would commit
                if stmt.strip():
                    con.execute(stmt)
            # additive column (D-281: no version bump, readers tolerate absence)
            cols = {r[1] for r in con.execute("PRAGMA table_info(store_version)")}
            if "fingerprint_version" not in cols:
                con.execute("ALTER TABLE store_version ADD COLUMN fingerprint_version "
                            "TEXT NOT NULL DEFAULT ''")
            found = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if found is None:
                con.execute("INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
                            (SCHEMA_VERSION,))
                con.execute("INSERT INTO meta(key, value) VALUES ('created_at', ?)",
                            (now_iso(),))
            elif found[0] != SCHEMA_VERSION:
                con.execute("ROLLBACK")
                raise RegistryError(
                    "REG-E003",
                    f"registry schema_version {found[0]!r} unsupported "
                    f"(this build writes {SCHEMA_VERSION})")
            con.execute("COMMIT")
        finally:
            con.close()

    def exists(self) -> bool:
        return self.path.is_file()

    # -- store versions ----------------------------------------------------

    @staticmethod
    def _sv(row: sqlite3.Row) -> StoreVersion:
        return StoreVersion(
            store=row["store"], unit=row["unit"], version=int(row["version"]),
            fingerprint=row["fingerprint"], file=row["file"], sha256=row["sha256"],
            bytes=int(row["bytes"]), schema_version=row["schema_version"],
            staged_at=row["staged_at"], staged_by=row["staged_by"],
            removed_at=row["removed_at"],
            fingerprint_version=(row["fingerprint_version"]
                                 if "fingerprint_version" in row.keys() else ""))

    def find_version(self, store: str, unit: str, fingerprint: str,
                     schema_version: "str | None" = "",
                     fingerprint_version: "str | None" = None) -> "StoreVersion | None":
        """The newest live (not removed) version of a unit with this content
        identity, or None. ``schema_version`` must match too (None = any):
        the same source converted by a newer schema is new content for
        serving; so is the same source hashed under a newer canonical form
        (``fingerprint_version``; None = any)."""
        con = self._connect()
        try:
            sql = ("SELECT * FROM store_version WHERE store=? AND unit=? AND fingerprint=? "
                   "AND removed_at=''")
            args: list = [store, unit, fingerprint]
            if schema_version is not None:
                sql += " AND schema_version=?"
                args.append(schema_version)
            if fingerprint_version is not None:
                sql += " AND fingerprint_version=?"
                args.append(fingerprint_version)
            row = con.execute(sql + " ORDER BY version DESC LIMIT 1", args).fetchone()
            return self._sv(row) if row else None
        finally:
            con.close()

    def get_version(self, store: str, unit: str, version: int) -> "StoreVersion | None":
        con = self._connect()
        try:
            row = con.execute(
                "SELECT * FROM store_version WHERE store=? AND unit=? AND version=?",
                (store, unit, version)).fetchone()
            return self._sv(row) if row else None
        finally:
            con.close()

    def versions(self, store: str = "", unit: str = "") -> list[StoreVersion]:
        con = self._connect()
        try:
            sql, args = "SELECT * FROM store_version", []
            conds = []
            if store:
                conds.append("store=?"); args.append(store)
            if unit:
                conds.append("unit=?"); args.append(unit)
            if conds:
                sql += " WHERE " + " AND ".join(conds)
            sql += " ORDER BY store, unit, version"
            return [self._sv(r) for r in con.execute(sql, args)]
        finally:
            con.close()

    def next_version(self, con: sqlite3.Connection, store: str, unit: str) -> int:
        row = con.execute(
            "SELECT COALESCE(MAX(version), 0) FROM store_version WHERE store=? AND unit=?",
            (store, unit)).fetchone()
        return int(row[0]) + 1

    # -- release sets ------------------------------------------------------

    def begin_write(self) -> sqlite3.Connection:
        """An open connection holding the write lock (``BEGIN IMMEDIATE``).
        The publisher stages files under this lock so version numbers it
        assigned stay its own; it ends with ``commit_write`` or
        ``rollback_write``."""
        if self.read_only:
            raise RegistryError("REG-E002", "registry opened read-only")
        con = self._connect()
        con.execute("BEGIN IMMEDIATE")
        return con

    @staticmethod
    def commit_write(con: sqlite3.Connection) -> None:
        try:
            con.execute("COMMIT")
        finally:
            con.close()

    @staticmethod
    def rollback_write(con: sqlite3.Connection) -> None:
        try:
            con.execute("ROLLBACK")
        finally:
            con.close()

    def record_release_set(self, con: sqlite3.Connection, label: str,
                           new_units: Iterable[NewUnit],
                           reused: Iterable[StoreVersion], *,
                           versions: "dict[tuple[str, str], int] | None" = None,
                           created_by: str = "", note: str = "",
                           code_version: str = "", prompt_scheme: str = "",
                           at: str = "") -> list[StoreVersion]:
        """Inside an open write (``begin_write``): insert the new
        ``store_version`` rows (version numbers from ``versions``, else
        assigned now), the ``release_set`` and every member. Returns the
        set's members as ``StoreVersion`` rows."""
        at = at or now_iso()
        if con.execute("SELECT 1 FROM release_set WHERE label=?", (label,)).fetchone():
            raise RegistryError("REG-E004", f"release set {label!r} already exists")
        members: list[StoreVersion] = []
        for nu in new_units:
            if nu.store not in STORES:
                raise RegistryError("REG-E005", f"unknown store {nu.store!r}")
            ver = (versions or {}).get((nu.store, nu.unit)) or self.next_version(con, nu.store, nu.unit)
            con.execute(
                "INSERT INTO store_version(store, unit, version, fingerprint, file, sha256, "
                "bytes, schema_version, fingerprint_version, staged_at, staged_by) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (nu.store, nu.unit, ver, nu.fingerprint, nu.file, nu.sha256, nu.bytes,
                 nu.schema_version, nu.fingerprint_version, at, created_by))
            members.append(StoreVersion(nu.store, nu.unit, ver, nu.fingerprint, nu.file,
                                        nu.sha256, nu.bytes, nu.schema_version, at,
                                        created_by, fingerprint_version=nu.fingerprint_version))
        members.extend(reused)
        seen: set[tuple[str, str]] = set()
        for m in members:
            if m.key in seen:
                raise RegistryError("REG-E006", f"unit {m.store}/{m.unit} listed twice")
            seen.add(m.key)
        con.execute(
            "INSERT INTO release_set(label, created_at, created_by, note, code_version, "
            "prompt_scheme) VALUES (?,?,?,?,?,?)",
            (label, at, created_by, note, code_version, prompt_scheme))
        con.executemany(
            "INSERT INTO release_set_member(label, store, unit, version) VALUES (?,?,?,?)",
            [(label, m.store, m.unit, m.version) for m in members])
        return sorted(members, key=lambda m: (m.store, m.unit))

    def release_set(self, label: str) -> "dict | None":
        con = self._connect()
        try:
            row = con.execute("SELECT * FROM release_set WHERE label=?", (label,)).fetchone()
            return dict(row) if row else None
        finally:
            con.close()

    def release_sets(self) -> list[dict]:
        """Every release set, newest first."""
        con = self._connect()
        try:
            return [dict(r) for r in con.execute(
                "SELECT * FROM release_set ORDER BY created_at DESC, label DESC")]
        finally:
            con.close()

    def members(self, label: str) -> list[StoreVersion]:
        con = self._connect()
        try:
            if not con.execute("SELECT 1 FROM release_set WHERE label=?", (label,)).fetchone():
                raise RegistryError("REG-E007", f"unknown release set {label!r}")
            rows = con.execute(
                "SELECT v.* FROM release_set_member m JOIN store_version v "
                "ON v.store=m.store AND v.unit=m.unit AND v.version=m.version "
                "WHERE m.label=? ORDER BY v.store, v.unit", (label,))
            return [self._sv(r) for r in rows]
        finally:
            con.close()

    # -- gates ---------------------------------------------------------------

    def record_gate(self, label: str, kind: str, verdict: str, *, run_ref: str = "",
                    by: str = "", note: str = "", at: str = "") -> int:
        if kind not in GATE_KINDS:
            raise RegistryError("REG-E008", f"gate kind must be one of {', '.join(GATE_KINDS)}")
        if verdict not in VERDICTS:
            raise RegistryError("REG-E008", f"verdict must be one of {', '.join(VERDICTS)}")
        if self.release_set(label) is None:
            raise RegistryError("REG-E007", f"unknown release set {label!r}")
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            cur = con.execute(
                "INSERT INTO gate_run(label, kind, run_ref, verdict, recorded_at, recorded_by, "
                "note) VALUES (?,?,?,?,?,?,?)",
                (label, kind, run_ref, verdict, at or now_iso(), by, note))
            con.execute("COMMIT")
            return int(cur.lastrowid)
        finally:
            con.close()

    def gates(self, label: str) -> list[dict]:
        con = self._connect()
        try:
            return [dict(r) for r in con.execute(
                "SELECT * FROM gate_run WHERE label=? ORDER BY id", (label,))]
        finally:
            con.close()

    def gate_verdict(self, label: str) -> tuple[bool, dict[str, str]]:
        """``(ok, {kind: latest verdict})``. The latest run per kind decides
        that kind; the set may flip when at least one kind passed (or was
        waived) and no kind's latest run failed."""
        latest: dict[str, str] = {}
        for g in self.gates(label):          # ascending id → last wins
            latest[g["kind"]] = g["verdict"]
        ok = bool(latest) and all(v in ("pass", "waived") for v in latest.values())
        return ok, latest

    # -- active / flips --------------------------------------------------------

    def active(self) -> list[dict]:
        con = self._connect()
        try:
            return [dict(r) for r in con.execute(
                "SELECT * FROM active ORDER BY store, unit")]
        finally:
            con.close()

    def active_label(self) -> str:
        """The label every active row came from; '' when none or mixed."""
        labels = {r["label"] for r in self.active()}
        return labels.pop() if len(labels) == 1 else ""

    def flips(self, limit: int = 0) -> list[dict]:
        """Flip log, newest first."""
        con = self._connect()
        try:
            sql = "SELECT * FROM flip ORDER BY id DESC"
            if limit:
                sql += f" LIMIT {int(limit)}"
            return [dict(r) for r in con.execute(sql)]
        finally:
            con.close()

    def served_before(self, label: str) -> bool:
        con = self._connect()
        try:
            return con.execute("SELECT 1 FROM flip WHERE label=? LIMIT 1",
                               (label,)).fetchone() is not None
        finally:
            con.close()

    def flip(self, label: str, *, kind: str = "flip", mode: str = "staged",
             by: str = "", note: str = "", at: str = "") -> dict:
        """Make ``active`` equal to the set's members and log the flip —
        one transaction. Returns ``{from_label, label, changed: [...],
        removed: [...]}`` (units whose version changed / disappeared)."""
        if kind not in FLIP_KINDS:
            raise RegistryError("REG-E008", f"flip kind must be one of {', '.join(FLIP_KINDS)}")
        if mode not in FLIP_MODES:
            raise RegistryError("REG-E008", f"flip mode must be one of {', '.join(FLIP_MODES)}")
        members = self.members(label)
        at = at or now_iso()
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            before = {(r["store"], r["unit"]): (int(r["version"]), r["label"])
                      for r in con.execute("SELECT * FROM active")}
            labels = {v[1] for v in before.values()}
            last = con.execute("SELECT label FROM flip ORDER BY id DESC LIMIT 1").fetchone()
            from_label = labels.pop() if len(labels) == 1 else (last["label"] if last else "")
            con.execute("DELETE FROM active")
            con.executemany(
                "INSERT INTO active(store, unit, version, label, since) VALUES (?,?,?,?,?)",
                [(m.store, m.unit, m.version, label, at) for m in members])
            con.execute(
                "INSERT INTO flip(label, kind, from_label, at, by, mode, note) "
                "VALUES (?,?,?,?,?,?,?)", (label, kind, from_label, at, by, mode, note))
            con.execute("COMMIT")
        except BaseException:
            try:
                con.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            con.close()
        after = {m.key: m.version for m in members}
        changed = sorted(f"{s}/{u}" for (s, u), v in after.items()
                         if before.get((s, u), (None,))[0] != v)
        removed = sorted(f"{s}/{u}" for (s, u) in before if (s, u) not in after)
        return {"from_label": from_label, "label": label, "kind": kind,
                "changed": changed, "removed": removed, "at": at}

    # -- resolution (serving) -------------------------------------------------

    def resolve(self, label: str = "") -> dict[tuple[str, str], StoreVersion]:
        """``{(store, unit): StoreVersion}`` for the set to serve: the
        ``active`` rows, or the named release set's members."""
        if label:
            return {m.key: m for m in self.members(label)}
        out: dict[tuple[str, str], StoreVersion] = {}
        for row in self.active():
            sv = self.get_version(row["store"], row["unit"], int(row["version"]))
            if sv is not None:
                out[sv.key] = sv
        return out

    def resolve_paths(self, label: str = "") -> dict[str, dict[str, Path]]:
        """``{store: {unit: absolute file path}}`` for the set to serve."""
        out: dict[str, dict[str, Path]] = {s: {} for s in STORES}
        for sv in self.resolve(label).values():
            out.setdefault(sv.store, {})[sv.unit] = self.root / sv.file
        return out

    def serving_label(self, label: str = "") -> str:
        return label or self.active_label()

    # -- retention -------------------------------------------------------------

    def protected_labels(self, keep: int) -> set[str]:
        """Labels whose files GC must keep: the active set(s), the last
        ``keep`` release sets by creation, and the targets of the last
        ``keep`` flips (a rollback target stays restorable)."""
        keep = max(0, int(keep))
        labels = {r["label"] for r in self.active()}
        labels.update(rs["label"] for rs in self.release_sets()[:keep])
        labels.update(f["label"] for f in self.flips(limit=keep))
        return labels

    def gc_plan(self, keep: int) -> list[StoreVersion]:
        """Live versions that are neither active nor members of a protected
        set (``protected_labels``). Rows stay; the caller removes files and
        calls ``mark_removed``."""
        protected = self.protected_labels(keep)
        keep_versions: set[tuple[str, str, int]] = {
            (r["store"], r["unit"], int(r["version"])) for r in self.active()}
        for label in protected:
            try:
                keep_versions.update((m.store, m.unit, m.version) for m in self.members(label))
            except RegistryError:
                continue
        return [v for v in self.versions() if not v.removed_at
                and (v.store, v.unit, v.version) not in keep_versions]

    def mark_removed(self, store: str, unit: str, version: int, at: str = "") -> None:
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "UPDATE store_version SET removed_at=? WHERE store=? AND unit=? AND version=?",
                (at or now_iso(), store, unit, version))
            con.execute("COMMIT")
        finally:
            con.close()


def open_registry(path: "str | Path | None" = None,
                  read_only: bool = True) -> "Registry | None":
    """``Registry`` over ``path`` (None → ``$NORA_STORE_REGISTRY``); None
    when neither names a file (registry mode off)."""
    p = str(path if path is not None else os.getenv(REGISTRY_ENV, "")).strip()
    return Registry(p, read_only=read_only) if p else None


def registry_store_paths(store: str, registry: "str | Path | None" = None,
                         label: "str | None" = None) -> list[Path]:
    """The files of one store in the served set — ``active``, or the set
    named by ``label`` (None → ``$NORA_STORE_RELEASE_SET``). For the
    ``open_*`` factories: an explicit file list, never a directory scan,
    because a serving root holds several versions of one unit."""
    reg = open_registry(registry)
    if reg is None:
        return []
    lbl = (os.getenv(RELEASE_SET_ENV, "") if label is None else label).strip()
    paths = reg.resolve_paths(lbl).get(store, {})
    return [paths[u] for u in sorted(paths)]
