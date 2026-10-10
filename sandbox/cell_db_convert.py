"""Cell-DB converter — phase 1 of the corp-db-layer strand.

Converts one promoted label's SIRA flat files into one SQLite **cell DB**
per (MNO, release) cell: corpus + enrichment as rows, and the ENRICHED
serialized bm25x index as blobs. A cell-db stack (phase 2) serves from
these files and is A/B-evaluated against the flat-file stack on identical
content. Design: docs/compact/strands/corp-db-layer/converter-design.md.

Parity by construction: the converter applies enrichment with the exact
steps the service uses (BM25.load → disable_auto_save → enrich_batch of
the overlay-folded effective sets) and stores the resulting index bytes —
a cell-db stack loads the same engine from the same bytes. Self-verify
(--verify N) replays probe queries through the in-memory enriched index
vs a fresh index loaded from the DB blobs; any ranking difference fails
that cell's conversion.

Usage (inside the sandbox venv, from the repo root):

    python -m sandbox.cell_db_convert --label <serve-root>/<label> --out <dir>
    python -m sandbox.cell_db_convert --db-root <cells-dir> --out <dir> \
        [--cells <mno>__<release>,...] [--enrich-run NAME | --latest-runs] \
        [--overlay-root <corrections-root>] [--no-fts5] [--verify N] [--force]

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
from sandbox.sira_query.enrich_overlay import (
    allowed_labels,
    apply_overlay_to_req,
    filter_overlay,
    load_accepted_labels,
    load_overlay,
    make_verdict_fn,
)

SCHEMA_VERSION = 1
CONVERTER_VERSION = "0.1"

_DDL = """
CREATE TABLE meta(
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE corpus(
  doc     INTEGER PRIMARY KEY,          -- stable load-order index
  req_id  TEXT UNIQUE NOT NULL,         -- corpus.jsonl `_id`
  title   TEXT NOT NULL DEFAULT '',
  text    TEXT NOT NULL DEFAULT ''
);
CREATE TABLE enrichment(
  req_id  TEXT PRIMARY KEY,             -- post-overlay-fold EFFECTIVE set
  phrases TEXT NOT NULL,                -- JSON array of phrases
  run_id  TEXT NOT NULL DEFAULT ''
);
CREATE TABLE index_blob(
  filename TEXT PRIMARY KEY,            -- bm25x serialized file name
  codec    TEXT NOT NULL DEFAULT 'zlib',-- 'zlib' | 'raw'
  data     BLOB NOT NULL                -- codec-encoded file bytes
);
"""

_FTS_DDL = "CREATE VIRTUAL TABLE fts USING fts5(toks);"


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
    (sira_query/service.py): streamed sha256 over corpus.jsonl + the
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

    run_dir = _resolve_run_dir(base / "runs" / "doc-enrich", enrich_run, use_latest)
    enrich_model = _enrich_model_of_run(run_dir) if run_dir is not None else ""
    phrases_path: Path | None = None
    run_id = ""
    if run_dir is not None and (run_dir / "enrichments.kept.jsonl").exists():
        phrases_path = run_dir / "enrichments.kept.jsonl"
        run_id = run_dir.name
    if phrases_path is None:
        fallback = base / "enrichments" / "doc" / "best.jsonl"
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
    )


def _fold_and_enrich(cd: CellData, cells: dict[CellKey, CellData],
                     overlay_root: str) -> None:
    """Pass 2: fold the corrections overlay (main view) into the effective
    sets and apply them to the in-memory index. Mirrors
    service._apply_overlay_and_enrich with label='' — peers for the
    cross-release verdict are the cells being converted."""
    mno, release = cd.cell
    try:
        if overlay_root:
            accepted = load_accepted_labels(overlay_root)
            overlay = filter_overlay(load_overlay(overlay_root, mno),
                                     allowed_labels(accepted, ""))
        else:
            overlay = {}
    except Exception as exc:
        raise ConvertError("CNV-008", f"overlay load failed for {cell_dirname(cd.cell)}: {type(exc).__name__}") from exc
    cd.overlay_digest = _overlay_digest(overlay)

    def token_sets(rel: str, req_id: str):
        peer = cells.get((mno, rel)) if rel != release else cd
        return peer.vanilla_tokens(req_id) if peer is not None else None

    verdict = make_verdict_fn(token_sets, release)

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
    """Load the index back from the DB blobs and require rankings
    IDENTICAL to the in-memory enriched index."""
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


def _fts_token_stream(cd: CellData, rid: str, text: str) -> str:
    """Unigram token stream for the optional FTS5 table: the index's own
    tokenizer over the corpus text, plus each effective phrase tokenized
    in isolation (matching bm25x enrich semantics — no cross-boundary
    tokens, repetitions preserved)."""
    toks = list(cd.bm25.tokenize(text))
    for phrase in cd.effective.get(rid, []):
        toks.extend(cd.bm25.tokenize(phrase))
    return " ".join(toks)


def _write_db(cd: CellData, out_dir: Path, source_label: str,
              fts5: bool, force: bool) -> tuple[Path, dict]:
    mno, release = cd.cell
    final = out_dir / f"cell-{mno}-{release}.db"
    if final.exists() and not force:
        raise ConvertError("CNV-004", f"{final.name} exists (use --force to overwrite)")
    tmp = out_dir / f".tmp-{final.name}"
    tmp.unlink(missing_ok=True)

    # Serialize the ENRICHED index to blobs. zlib matters: bm25x's hashed
    # n-gram side serializes its full slot table (~134 MB of mostly zeros
    # at default n_features) regardless of corpus size; compression takes
    # it down ~500x. Decompression restores byte-identical files, so
    # parity is untouched.
    with tempfile.TemporaryDirectory(prefix="cnv-save-") as td:
        cd.bm25.save(td)
        blob_files = sorted(p for p in Path(td).iterdir() if p.is_file())
        blobs = [(p.name, p.read_bytes()) for p in blob_files]
        stored = [(name, "zlib", zlib.compress(data, 6)) for name, data in blobs]

    con = sqlite3.connect(tmp)
    try:
        con.executescript(_DDL)
        if fts5:
            con.executescript(_FTS_DDL)
        con.executemany(
            "INSERT INTO corpus(doc, req_id, title, text) VALUES (?,?,?,?)", cd.rows)
        con.executemany(
            "INSERT INTO enrichment(req_id, phrases, run_id) VALUES (?,?,?)",
            [(rid, json.dumps(ph, ensure_ascii=False), cd.enrich_run_id)
             for rid, ph in sorted(cd.effective.items())])
        con.executemany(
            "INSERT INTO index_blob(filename, codec, data) VALUES (?,?,?)", stored)
        if fts5:
            con.executemany(
                "INSERT INTO fts(rowid, toks) VALUES (?,?)",
                [(doc, _fts_token_stream(cd, rid, text))
                 for doc, rid, _title, text in cd.rows])

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
            "enriched_docs": str(len(cd.effective)),
            "overlay_digest_baked": cd.overlay_digest,
            "overlay_counts": json.dumps(cd.overlay_counts),
            "bm25x_max_n": str(cd.bm25.max_n()),
            "bm25x_ngrams": json.dumps(cd.bm25.ngrams()),
            "n_docs": str(len(cd.rows)),
            "fts5": "1" if fts5 else "0",
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
        "enriched_docs": len(cd.effective),
        "overlay": cd.overlay_counts,
        "overlay_digest": cd.overlay_digest,
        "enrich_run": cd.enrich_run_id,
        "fingerprint": meta["source_fingerprint"],
        "blob_files": len(blobs),
        "blob_bytes": sum(len(d) for _, d in blobs),          # raw (decoded)
        "blob_stored_bytes": sum(len(d) for _, _, d in stored),  # on-disk
        "fts5": fts5,
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
            fts5: bool = True, verify_n: int = 25, force: bool = False,
            source_label: str = "") -> dict:
    """Convert every selected cell under db_root; returns the report dict.
    Raises ConvertError on the first failing cell (already-finalized cells
    stay in place; the failed cell leaves no final file)."""
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
        _fold_and_enrich(cd, loaded, overlay_root)

    report: dict = {
        "converter_version": CONVERTER_VERSION,
        "schema_version": SCHEMA_VERSION,
        "source_label": source_label,
        "cells": [],
    }
    for cell in cells:
        cd = loaded[cell]
        tmp, cell_report = _write_db(cd, out_dir, source_label, fts5, force)
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
                    help="corrections root — bake the overlay (main view) into the DB")
    ap.add_argument("--no-fts5", action="store_true", help="skip the optional FTS5 table")
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
            overlay_root=args.overlay_root, fts5=not args.no_fts5,
            verify_n=args.verify, force=args.force, source_label=source_label)
    except ConvertError as exc:
        print(f"FAILED {exc}", file=sys.stderr)
        return 1

    report_path = Path(args.out) / "conversion-report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for c in report["cells"]:
        v = c.get("verify")
        vtxt = f"verify {v['probes']}q/k{v['k']} OK" if v else "verify skipped"
        print(f"{c['cell']:24s} docs={c['n_docs']:<6d} enriched={c['enriched_docs']:<6d} "
              f"blobs={c['blob_files']}({c['blob_stored_bytes'] >> 10}K stored, "
              f"{c['blob_bytes'] >> 20}M raw) "
              f"fts5={'y' if c['fts5'] else 'n'}  {vtxt}  -> {c['file']}")
    print(f"report: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
