"""Build the Trees store: one SQLite DB per cell (strand corp-db-layer S4).

Reads ``<env-dir>/out/parse/<mno>/<release>/*_tree.json`` and writes
``<out>/trees-<mno>-<release>.db`` per cell (schema in ``trees.py``). Each
DB is written under a temp name, verified, then renamed into place, so a
reader never sees a partial file. Verify: ``SqliteTreeStore`` over the new
file and ``FileTreeStore`` over the env dir must agree on ``list_docs``,
``load_tree`` (dict equality, every doc), ``reqs_for_plan`` (every plan) and
``find_req`` for a sample of ids (exact and case-insensitive). On a
mismatch the temp file is removed and nothing is replaced.

Per-cell layout only: trees directly under ``out/parse/`` (the legacy
flat layout) are not built.

Usage:

    python -m core.src.stores.trees_build --env-dir <dir> --out <dir> \
        [--cells MNO__REL,...] [--force]

Prints counts only — never requirement content.

Error codes (stable):
    TRS-E001  env dir has no per-cell parse trees
    TRS-E002  output DB exists and --force was not given
    TRS-E003  a requested cell has no parse trees
    TRS-E004  a tree file is unreadable or malformed
    TRS-E005  verification mismatch between the two backends
    TRS-E006  SQLite write failure
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from core.src.pipeline.error_codes import PipelineError
from core.src.stores.trees import (
    FileTreeStore,
    SqliteTreeStore,
    db_name,
    parse_dir,
    source_fingerprint,
    write_tree_db,
)

_SAMPLE = 50        # find_req ids checked per cell


def build_trees(env_dir: "str | Path", out_dir: "str | Path",
                cells: "list[str] | None" = None, force: bool = False) -> dict:
    """Build + verify every selected cell; returns the counts report.
    Raises PipelineError."""
    env_dir, out_dir = Path(env_dir), Path(out_dir)
    files = FileTreeStore(env_dir)
    available = [(c["mno"], c["release"]) for c in files.list_cells()]
    if not available:
        raise PipelineError("TRS-E001", {"path": str(parse_dir(env_dir))})
    if cells:
        selected = []
        for name in cells:
            mno, _, rel = name.partition("__")
            if (mno, rel) not in available:
                raise PipelineError("TRS-E003", {"cell": name})
            selected.append((mno, rel))
    else:
        selected = available
    for mno, rel in selected:          # refuse before writing anything
        target = out_dir / db_name(mno, rel)
        if target.exists() and not force:
            raise PipelineError("TRS-E002", {"path": str(target)})

    out_dir.mkdir(parents=True, exist_ok=True)
    counts = {"cells": 0, "docs": 0, "nodes": 0, "per_cell": {}}
    for mno, rel in selected:
        c = _build_cell(files, env_dir, out_dir, mno, rel)
        counts["per_cell"][f"{mno}__{rel}"] = c
        counts["cells"] += 1
        counts["docs"] += c["n_docs"]
        counts["nodes"] += c["n_nodes"]
    return counts


def _read_cell(env_dir: Path, mno: str, rel: str) -> tuple[list[Path], list]:
    cell = f"{mno}__{rel}"
    paths = sorted((parse_dir(env_dir) / mno / rel).glob("*_tree.json"))
    docs = []
    for p in paths:
        try:
            tree = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PipelineError("TRS-E004", {"cell": cell,
                                             "reason": type(exc).__name__}) from exc
        reqs = tree.get("requirements", []) if isinstance(tree, dict) else None
        if not isinstance(reqs, list) or not all(isinstance(r, dict) for r in reqs):
            raise PipelineError("TRS-E004", {"cell": cell,
                                             "reason": "not a tree object"})
        docs.append((p.stem.replace("_tree", ""), tree))
    return paths, docs


def _build_cell(files: FileTreeStore, env_dir: Path, out_dir: Path,
                mno: str, rel: str) -> dict:
    paths, docs = _read_cell(env_dir, mno, rel)
    fd, tmp_name = tempfile.mkstemp(prefix=".trees-", suffix=".tmp", dir=out_dir)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.unlink()                    # sqlite creates it fresh
        try:
            c = write_tree_db(tmp, mno, rel, docs, source_fingerprint(paths))
        except sqlite3.Error as exc:
            raise PipelineError("TRS-E006", {"reason": type(exc).__name__}, exc) from exc
        _verify(files, SqliteTreeStore(db_paths=[tmp]), mno, rel)
        os.replace(tmp, out_dir / db_name(mno, rel))
        return c
    finally:
        if tmp.exists():
            tmp.unlink()


def _verify(a: FileTreeStore, b: SqliteTreeStore, mno: str, rel: str) -> None:
    """Both backends must return the same content for the cell."""
    cell = f"{mno}__{rel}"

    def fail(what: str, n: int = 1):
        raise PipelineError("TRS-E005", {"cell": cell, "what": what, "count": str(n)})

    docs = a.list_docs(mno, rel)
    if docs != b.list_docs(mno, rel):
        fail("doc list")
    bad = sum(1 for d in docs if a.load_tree(d, mno, rel) != b.load_tree(d, mno, rel))
    if bad:
        fail("load_tree", bad)
    ids: list[str] = []
    plans: set[str] = set()
    for d in docs:
        tree = a.load_tree(d, mno, rel)
        for r in tree.get("requirements", []):
            if r.get("req_id"):
                ids.append(r["req_id"])
            plans.add(r.get("plan_id") or tree.get("plan_id") or "")
    ids = sorted(set(ids))
    step = max(1, len(ids) // _SAMPLE)
    bad = 0
    for rid in ids[::step]:
        for probe, ic in ((rid, False), (rid.swapcase(), True)):
            if a.find_req(probe, mno, rel, ignore_case=ic) != \
                    b.find_req(probe, mno, rel, ignore_case=ic):
                bad += 1
    if bad:
        fail("find_req", bad)
    bad = sum(1 for p in sorted(plans)
              if a.reqs_for_plan(mno, rel, p) != b.reqs_for_plan(mno, rel, p))
    if bad:
        fail("reqs_for_plan", bad)


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(
        prog="trees_build",
        description="Build one Trees SQLite DB per (MNO, release) cell from parse output.")
    ap.add_argument("--env-dir", required=True, help="env dir holding out/parse/")
    ap.add_argument("--out", required=True, help="directory for trees-<mno>-<release>.db")
    ap.add_argument("--cells", default="",
                    help="comma-separated MNO__REL cells (default: every cell)")
    ap.add_argument("--force", action="store_true",
                    help="replace existing cell DBs")
    args = ap.parse_args(argv)
    cells = [c.strip() for c in args.cells.split(",") if c.strip()] or None
    try:
        counts = build_trees(args.env_dir, args.out, cells=cells, force=args.force)
    except PipelineError as exc:
        print(f"FAILED {exc.message}", file=sys.stderr)
        if exc.hint:
            print(f"  hint: {exc.hint}", file=sys.stderr)
        return 1
    print(f"built {counts['cells']} cell DB(s): {counts['docs']} docs, "
          f"{counts['nodes']} nodes")
    for cell, c in sorted(counts["per_cell"].items()):
        print(f"  {cell}: {c['n_docs']} docs, {c['n_nodes']} nodes")
    print("verified: doc lists, trees and sampled lookups equal in both backends")
    return 0


if __name__ == "__main__":
    sys.exit(main())
