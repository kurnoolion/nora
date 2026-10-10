"""Cell-DB read side for the SIRA query service (corp-db-layer phase 2).

Loads the per-(MNO, release) SQLite cell DBs that `sandbox/cell_db_convert.py`
emits, and provides the FTS5 scoring path. The service selects this source
with `NORA_SIRA_INDEX=cell-db`; the default (`flat`) path never imports the
functions here at runtime, so existing deployments are untouched.

Design: docs/compact/strands/corp-db-layer/{storage,converter}-design.md.
stdlib + sqlite only — the bm25x engine object is loaded by the caller
from the extracted blobs.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED_SCHEMA = "1"


class CellDbError(Exception):
    """Cell DB cannot be served (bad schema, missing table, undecodable blob)."""


@dataclass
class CellDbData:
    """Everything the service needs from one cell DB file."""
    db_path: str
    meta: dict[str, str]
    cell: tuple[str, str]                     # (mno, release) from meta
    rows: list[tuple[int, str, str, str]]     # (doc, req_id, title, text) in doc order
    enrichment: dict[str, list[str]]          # effective sets (post-fold, baked)
    index_dir: str                            # extracted bm25x blobs (temp dir)
    has_fts: bool = False
    con: sqlite3.Connection | None = field(default=None, repr=False)


def open_ro(db_path: str | Path, *, for_queries: bool = False) -> sqlite3.Connection:
    """Read-only, immutable open — write-once is enforced, not assumed.
    `for_queries` allows cross-thread use (served FTS5 lookups run in the
    API threadpool; sqlite's serialized mode makes that safe for a
    read-only immutable file)."""
    return sqlite3.connect(
        f"file:{db_path}?mode=ro&immutable=1", uri=True,
        check_same_thread=not for_queries,
    )


def load_cell_db(db_path: str | Path, *, keep_connection: bool = False) -> CellDbData:
    """Read one cell DB: meta (schema-checked), corpus rows in doc order,
    baked enrichment sets, and the bm25x index blobs extracted to a fresh
    temp dir for `BM25.load`. Raises CellDbError on anything unservable."""
    db_path = str(db_path)
    con = open_ro(db_path, for_queries=keep_connection)
    try:
        meta = dict(con.execute("SELECT key, value FROM meta"))
        schema = meta.get("schema_version", "")
        if schema != SUPPORTED_SCHEMA:
            raise CellDbError(
                f"{Path(db_path).name}: schema_version {schema!r} unsupported "
                f"(loader supports {SUPPORTED_SCHEMA}) — reconvert the cell")
        mno, release = meta.get("cell_mno", ""), meta.get("cell_release", "")
        if not mno or not release:
            raise CellDbError(f"{Path(db_path).name}: meta lacks cell identity")

        rows = con.execute(
            "SELECT doc, req_id, title, text FROM corpus ORDER BY doc").fetchall()
        if not rows:
            raise CellDbError(f"{Path(db_path).name}: empty corpus")

        enrichment = {
            rid: json.loads(ph) for rid, ph in
            con.execute("SELECT req_id, phrases FROM enrichment")
        }

        index_dir = tempfile.mkdtemp(prefix=f"cell-db-{mno}-{release}-")
        for fname, codec, data in con.execute(
                "SELECT filename, codec, data FROM index_blob"):
            try:
                raw = zlib.decompress(data) if codec == "zlib" else bytes(data)
            except zlib.error as exc:
                raise CellDbError(
                    f"{Path(db_path).name}: index blob undecodable "
                    f"({type(exc).__name__})") from exc
            (Path(index_dir) / fname).write_bytes(raw)

        has_fts = bool(con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fts'"
        ).fetchone())

        data = CellDbData(
            db_path=db_path, meta=meta, cell=(mno, release), rows=rows,
            enrichment=enrichment, index_dir=index_dir, has_fts=has_fts,
            con=con if keep_connection else None,
        )
    except Exception:
        con.close()
        raise
    if not keep_connection:
        con.close()
    return data


def _fts_query(tokens: list[str]) -> str:
    """Quoted-token OR query; internal quotes doubled per FTS5 syntax."""
    quoted = ['"' + t.replace('"', '""') + '"' for t in tokens if t]
    return " OR ".join(quoted)


def _fts_scores(con: sqlite3.Connection, tokens: list[str]) -> dict[int, float]:
    """One MATCH pass -> {doc: score}, with bm25()'s negative-is-better
    rank normalized to positive-is-better before fusion."""
    q = _fts_query(tokens)
    if not q:
        return {}
    return {
        doc: -rank for doc, rank in
        con.execute("SELECT rowid, bm25(fts) FROM fts WHERE fts MATCH ?", (q,))
    }


def fts5_search(con: sqlite3.Connection, orig_tokens: list[str],
                exp_tokens: list[str], k: int, weight: float,
                ) -> list[tuple[int, float]]:
    """The fts5 engine's retrieval: two MATCH passes combined as
    `score = s_orig + weight * s_exp` (search_with_expansion semantics),
    top-k by combined score. Expansion tokens are expected already
    DF-filtered and stemmed by the caller against the cell's own index."""
    combined = _fts_scores(con, orig_tokens)
    if exp_tokens and weight > 0.0:
        for doc, s in _fts_scores(con, exp_tokens).items():
            combined[doc] = combined.get(doc, 0.0) + weight * s
    ranked = sorted(combined.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[:k]
