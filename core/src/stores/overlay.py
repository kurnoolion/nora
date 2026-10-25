"""Overlay store — review edits to SIRA enrichments (store-split-design §2,
§6; strand corp-db-layer D-DRAFT-24).

Storage-level interface at entry-dict granularity. Edit semantics (ops,
labels as branches, the label-view digest) live in `overlay_ops` (run by
`core.src.web.enrich_overlay_store.EnrichOverlayStore` on a local backend
and by the nora-store edit route) and in the stdlib fold
(`core.src.store_service.enrich_overlay`). A backend only stores and returns per-MNO
`{req_id: entry}` maps plus the label and reason-category lists.

Entry shape (identical across backends):

    {"remove": [rec, ...], "add": [rec, ...], "suppress_all": rec}
    rec = {"word", "label", "reason": {"category", "note"}, "by", "at",
           "origin": {"release"}, ["fingerprint"]}
    suppress_all rec additionally carries {"value": true} and word "".

Backends:
- `JsonOverlayStore(corrections_root)` — today's file layout under
  `<root>/sira-enrich/` (`<MNO>.json`, `accepted-labels.json`,
  `labels.json`, `reason-categories.json`); one flock'd lock file guards
  every mutation; writes are atomic (tmp + rename).
- `SqliteOverlayStore(db_path)` — one file per deployment, WAL,
  `busy_timeout`, `BEGIN IMMEDIATE` transactions. Schema stays
  Postgres-portable (no SQLite-only types). Readers on the same host open
  it with `read_only=True` (`mode=ro` URI) while the authority writes.
  A read never creates or migrates the file (writes do); a file whose
  `schema_version` this reader does not know is refused (`OVL-E005`);
  an additive record column missing from an older file reads as its
  default (design §9).
- `HttpOverlayStore(base_url)` (`clients.py`) — the same protocol over
  nora-store's `/stores/overlay/v1` routes; edits go to the server's
  `edit` route in one round trip (`run_op`).

`open_overlay_store` picks the backend: `url` (or `$NORA_OVERLAY_URL`)
→ HTTP client; else `db_path` (or `$NORA_OVERLAY_DB`) → SQLite; else
`corrections_root` → JSON; else None (overlay disabled).

Stdlib only (the HTTP client is imported lazily).
"""

from __future__ import annotations

import copy
import fcntl
import json
import os
import sqlite3
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Protocol, runtime_checkable
from urllib.parse import quote

OVERLAY_DB_ENV = "NORA_OVERLAY_DB"
OVERLAY_URL_ENV = "NORA_OVERLAY_URL"

DIRECTIONS = ("remove", "add")          # word-record lists of an entry
SUPPRESS = "suppress_all"               # entry-level record

# JSON layout: files under <root>/sira-enrich/ that are NOT MNO overlays
_ACCEPTED_FILE = "accepted-labels.json"
_DISABLED_FILE = "labels.json"
_REASONS_FILE = "reason-categories.json"
_NON_MNO_FILES = (_DISABLED_FILE, _ACCEPTED_FILE, _REASONS_FILE)


@runtime_checkable
class OverlayStore(Protocol):
    """Storage interface for the Overlay (one writer: the nora-store
    that hosts it — the Overlay authority; nora-web edits through it)."""

    read_only: bool

    # reads
    def get_overlay(self, mno: str) -> dict[str, Any]: ...
    def get_entry(self, mno: str, req_id: str) -> "dict[str, Any] | None": ...
    def list_mnos(self) -> list[str]: ...
    def version(self, mno: str) -> float:
        """Monotonic change marker; 0.0 when the MNO has no overlay."""
        ...
    def accepted_labels(self) -> set[str]: ...
    def disabled_labels(self) -> set[str]: ...
    def reason_categories(self) -> "list[str] | None":
        """The stored list; None when never set (caller applies defaults)."""
        ...

    # writes
    def transaction(self) -> Any:
        """Context manager: exclusive across processes on one host. Reads
        inside it see the transaction's own writes. Nested use joins the
        open transaction."""
        ...
    def put_entry(self, mno: str, req_id: str,
                  entry: "dict[str, Any] | None") -> None:
        """Replace one req's entry; None or {} deletes it. Always marks
        the MNO as changed (registers it for `list_mnos`)."""
        ...
    def set_accepted_labels(self, labels: set[str]) -> None: ...
    def set_disabled_labels(self, labels: set[str]) -> None: ...
    def set_reason_categories(self, categories: list[str]) -> None: ...


class OverlayReadOnlyError(RuntimeError):
    """A write was attempted on a store opened read-only."""


# ── JSON backend ────────────────────────────────────────────────────


class JsonOverlayStore:
    """Today's per-MNO JSON files. Writes inside a transaction are
    buffered and flushed (atomically, per file) when it exits cleanly."""

    def __init__(self, corrections_root: "str | Path", read_only: bool = False):
        self.root = Path(corrections_root)
        self.dir = self.root / "sira-enrich"
        self.read_only = read_only
        self._tls = threading.local()   # per-thread transaction buffer

    # ── locking + IO ────────────────────────────────────────────────

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.dir / ".lock"
        with open(lock_path, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    @staticmethod
    def _read_json(path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1, sort_keys=True)
                f.write("\n")
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _mno_path(self, mno: str) -> Path:
        return self.dir / f"{mno}.json"

    def _pending(self) -> "dict[Path, Any] | None":
        return getattr(self._tls, "pending", None)

    def _read(self, path: Path, default: Any) -> Any:
        """Read through the open transaction's buffer (a deep copy, so
        callers may mutate what they get)."""
        pending = self._pending()
        if pending is not None and path in pending:
            return copy.deepcopy(pending[path])
        return self._read_json(path, default)

    def _stage(self, path: Path, data: Any) -> None:
        if self.read_only:
            raise OverlayReadOnlyError("overlay store opened read-only")
        pending = self._pending()
        if pending is None:
            with self.transaction():
                self._stage(path, data)
            return
        pending[path] = data

    # ── reads ───────────────────────────────────────────────────────

    def get_overlay(self, mno: str) -> dict[str, Any]:
        return self._read(self._mno_path(mno), {})

    def get_entry(self, mno: str, req_id: str) -> "dict[str, Any] | None":
        return self.get_overlay(mno).get(req_id)

    def list_mnos(self) -> list[str]:
        """MNOs with an overlay file (sorted)."""
        names = set()
        if self.dir.is_dir():
            names = {p.stem for p in self.dir.glob("*.json")
                     if p.name not in _NON_MNO_FILES}
        pending = self._pending() or {}
        names |= {p.stem for p in pending
                  if p.suffix == ".json" and p.name not in _NON_MNO_FILES}
        return sorted(names)

    def version(self, mno: str) -> float:
        """File mtime; 0.0 when absent."""
        try:
            return self._mno_path(mno).stat().st_mtime
        except OSError:
            return 0.0

    def accepted_labels(self) -> set[str]:
        data = self._read(self.dir / _ACCEPTED_FILE, {})
        return set(data.get("accepted", []))

    def disabled_labels(self) -> set[str]:
        data = self._read(self.dir / _DISABLED_FILE, {})
        return set(data.get("disabled", []))

    def reason_categories(self) -> "list[str] | None":
        cats = self._read(self.dir / _REASONS_FILE, None)
        return cats if isinstance(cats, list) and cats else None

    # ── writes ──────────────────────────────────────────────────────

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._pending() is not None:     # nested: join
            yield
            return
        if self.read_only:
            raise OverlayReadOnlyError("overlay store opened read-only")
        with self._locked():
            self._tls.pending = {}
            try:
                yield
                for path, data in self._tls.pending.items():
                    self._write_json(path, data)
            finally:
                self._tls.pending = None

    def put_entry(self, mno: str, req_id: str,
                  entry: "dict[str, Any] | None") -> None:
        with self.transaction():
            path = self._mno_path(mno)
            overlay = self._read(path, {})
            if entry:
                overlay[req_id] = entry
            else:
                overlay.pop(req_id, None)
            self._stage(path, overlay)

    def set_accepted_labels(self, labels: set[str]) -> None:
        self._stage(self.dir / _ACCEPTED_FILE, {"accepted": sorted(labels)})

    def set_disabled_labels(self, labels: set[str]) -> None:
        self._stage(self.dir / _DISABLED_FILE, {"disabled": sorted(labels)})

    def set_reason_categories(self, categories: list[str]) -> None:
        self._stage(self.dir / _REASONS_FILE, list(categories))


# ── SQLite backend ──────────────────────────────────────────────────

SQLITE_SCHEMA_VERSION = "1"

# Postgres-portable: TEXT / INTEGER / REAL only, ON CONFLICT upserts.
# `seq` keeps record order within (mno, req_id, direction) stable
# (insertion order, as in the JSON lists). `raw_json` holds a record
# verbatim only when the columns cannot reproduce it exactly (legacy or
# hand-edited shapes); '' otherwise.
_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS meta(
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS record(
  mno            TEXT NOT NULL,
  req_id         TEXT NOT NULL,
  direction      TEXT NOT NULL CHECK (direction IN ('remove','add','suppress_all')),
  word           TEXT NOT NULL,          -- '' for suppress_all
  seq            INTEGER NOT NULL,
  label          TEXT NOT NULL DEFAULT '',
  reason_json    TEXT NOT NULL DEFAULT '{}',
  by_user        TEXT NOT NULL DEFAULT '',
  at             TEXT NOT NULL DEFAULT '',
  origin_release TEXT NOT NULL DEFAULT '',
  fingerprint    TEXT NOT NULL DEFAULT '',
  raw_json       TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (mno, req_id, direction, word)
);
CREATE INDEX IF NOT EXISTS record_label_idx ON record(label);
CREATE TABLE IF NOT EXISTS label(
  name     TEXT PRIMARY KEY,
  accepted INTEGER NOT NULL DEFAULT 0,
  disabled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS reason_category(
  pos  INTEGER PRIMARY KEY,
  name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mno_version(
  mno     TEXT PRIMARY KEY,
  version REAL NOT NULL
);
"""

_RECORD_COLS = ("word", "label", "reason_json", "by_user", "at",
                "origin_release", "fingerprint", "raw_json")
# Defaults substituted for a record column an older file lacks (design
# §9: additive columns do not bump the schema version; readers tolerate
# absence). `word` is part of the key and never optional.
_RECORD_COL_DEFAULTS = {"label": "''", "reason_json": "'{}'", "by_user": "''",
                        "at": "''", "origin_release": "''", "fingerprint": "''",
                        "raw_json": "''"}


def _str(v: Any) -> str:
    return v if isinstance(v, str) else ""


def _row_to_record(direction: str, row: "tuple | sqlite3.Row") -> Any:
    """Columns → the record dict the JSON backend returns (sorted keys)."""
    word, label, reason_json, by, at, origin, fp, raw = row
    if raw:
        return json.loads(raw)
    rec: dict[str, Any] = {
        "word": word, "label": label, "reason": json.loads(reason_json),
        "by": by, "at": at, "origin": {"release": origin}}
    if fp:
        rec["fingerprint"] = fp
    if direction == SUPPRESS:
        rec["value"] = True
    return dict(sorted(rec.items()))


def _record_to_row(direction: str, rec: Any) -> tuple:
    """Record → column values; falls back to `raw_json` whenever the
    columns would not reproduce the record exactly."""
    d = rec if isinstance(rec, dict) else {}
    reason = d.get("reason")
    origin = d.get("origin")
    row = (
        "" if direction == SUPPRESS else _str(d.get("word")),
        _str(d.get("label")),
        json.dumps(reason if isinstance(reason, dict) else {}, sort_keys=True),
        _str(d.get("by")),
        _str(d.get("at")),
        _str(origin.get("release")) if isinstance(origin, dict) else "",
        _str(d.get("fingerprint")),
        "",
    )
    if _row_to_record(direction, row) != rec:
        row = row[:-1] + (json.dumps(rec, sort_keys=True),)
    return row


class SqliteOverlayStore:
    """One SQLite file per deployment. Single writer (the Overlay
    authority); readers open `read_only=True`. Connections are per call /
    per transaction, so one instance is safe to share between threads."""

    def __init__(self, db_path: "str | Path", read_only: bool = False,
                 busy_timeout_ms: int = 5000):
        self.db_path = Path(db_path)
        self.read_only = read_only
        self.busy_timeout_ms = int(busy_timeout_ms)
        self._tls = threading.local()
        self._schema_ready = False
        self._record_select = ""        # column list tolerant of older files

    # ── connections ─────────────────────────────────────────────────

    def _connect(self, create: bool = False) -> sqlite3.Connection:
        """A connection to the file. `create` (writers only) makes the
        file and its schema; a read never does."""
        timeout = self.busy_timeout_ms / 1000.0
        if create and not self.read_only:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(str(self.db_path), timeout=timeout,
                                  isolation_level=None)
        else:
            # `rw` for the writer's own reads (it may need to create the
            # WAL -shm file); `ro` for everyone else
            mode = "ro" if self.read_only else "rw"
            uri = f"file:{quote(str(self.db_path.resolve()))}?mode={mode}"
            con = sqlite3.connect(uri, uri=True, timeout=timeout,
                                  isolation_level=None)
        con.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        if create and not self._schema_ready:
            self._ensure_schema(con)
        if not self._record_select:
            self._check_file(con)
        return con

    def _check_file(self, con: sqlite3.Connection) -> None:
        """Once per instance: refuse an unknown `schema_version`, and build
        the record column list from the columns the file actually has."""
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                       "AND name='meta'").fetchone() is None:
            return                      # not initialised: reads see empty
        row = con.execute("SELECT value FROM meta WHERE key = 'schema_version'"
                          ).fetchone()
        found = row[0] if row else ""
        if found and found != SQLITE_SCHEMA_VERSION:
            from core.src.stores.errors import SchemaVersionError
            raise SchemaVersionError(
                "OVL-E005", "Overlay", self.db_path.name, found,
                SQLITE_SCHEMA_VERSION,
                "re-import the overlay (core.src.stores.overlay_import) "
                "or run a newer build")
        present = {r[1] for r in con.execute("PRAGMA table_info(record)")}
        self._record_select = ", ".join(
            c if c in present else f"{_RECORD_COL_DEFAULTS[c]} AS {c}"
            for c in _RECORD_COLS)

    def _ensure_schema(self, con: sqlite3.Connection) -> None:
        """Create the schema once per file. Checks first, so an instance
        opened while another writer holds the lock can still read."""
        ready = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                            "AND name='meta'").fetchone() is not None
        if not ready:
            if con.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
                con.execute("PRAGMA journal_mode = WAL")
            con.execute("BEGIN IMMEDIATE")
            try:
                for stmt in filter(str.strip, _SQLITE_DDL.split(";")):
                    con.execute(stmt)
                con.execute(
                    "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
                    "ON CONFLICT(key) DO NOTHING", (SQLITE_SCHEMA_VERSION,))
            except BaseException:
                con.execute("ROLLBACK")
                raise
            con.execute("COMMIT")
        self._schema_ready = True

    def _tx_con(self) -> "sqlite3.Connection | None":
        return getattr(self._tls, "con", None)

    @contextmanager
    def _reader(self) -> Iterator["sqlite3.Connection | None"]:
        """The open transaction's connection, else a fresh one. Yields
        None when the file is absent or not initialised (reads then
        return empty; a read never creates the file)."""
        con = self._tx_con()
        if con is not None:
            yield con
            return
        if not self.db_path.is_file():
            yield None
            return
        con = self._connect()
        try:
            if con.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='mno_version'").fetchone() is None:
                yield None
            else:
                yield con
        finally:
            con.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._tx_con() is not None:      # nested: join
            yield
            return
        if self.read_only:
            raise OverlayReadOnlyError("overlay store opened read-only")
        con = self._connect(create=True)
        try:
            con.execute("BEGIN IMMEDIATE")
            self._tls.con = con
            try:
                yield
            except BaseException:
                con.execute("ROLLBACK")
                raise
            con.execute("COMMIT")
        finally:
            self._tls.con = None
            con.close()

    # ── reads ───────────────────────────────────────────────────────

    def get_overlay(self, mno: str) -> dict[str, Any]:
        with self._reader() as con:
            if con is None:
                return {}
            rows = con.execute(
                f"SELECT req_id, direction, {self._record_select} "
                "FROM record WHERE mno = ? ORDER BY req_id, direction, seq",
                (mno,)).fetchall()
        overlay: dict[str, Any] = {}
        for req_id, direction, *cols in rows:
            entry = overlay.setdefault(req_id, {})
            rec = _row_to_record(direction, tuple(cols))
            if direction == SUPPRESS:
                entry[SUPPRESS] = rec
            else:
                entry.setdefault(direction, []).append(rec)
        # key order as the JSON backend's sorted files return it
        return {rid: dict(sorted(e.items())) for rid, e in overlay.items()}

    def get_entry(self, mno: str, req_id: str) -> "dict[str, Any] | None":
        with self._reader() as con:
            if con is None:
                return None
            rows = con.execute(
                f"SELECT direction, {self._record_select} FROM record "
                "WHERE mno = ? AND req_id = ? ORDER BY direction, seq",
                (mno, req_id)).fetchall()
        if not rows:
            return None
        entry: dict[str, Any] = {}
        for direction, *cols in rows:
            rec = _row_to_record(direction, tuple(cols))
            if direction == SUPPRESS:
                entry[SUPPRESS] = rec
            else:
                entry.setdefault(direction, []).append(rec)
        return dict(sorted(entry.items()))

    def list_mnos(self) -> list[str]:
        with self._reader() as con:
            if con is None:
                return []
            return [r[0] for r in con.execute(
                "SELECT mno FROM mno_version ORDER BY mno")]

    def version(self, mno: str) -> float:
        with self._reader() as con:
            if con is None:
                return 0.0
            row = con.execute("SELECT version FROM mno_version WHERE mno = ?",
                              (mno,)).fetchone()
        return float(row[0]) if row else 0.0

    def _label_set(self, column: str) -> set[str]:
        with self._reader() as con:
            if con is None:
                return set()
            return {r[0] for r in con.execute(
                f"SELECT name FROM label WHERE {column} = 1")}

    def accepted_labels(self) -> set[str]:
        return self._label_set("accepted")

    def disabled_labels(self) -> set[str]:
        return self._label_set("disabled")

    def reason_categories(self) -> "list[str] | None":
        with self._reader() as con:
            if con is None:
                return None
            cats = [r[0] for r in con.execute(
                "SELECT name FROM reason_category ORDER BY pos")]
        return cats or None

    # ── writes ──────────────────────────────────────────────────────

    def _bump(self, con: sqlite3.Connection, mno: str) -> None:
        row = con.execute("SELECT version FROM mno_version WHERE mno = ?",
                          (mno,)).fetchone()
        # wall-clock epoch, forced strictly increasing (pending detection
        # compares it against a cell's loaded_at)
        new = max(time.time(), (float(row[0]) + 1e-6) if row else 0.0)
        con.execute(
            "INSERT INTO mno_version(mno, version) VALUES (?, ?) "
            "ON CONFLICT(mno) DO UPDATE SET version = excluded.version",
            (mno, new))

    def put_entry(self, mno: str, req_id: str,
                  entry: "dict[str, Any] | None") -> None:
        with self.transaction():
            con = self._tx_con()
            con.execute("DELETE FROM record WHERE mno = ? AND req_id = ?",
                        (mno, req_id))
            rows = []
            for direction in DIRECTIONS:
                for seq, rec in enumerate((entry or {}).get(direction) or []):
                    rows.append((mno, req_id, direction, seq,
                                 *_record_to_row(direction, rec)))
            if (entry or {}).get(SUPPRESS) is not None:
                rows.append((mno, req_id, SUPPRESS, 0,
                             *_record_to_row(SUPPRESS, entry[SUPPRESS])))
            con.executemany(
                "INSERT INTO record(mno, req_id, direction, seq, "
                f"{', '.join(_RECORD_COLS)}) VALUES "
                f"(?, ?, ?, ?, {', '.join('?' * len(_RECORD_COLS))})", rows)
            self._bump(con, mno)

    def _set_label_flag(self, column: str, labels: set[str]) -> None:
        with self.transaction():
            con = self._tx_con()
            con.execute(f"UPDATE label SET {column} = 0")
            con.executemany(
                f"INSERT INTO label(name, {column}) VALUES (?, 1) "
                f"ON CONFLICT(name) DO UPDATE SET {column} = 1",
                [(name,) for name in sorted(labels)])
            con.execute("DELETE FROM label WHERE accepted = 0 AND disabled = 0")

    def set_accepted_labels(self, labels: set[str]) -> None:
        self._set_label_flag("accepted", labels)

    def set_disabled_labels(self, labels: set[str]) -> None:
        self._set_label_flag("disabled", labels)

    def set_reason_categories(self, categories: list[str]) -> None:
        with self.transaction():
            con = self._tx_con()
            con.execute("DELETE FROM reason_category")
            con.executemany(
                "INSERT INTO reason_category(pos, name) VALUES (?, ?)",
                list(enumerate(categories)))

    # ── maintenance (import CLI) ────────────────────────────────────

    def is_empty(self) -> bool:
        with self._reader() as con:
            if con is None:
                return True
            return not any(
                con.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone()
                for t in ("record", "label", "reason_category", "mno_version"))

    def clear(self) -> None:
        with self.transaction():
            con = self._tx_con()
            for t in ("record", "label", "reason_category", "mno_version"):
                con.execute(f"DELETE FROM {t}")


# ── factory ─────────────────────────────────────────────────────────


def open_overlay_store(corrections_root: "str | Path" = "",
                       db_path: "str | Path" = "",
                       read_only: bool = False,
                       url: "str | None" = None) -> "OverlayStore | None":
    """`url` (None → `$NORA_OVERLAY_URL`) → HTTP client on nora-store;
    else `db_path` (else `$NORA_OVERLAY_DB`) → SQLite; else
    `corrections_root` → JSON files; else None (overlay disabled). Pass
    `url=""` to ignore the env var."""
    base = (os.getenv(OVERLAY_URL_ENV, "") if url is None else url).strip()
    if base:
        from core.src.stores.clients import HttpOverlayStore
        return HttpOverlayStore(base, read_only=read_only)
    db = str(db_path or os.getenv(OVERLAY_DB_ENV, "")).strip()
    if db:
        return SqliteOverlayStore(db, read_only=read_only)
    if corrections_root:
        return JsonOverlayStore(corrections_root, read_only=read_only)
    return None
