"""Cell-DB converter — the Cells store builder (corp-db-layer).

Converts one promoted label's SIRA flat files into one SQLite **cell DB**
per (MNO, release) cell. Schema 2 (store-split-design §2, §4) keeps the
layers separate: corpus rows with their content fingerprint, the VANILLA
serialized bm25x index, and SIRA's raw per-requirement phrases. Review
edits are not stored here — the service folds the Overlay store in at
load. Design: docs/compact/strands/corp-db-layer/store-split-design.md.

Identity gate (--verify N): the converter folds the overlay into SIRA's
phrases exactly as the service does, enriches the in-memory index, then
loads the VANILLA index back from the DB blobs, applies the same effective
sets, and requires identical rankings on N probe queries. That proves a
schema-2 cell composed at load serves what a baked schema-1 cell served.

Usage (inside the sandbox venv, from the repo root):

    python -m sandbox.cell_db_convert --label <serve-root>/<label> --out <dir>
    python -m sandbox.cell_db_convert --db-root <cells-dir> --out <dir> \
        [--cells <mno>__<release>,...] [--enrich-run NAME | --latest-runs] \
        [--overlay-root <corrections-root> | --overlay-db <overlay.db>] \
        [--verify N] [--force]

Error codes (stable, content-free — NFR-9):
    CNV-001  corpus.jsonl missing for a cell
    CNV-002  index/best missing for a cell
    CNV-003  enrichment resolve failure (pinned run named but absent)
    CNV-004  output DB exists and --force not given
    CNV-005  enrich_batch failed while building the enriched index
    CNV-006  self-verify ranking mismatch (DB-loaded index != in-memory)
    CNV-007  blob round-trip failure (saved index does not reload)
    CNV-008  overlay fold failure
    CNV-009  no cells found under the input root
    CNV-010  FTS5 requested — schema 2 stores the vanilla index only
    CNV-011  best.jsonl is a dangling link (pin the run with --enrich-run)

The report (stdout + <out>/conversion-report.json) carries counts and
digests only — never requirement text, titles, or enrichment words (D-012).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from sandbox.sira_cells import CellKey, cell_dirname, enumerate_cells
from core.src.stores.overlay import open_overlay_store
from nora_store.enrich_overlay import (
    allowed_labels,
    apply_overlay_to_req,
    filter_overlay,
    make_verdict_fn,
)

SCHEMA_VERSION = 2   # 2: vanilla index + raw SIRA phrases + fingerprints
#   (store-split-design D-DRAFT-23); 1: baked enriched index (loader still reads it)
CONVERTER_VERSION = "0.3"  # 0.3: schema 2. 0.2: corpus.req_id UNIQUE dropped — real cells
#   carry duplicate doc/section `_id` rows (field finding, 2026-10-10);
#   the row set must stay byte- and position-faithful to corpus.jsonl
#   because the index blobs score every row. Id lookups are last-wins
#   (max doc per req_id), mirroring the serving loader's dict semantics.

_DDL = """
CREATE TABLE meta(
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE corpus(
  doc     INTEGER PRIMARY KEY,          -- stable load-order index
  req_id  TEXT NOT NULL,                -- corpus.jsonl `_id`; NOT unique:
                                        -- real cells carry duplicate doc/
                                        -- section ids, and the index blobs
                                        -- score every row positionally.
                                        -- Service semantics for id lookup
                                        -- is last-wins (max doc per id).
  title   TEXT NOT NULL DEFAULT '',
  text    TEXT NOT NULL DEFAULT '',
  fingerprint TEXT NOT NULL DEFAULT ''  -- text_fp; '' for composite
                                        -- doc:/section: rows and pre-sidecar builds
);
CREATE INDEX corpus_req_id_idx ON corpus(req_id);
CREATE TABLE sira_phrases(
  req_id  TEXT PRIMARY KEY,             -- SIRA's RAW phrases (pre-overlay)
  phrases TEXT NOT NULL,                -- JSON array of phrases
  run_id  TEXT NOT NULL DEFAULT ''
);
CREATE TABLE index_blob(
  filename TEXT PRIMARY KEY,            -- VANILLA bm25x serialized file name
  codec    TEXT NOT NULL DEFAULT 'zlib',-- 'zlib' | 'raw'
  data     BLOB NOT NULL                -- codec-encoded file bytes
);
"""

FINGERPRINTS_NAME = "fingerprints.jsonl"   # adapter sidecar (raw/)


class ConvertError(Exception):
    """Conversion failure with a stable CNV- code. Message stays content-free."""

    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(f"{code}: {detail}")


@dataclass
class CellData:
    """Pass-1 state for one cell (mirrors the service's CellState subset)."""
    cell: CellKey
    base: Path
    doc_ids: list[str]
    doc_id_to_idx: dict[str, int]
    rows: list[tuple[int, str, str, str]]   # (doc, req_id, title, text)
    bm25: object
    llm_words: dict[str, list[str]]
    enrich_source: Path | None
    enrich_run_id: str
    enrich_model: str
    vanilla_blobs: list[tuple[str, bytes]] = field(default_factory=list)
    fingerprints: dict[str, str] = field(default_factory=dict)
    # filled in pass 2
    effective: dict[str, list[str]] = field(default_factory=dict)
    overlay_digest: str = ""
    overlay_counts: dict[str, int] = field(default_factory=dict)
    _token_cache: dict[str, frozenset] = field(default_factory=dict)

    def vanilla_tokens(self, req_id: str) -> frozenset | None:
        """Vanilla token set for the cross-release verdict — same basis as
        the service's CellState.vanilla_tokens (the index's own tokenizer
        over the pre-enrichment corpus text)."""
        cached = self._token_cache.get(req_id)
        if cached is not None:
            return cached
        idx = self.doc_id_to_idx.get(req_id)
        if idx is None:
            return None
        toks = frozenset(self.bm25.tokenize(self.rows[idx][3]))
        self._token_cache[req_id] = toks
        return toks


def _sha256_files(paths: list[Path | None]) -> str:
    """Per-cell data fingerprint — MUST match service._sha256_files
    (nora_store/service.py): streamed sha256 over corpus.jsonl + the
    applied phrases file, absent files hashing as a marker."""
    h = hashlib.sha256()
    for p in paths:
        if p and p.is_file():
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
        else:
            h.update(b"<absent>")
        h.update(b"\x00")
    return h.hexdigest()


def _overlay_digest(filtered_overlay: dict) -> str:
    """Formula must match service._overlay_digest and
    EnrichOverlayStore.overlay_digest."""
    payload = json.dumps({"overlay": filtered_overlay},
                         sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _resolve_run_dir(stage_dir: Path, pinned: str, use_latest: bool) -> Path | None:
    """Service precedence (service._resolve_run_dir): pinned name wins,
    else most-recent run dir when --latest-runs, else None (best-pointer)."""
    if pinned:
        cand = stage_dir / pinned
        if not cand.is_dir():
            raise ConvertError("CNV-003", f"pinned enrich run not found under {stage_dir.name}/")
        return cand
    if use_latest and stage_dir.is_dir():
        subs = [p for p in stage_dir.iterdir() if p.is_dir()]
        if subs:
            return max(subs, key=lambda p: p.stat().st_mtime)
    return None


def _enrich_model_of_run(run_dir: Path) -> str:
    """Basename of the run config's sglang.model (service._enrich_model_of_run)."""
    try:
        cfg = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
        model = str((cfg.get("sglang") or {}).get("model") or "")
        return model.rstrip("/").rsplit("/", 1)[-1]
    except (OSError, ValueError, AttributeError):
        return ""


def _load_cell(base: Path, cell: CellKey, enrich_run: str, use_latest: bool) -> CellData:
    """Pass 1: corpus rows + raw LLM enrichment words + vanilla index.
    Mirrors service._load_one_cell (enrichment precedence included)."""
    corpus_path = base / "raw" / "corpus.jsonl"
    index_dir = base / "index" / "best"
    if not corpus_path.exists():
        raise ConvertError("CNV-001", f"corpus.jsonl missing in {cell_dirname(cell)}")
    if not index_dir.exists():
        raise ConvertError("CNV-002", f"index/best missing in {cell_dirname(cell)}")

    doc_ids: list[str] = []
    doc_id_to_idx: dict[str, int] = {}
    rows: list[tuple[int, str, str, str]] = []
    with open(corpus_path, encoding="utf-8") as f:
        for line in f:
            obj = json.loads(line)
            rid = obj["_id"]
            doc_id_to_idx[rid] = len(doc_ids)
            doc_ids.append(rid)
            rows.append((len(rows), rid, obj.get("title", ""), obj.get("text", "")))

    from bm25x import BM25
    bm25 = BM25.load(str(index_dir))
    # Defensive, same as the service and SIRA's own scripts: mutations on
    # a path-bound index auto-save; never touch the label's index/best.
    bm25.disable_auto_save()
    # Capture the VANILLA serialization now, before pass 2 enriches the
    # in-memory index — schema 2 stores the vanilla layer only.
    with tempfile.TemporaryDirectory(prefix="cnv-vanilla-") as td:
        bm25.save(td)
        vanilla_blobs = [(p.name, p.read_bytes())
                         for p in sorted(Path(td).iterdir()) if p.is_file()]

    fingerprints: dict[str, str] = {}
    fp_path = base / "raw" / FINGERPRINTS_NAME
    if fp_path.is_file():
        with open(fp_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    row = json.loads(line)
                    fingerprints[row["_id"]] = row.get("fingerprint", "")

    run_dir = _resolve_run_dir(base / "runs" / "doc-enrich", enrich_run, use_latest)
    enrich_model = _enrich_model_of_run(run_dir) if run_dir is not None else ""
    phrases_path: Path | None = None
    run_id = ""
    if run_dir is not None and (run_dir / "enrichments.kept.jsonl").exists():
        phrases_path = run_dir / "enrichments.kept.jsonl"
        run_id = run_dir.name
    if phrases_path is None:
        fallback = base / "enrichments" / "doc" / "best.jsonl"
        if fallback.is_symlink() and not fallback.exists():
            # Field finding: a best pointer to a pruned run silently produced
            # a cell with no phrases. Refuse; pin the run with --enrich-run.
            raise ConvertError("CNV-011", f"enrichments/doc/best.jsonl is a dangling link in {cell_dirname(cell)} — pin the run with --enrich-run")
        if fallback.exists() and fallback.stat().st_size > 0:
            phrases_path = fallback
            run_id = "best"

    llm_words: dict[str, list[str]] = {}
    if phrases_path is not None:
        with open(phrases_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                did = row.get("doc_id") or row.get("_id")
                phrases = row.get("phrases") or []
                if did and phrases and did in doc_id_to_idx:
                    llm_words.setdefault(did, []).extend(phrases)

    return CellData(
        cell=cell, base=base, doc_ids=doc_ids, doc_id_to_idx=doc_id_to_idx,
        rows=rows, bm25=bm25, llm_words=llm_words,
        enrich_source=phrases_path, enrich_run_id=run_id, enrich_model=enrich_model,
        vanilla_blobs=vanilla_blobs, fingerprints=fingerprints,
    )


def _fold_and_enrich(cd: CellData, cells: dict[CellKey, CellData],
                     overlay_root: str, overlay_db: str = "") -> None:
    """Pass 2: fold the corrections overlay (main view) into the effective
    sets and apply them to the in-memory index. Mirrors
    service._apply_overlay_and_enrich with label='' — peers for the
    cross-release verdict are the cells being converted. The overlay is
    read through the Overlay store (`overlay_db`, else $NORA_OVERLAY_DB,
    else the JSON files under `overlay_root`); neither given = no overlay."""
    mno, release = cd.cell
    try:
        store = (open_overlay_store(overlay_root, db_path=overlay_db,
                                    read_only=True)
                 if (overlay_root or overlay_db) else None)
        if store is not None:
            accepted = store.accepted_labels()
            overlay = filter_overlay(store.get_overlay(mno),
                                     allowed_labels(accepted, ""))
        else:
            overlay = {}
    except Exception as exc:
        raise ConvertError("CNV-008", f"overlay load failed for {cell_dirname(cd.cell)}: {type(exc).__name__}") from exc
    cd.overlay_digest = _overlay_digest(overlay)

    def token_sets(rel: str, req_id: str):
        peer = cells.get((mno, rel)) if rel != release else cd
        return peer.vanilla_tokens(req_id) if peer is not None else None

    def fingerprint_of(rel: str, req_id: str):
        peer = cells.get((mno, rel)) if rel != release else cd
        return peer.fingerprints.get(req_id) if peer is not None else None

    verdict = make_verdict_fn(token_sets, release, fingerprint_of=fingerprint_of)

    effective: dict[str, list[str]] = {}
    n_rem = n_add = n_held = n_sup = 0
    req_ids = set(cd.llm_words) | {r for r in overlay if r in cd.doc_id_to_idx}
    for rid in req_ids:
        res = apply_overlay_to_req(
            cd.llm_words.get(rid, []), overlay.get(rid), None, verdict, rid)
        if res.effective:
            effective[rid] = res.effective
        n_rem += len(res.applied_removes)
        n_add += len(res.applied_adds)
        n_held += len(res.held)
        n_sup += 1 if res.suppressed else 0

    items = [(cd.doc_id_to_idx[rid], ph) for rid, ph in effective.items()]
    try:
        if items:
            cd.bm25.enrich_batch(items)
    except Exception as exc:
        raise ConvertError("CNV-005", f"enrich_batch failed for {cell_dirname(cd.cell)}: {type(exc).__name__}") from exc
    cd.effective = effective
    cd.overlay_counts = {"removes": n_rem, "adds": n_add,
                         "held": n_held, "suppressed": n_sup}


def _probe_queries(cd: CellData, n: int) -> list[tuple[str, str]]:
    """(query, expansion) probe pairs: titles sampled evenly across the
    corpus; expansion = that doc's effective phrases (space-joined) when
    present, exercising the weighted-expansion path. Content stays
    in-process — probes never appear in the report (D-012)."""
    if not cd.rows or n <= 0:
        return []
    step = max(1, len(cd.rows) // n)
    probes: list[tuple[str, str]] = []
    for i in range(0, len(cd.rows), step):
        doc, rid, title, text = cd.rows[i]
        query = title.strip() or text[:80]
        if not query:
            continue
        expansion = " ".join(cd.effective.get(rid, []))
        probes.append((query, expansion))
        if len(probes) >= n:
            break
    return probes


def _rankings(bm25, probes: list[tuple[str, str]], k: int, weight: float):
    queries = [q for q, _ in probes]
    expansions = [e for _, e in probes]
    return bm25.search_with_expansion(queries, expansions, k, weight)


def _verify(cd: CellData, db_path: Path, probes: list[tuple[str, str]],
            k: int = 10, weight: float = 0.5) -> dict:
    """Identity gate: load the VANILLA index back from the DB blobs, apply
    the same effective sets the service would compose at load, and require
    rankings IDENTICAL to the in-memory enriched index."""
    from bm25x import BM25
    with tempfile.TemporaryDirectory(prefix="cnv-verify-") as td:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            for fname, codec, data in con.execute(
                    "SELECT filename, codec, data FROM index_blob"):
                raw = zlib.decompress(data) if codec == "zlib" else bytes(data)
                (Path(td) / fname).write_bytes(raw)
        except zlib.error as exc:
            raise ConvertError("CNV-007", f"DB index blobs do not decode for {cell_dirname(cd.cell)}: {type(exc).__name__}") from exc
        finally:
            con.close()
        try:
            db_bm25 = BM25.load(td)
            db_bm25.disable_auto_save()
        except Exception as exc:
            raise ConvertError("CNV-007", f"DB index blobs do not reload for {cell_dirname(cd.cell)}: {type(exc).__name__}") from exc
        items = [(cd.doc_id_to_idx[rid], ph) for rid, ph in cd.effective.items()]
        try:
            if items:
                db_bm25.enrich_batch(items)
        except Exception as exc:
            raise ConvertError("CNV-005", f"composing the DB index failed for {cell_dirname(cd.cell)}: {type(exc).__name__}") from exc

        ref = _rankings(cd.bm25, probes, k, weight)
        got = _rankings(db_bm25, probes, k, weight)

    mismatches = 0
    for r, g in zip(ref, got):
        if [d for d, _ in r] != [d for d, _ in g]:
            mismatches += 1
            continue
        if any(abs(rs - gs) > 1e-6 for (_, rs), (_, gs) in zip(r, g)):
            mismatches += 1
    if mismatches:
        raise ConvertError(
            "CNV-006",
            f"self-verify mismatch in {cell_dirname(cd.cell)}: "
            f"{mismatches}/{len(probes)} probe rankings differ",
        )
    return {"probes": len(probes), "k": k, "mismatches": 0}


def _write_db(cd: CellData, out_dir: Path, source_label: str,
              force: bool) -> tuple[Path, dict]:
    mno, release = cd.cell
    final = out_dir / f"cell-{mno}-{release}.db"
    if final.exists() and not force:
        raise ConvertError("CNV-004", f"{final.name} exists (use --force to overwrite)")
    tmp = out_dir / f".tmp-{final.name}"
    tmp.unlink(missing_ok=True)

    # The VANILLA index blobs (captured in pass 1). zlib matters: bm25x's
    # hashed n-gram side serializes its full slot table (~134 MB of mostly
    # zeros at default n_features) regardless of corpus size; compression
    # takes it down ~500x. Decompression restores byte-identical files.
    blobs = cd.vanilla_blobs
    stored = [(name, "zlib", zlib.compress(data, 6)) for name, data in blobs]

    con = sqlite3.connect(tmp)
    try:
        con.executescript(_DDL)
        con.executemany(
            "INSERT INTO corpus(doc, req_id, title, text, fingerprint) VALUES (?,?,?,?,?)",
            [(doc, rid, title, text, cd.fingerprints.get(rid, ""))
             for doc, rid, title, text in cd.rows])
        con.executemany(
            "INSERT INTO sira_phrases(req_id, phrases, run_id) VALUES (?,?,?)",
            [(rid, json.dumps(ph, ensure_ascii=False), cd.enrich_run_id)
             for rid, ph in sorted(cd.llm_words.items())])
        con.executemany(
            "INSERT INTO index_blob(filename, codec, data) VALUES (?,?,?)", stored)

        fingerprint = _sha256_files(
            [cd.base / "raw" / "corpus.jsonl", cd.enrich_source])
        meta = {
            "schema_version": str(SCHEMA_VERSION),
            "converter_version": CONVERTER_VERSION,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "cell_mno": mno,
            "cell_release": release,
            "source_label": source_label,
            "source_fingerprint": fingerprint,
            "enrich_run": cd.enrich_run_id,
            "enrich_model": cd.enrich_model,
            "sira_docs": str(len(cd.llm_words)),
            "fingerprinted_docs": str(sum(1 for r in cd.rows if cd.fingerprints.get(r[1]))),
            "bm25x_max_n": str(cd.bm25.max_n()),
            "bm25x_ngrams": json.dumps(cd.bm25.ngrams()),
            "n_docs": str(len(cd.rows)),
            "fts5": "0",
            "index_files": json.dumps(
                {name: hashlib.sha256(data).hexdigest() for name, data in blobs}),
        }
        con.executemany("INSERT INTO meta(key, value) VALUES (?,?)",
                        sorted(meta.items()))
        con.commit()
        con.execute("VACUUM")
        con.commit()
    finally:
        con.close()

    cell_report = {
        "cell": cell_dirname(cd.cell),
        "n_docs": len(cd.rows),
        "sira_docs": len(cd.llm_words),
        "enriched_docs": len(cd.effective),   # composed with the overlay (verify only)
        "overlay": cd.overlay_counts,
        "overlay_digest": cd.overlay_digest,
        "enrich_run": cd.enrich_run_id,
        "fingerprint": meta["source_fingerprint"],
        "blob_files": len(blobs),
        "blob_bytes": sum(len(d) for _, d in blobs),          # raw (decoded)
        "blob_stored_bytes": sum(len(d) for _, _, d in stored),  # on-disk
        "fts5": False,
    }
    return tmp, cell_report


def _finalize(tmp: Path, out_dir: Path, cell: CellKey) -> Path:
    final = out_dir / f"cell-{cell[0]}-{cell[1]}.db"
    with open(tmp, "rb") as f:
        os.fsync(f.fileno())
    os.replace(tmp, final)
    return final


def convert(db_root: Path, out_dir: Path, *, cells_filter: list[str] | None = None,
            enrich_run: str = "", use_latest: bool = False, overlay_root: str = "",
            overlay_db: str = "", fts5: bool = False, verify_n: int = 25, force: bool = False,
            source_label: str = "") -> dict:
    """Convert every selected cell under db_root; returns the report dict.
    Raises ConvertError on the first failing cell (already-finalized cells
    stay in place; the failed cell leaves no final file). `overlay_root` /
    `overlay_db` feed the identity gate only — schema 2 never stores review
    edits."""
    if fts5:
        raise ConvertError("CNV-010", "FTS5 needs a baked enriched index; "
                           "schema 2 stores the vanilla index (use bm25x)")
    out_dir.mkdir(parents=True, exist_ok=True)
    cells = enumerate_cells(db_root)
    if cells_filter:
        wanted = set(cells_filter)
        cells = [c for c in cells if cell_dirname(c) in wanted]
    if not cells:
        raise ConvertError("CNV-009", f"no cells found under {db_root}")

    # Pass 1: load everything (peers must exist before overlay verdicts).
    loaded: dict[CellKey, CellData] = {}
    for cell in cells:
        loaded[cell] = _load_cell(db_root / cell_dirname(cell), cell,
                                  enrich_run, use_latest)
    # Pass 2: fold + enrich.
    for cd in loaded.values():
        _fold_and_enrich(cd, loaded, overlay_root, overlay_db)

    report: dict = {
        "converter_version": CONVERTER_VERSION,
        "schema_version": SCHEMA_VERSION,
        "source_label": source_label,
        "cells": [],
    }
    for cell in cells:
        cd = loaded[cell]
        tmp, cell_report = _write_db(cd, out_dir, source_label, force)
        if verify_n > 0:
            probes = _probe_queries(cd, verify_n)
            cell_report["verify"] = _verify(cd, tmp, probes)
        final = _finalize(tmp, out_dir, cell)
        cell_report["file"] = final.name
        report["cells"].append(cell_report)
    return report


def _resolve_inputs(args) -> tuple[Path, str]:
    """(db_root, source_label) from --label or --db-root. A label dir holds
    sira/<cells> (promote.sh layout); --db-root points at cells directly."""
    if args.label:
        label_dir = Path(args.label)
        db_root = label_dir / "sira"
        if not db_root.is_dir():
            db_root = label_dir  # tolerate a label already scoped to sira/
        label = ""
        for mpath in (label_dir / "MANIFEST.json", db_root / "MANIFEST.json"):
            if mpath.is_file():
                try:
                    label = str(json.loads(mpath.read_text(encoding="utf-8")).get("label", ""))
                    break
                except (OSError, ValueError):
                    pass
        return db_root, (label or label_dir.name)
    return Path(args.db_root), ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="cell_db_convert",
        description="Convert a label's SIRA flat files into per-cell SQLite DBs.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--label", help="promoted label dir (<serve-root>/<label>)")
    src.add_argument("--db-root", help="raw cell root (dir holding <mno>__<release>/)")
    ap.add_argument("--out", required=True, help="output dir for cell DBs")
    ap.add_argument("--cells", default="", help="comma-separated <mno>__<release> filter")
    ap.add_argument("--enrich-run", default="", help="pin a doc-enrich run name")
    ap.add_argument("--latest-runs", action="store_true",
                    help="use the most-recent doc-enrich run (mirrors NORA_SIRA_USE_LATEST_RUNS)")
    ap.add_argument("--overlay-root", default="",
                    help="corrections root — folded in for the identity gate only "
                         "(schema 2 never stores review edits)")
    ap.add_argument("--overlay-db", default="",
                    help="Overlay store SQLite file (wins over --overlay-root); "
                         "identity gate only")
    ap.add_argument("--no-fts5", action="store_true",
                    help="accepted for compatibility; schema 2 never writes FTS5")
    ap.add_argument("--verify", type=int, default=25, metavar="N",
                    help="probe queries per cell for self-verify (0 disables; default 25)")
    ap.add_argument("--force", action="store_true", help="overwrite existing cell DBs")
    args = ap.parse_args(argv)

    db_root, source_label = _resolve_inputs(args)
    cells_filter = [c.strip() for c in args.cells.split(",") if c.strip()] or None
    try:
        report = convert(
            db_root, Path(args.out), cells_filter=cells_filter,
            enrich_run=args.enrich_run, use_latest=args.latest_runs,
            overlay_root=args.overlay_root, overlay_db=args.overlay_db,
            verify_n=args.verify, force=args.force, source_label=source_label)
    except ConvertError as exc:
        print(f"FAILED {exc}", file=sys.stderr)
        return 1

    report_path = write_report(report, Path(args.out))
    print_report(report)
    print(f"report: {report_path}")
    return 0


def write_report(report: dict, out_dir: Path) -> Path:
    """Persist the content-free conversion report; returns its path."""
    report_path = out_dir / "conversion-report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report_path


def print_report(report: dict) -> None:
    """One line per cell, chat-pasteable (counts/digests only — D-012)."""
    for c in report["cells"]:
        v = c.get("verify")
        vtxt = f"verify {v['probes']}q/k{v['k']} OK" if v else "verify skipped"
        print(f"{c['cell']:24s} docs={c['n_docs']:<6d} enriched={c['enriched_docs']:<6d} "
              f"blobs={c['blob_files']}({c['blob_stored_bytes'] >> 10}K stored, "
              f"{c['blob_bytes'] >> 20}M raw) "
              f"sira={c['sira_docs']:<6d} {vtxt}  -> {c['file']}")


if __name__ == "__main__":
    sys.exit(main())
