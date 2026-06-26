"""SIRA doc-enrichment inspector — print the enrichment data for one req_id.

Doc-enrichment attaches query-expansion phrases to each requirement's BM25
index entry. This CLI surfaces, for a given req_id, the phrases SIRA added (so
you can verify the right terms land — band aliases, '5G' / 'SA NR' associations,
etc.), optionally alongside the source corpus text and the raw enrichment trace.

Multi-cell aware: searches every (MNO, release) cell under the db_root (or a
single ``--cell``), since the same req_id can exist in more than one cell.

Phrases are read from the same two places the service uses (best.jsonl is the
promoted "best" per doc; the run file is one specific enrichment run):
  * <cell>/runs/doc-enrich/<run>/enrichments.kept.jsonl
  * <cell>/enrichments/doc/best.jsonl

Usage:
    export NORA_SIRA_DB_ROOT=$HOME/work/nora/sandbox/adapter/out-db
    python -m sandbox.sira_enrich_inspect <req_id>
    python -m sandbox.sira_enrich_inspect <req_id> --text     # + corpus text
    python -m sandbox.sira_enrich_inspect <req_id> --trace    # + raw LLM trace
    python -m sandbox.sira_enrich_inspect <req_id> --cell VZW__Feb2026
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sandbox.sira_cells import cell_dirname, enumerate_cells


def _row_doc_id(row: dict) -> str | None:
    """Phrase/trace rows key the doc by `doc_id` or `_id` (sharded runs vary)."""
    return row.get("doc_id") or row.get("_id")


def _iter_jsonl(path: Path):
    """Yield parsed rows from a JSONL file, skipping blanks/bad lines."""
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def load_phrases(path: Path, req_id: str) -> list[str]:
    """Aggregate the phrases attached to `req_id` in a phrases JSONL.

    A doc_id can recur across sharded runs, so phrases are concatenated;
    duplicates are dropped while preserving first-seen order."""
    out: list[str] = []
    seen: set[str] = set()
    for row in _iter_jsonl(path):
        if _row_doc_id(row) != req_id:
            continue
        for p in row.get("phrases") or []:
            if p not in seen:
                seen.add(p)
                out.append(p)
    return out


def load_corpus_row(corpus_path: Path, req_id: str) -> dict | None:
    """The corpus.jsonl row for `req_id` (keyed by `_id`), or None."""
    for row in _iter_jsonl(corpus_path):
        if row.get("_id") == req_id:
            return row
    return None


def find_trace_row(trace_path: Path, req_id: str) -> dict | None:
    """First trace.kept.jsonl row whose doc id matches `req_id`. The trace
    schema is defined by the SIRA clone, so the row is returned verbatim."""
    for row in _iter_jsonl(trace_path):
        if _row_doc_id(row) == req_id:
            return row
    return None


def latest_run_dir(cell_dir: Path) -> Path | None:
    """The most-recently-modified doc-enrich run dir under a cell, or None."""
    runs = cell_dir / "runs" / "doc-enrich"
    if not runs.is_dir():
        return None
    candidates = [d for d in runs.iterdir() if d.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda d: d.stat().st_mtime)


def inspect_cell(
    cell_dir: Path, req_id: str, *, run: str | None,
    show_text: bool, show_trace: bool,
) -> bool:
    """Print the enrichment data for `req_id` in one cell. Returns True iff the
    req_id was found (in the corpus or any phrases source)."""
    corpus_row = load_corpus_row(cell_dir / "raw" / "corpus.jsonl", req_id)
    best = load_phrases(cell_dir / "enrichments" / "doc" / "best.jsonl", req_id)

    run_dir = (cell_dir / "runs" / "doc-enrich" / run) if run else latest_run_dir(cell_dir)
    run_phrases: list[str] = []
    run_label = "(no run)"
    if run_dir is not None and run_dir.is_dir():
        run_label = run_dir.name
        run_phrases = load_phrases(run_dir / "enrichments.kept.jsonl", req_id)

    if not (corpus_row or best or run_phrases):
        return False

    print(f"\n=== cell: {cell_dir.name} ===")
    print(f"req_id: {req_id}")
    if corpus_row is not None:
        title = corpus_row.get("title", "")
        if title:
            print(f"title:  {title}")
    else:
        print("(req_id not in this cell's corpus — phrases only)")

    print(f"\nbest.jsonl phrases ({len(best)}):")
    for p in best:
        print(f"  - {p}")
    if not best:
        print("  (none)")

    print(f"\nrun [{run_label}] enrichments.kept.jsonl phrases ({len(run_phrases)}):")
    for p in run_phrases:
        print(f"  - {p}")
    if not run_phrases:
        print("  (none)")

    if show_text and corpus_row is not None:
        text = corpus_row.get("text", "") or ""
        print(f"\ncorpus text ({len(text)} chars):")
        print(text)

    if show_trace and run_dir is not None:
        trace = find_trace_row(run_dir / "trace.kept.jsonl", req_id)
        print("\ntrace.kept.jsonl row:")
        print(json.dumps(trace, indent=2, ensure_ascii=False) if trace else "  (not found)")

    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="sira_enrich_inspect",
        description="Print SIRA doc-enrichment phrases for a req_id (multi-cell).",
    )
    ap.add_argument("req_id", help="requirement id to inspect")
    ap.add_argument(
        "--db-root", default=os.getenv("NORA_SIRA_DB_ROOT", ""),
        help="SIRA db_root (parent of the per-cell datasets); "
             "defaults to $NORA_SIRA_DB_ROOT",
    )
    ap.add_argument(
        "--cell", default=None,
        help="restrict to one cell dir name (e.g. VZW__Feb2026); "
             "default: search every cell",
    )
    ap.add_argument(
        "--run", default=None,
        help="specific doc-enrich run name; default: the latest run",
    )
    ap.add_argument("--text", action="store_true", help="also print the corpus text")
    ap.add_argument(
        "--trace", action="store_true",
        help="also print the raw enrichment trace row",
    )
    args = ap.parse_args(argv)

    if not args.db_root:
        print("error: set --db-root or $NORA_SIRA_DB_ROOT", file=sys.stderr)
        return 2
    db_root = Path(args.db_root)
    if not db_root.is_dir():
        print(f"error: db_root not found: {db_root}", file=sys.stderr)
        return 2

    if args.cell:
        cell_dirs = [db_root / args.cell]
        if not cell_dirs[0].is_dir():
            print(f"error: cell not found: {cell_dirs[0]}", file=sys.stderr)
            return 2
    else:
        cells = enumerate_cells(db_root)
        if not cells:
            print(
                f"error: no cells under {db_root} "
                f"(expected <mno>__<release>/ dirs with raw/metadata.json)",
                file=sys.stderr,
            )
            return 2
        cell_dirs = [db_root / cell_dirname(c) for c in cells]

    any_found = False
    for cell_dir in cell_dirs:
        if inspect_cell(
            cell_dir, args.req_id, run=args.run,
            show_text=args.text, show_trace=args.trace,
        ):
            any_found = True

    if not any_found:
        print(f"\nreq_id {args.req_id!r} not found in any inspected cell.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
