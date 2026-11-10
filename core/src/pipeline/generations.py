"""Per-cell generations at the corpus root: how a served cell is redone
(strand stage-graph, design §10b, step 3 slice 3).

A variant stage's output is identified by its fingerprint, so an unchanged
cell is never recomputed; the store and the build both say "found". To
redo one cell's language-model stages anyway (a better prompt run, a bad
batch), its generation is bumped here::

    <corpus>/generations.json
    {"version": 1, "cells": {"<MNO>/<release>": {"generation": 2, "at": "…", "by": "…", "note": "…"}}}

A generation above 1 enters every variant stage's fingerprint for that
cell (and no other cell's); generation 1, the default, enters nothing, so
the file's absence changes no fingerprint. Corpus stages never see it.

Standard library only: the host-side runner bumps it; the pipeline reads
it from the read-only corpus mount. CLI::

    python3 -m core.src.pipeline.generations show <corpus>
    python3 -m core.src.pipeline.generations bump <corpus> <MNO>/<release> [--by WHO] [--note TEXT]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

GENERATIONS_FILE = "generations.json"
_CELL_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]*/[A-Z][a-z]{2}\d{4}$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load(corpus_dir: str | Path) -> dict:
    p = Path(corpus_dir) / GENERATIONS_FILE
    if not p.is_file():
        return {"version": 1, "cells": {}}
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("cells"), dict):
        raise ValueError(f"{p}: not a generations file")
    return data


def cell_generation(corpus_dir: str | Path | None, cell_key: str) -> int:
    """The cell's generation (1 without a corpus root, a file or an entry)."""
    if corpus_dir is None:
        return 1
    entry = _load(corpus_dir)["cells"].get(cell_key) or {}
    n = entry.get("generation", 1)
    return n if isinstance(n, int) and not isinstance(n, bool) and n >= 1 else 1


def bump(corpus_dir: str | Path, cell_key: str, by: str = "", note: str = "") -> int:
    """Raise the cell's generation by one and return the new value. The
    file is rewritten atomically."""
    if not _CELL_RE.match(cell_key):
        raise ValueError(f"cell {cell_key!r}: expected <MNO>/<MMMYYYY>")
    p = Path(corpus_dir) / GENERATIONS_FILE
    data = _load(corpus_dir)
    n = cell_generation(corpus_dir, cell_key) + 1
    data["cells"][cell_key] = {"generation": n, "at": _now_iso(), "by": by, "note": note}
    data["cells"] = dict(sorted(data["cells"].items()))
    tmp = p.with_name(f".{p.name}.{uuid.uuid4().hex[:8]}")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="generations", description="Per-cell generations at the corpus root.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("show")
    s.add_argument("corpus")
    b = sub.add_parser("bump")
    b.add_argument("corpus")
    b.add_argument("cell")
    b.add_argument("--by", default="")
    b.add_argument("--note", default="")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "show":
            for key, e in sorted(_load(args.corpus)["cells"].items()):
                print(f"{key} generation={e.get('generation')} at={e.get('at', '')}")
        else:
            print(bump(args.corpus, args.cell, by=args.by, note=args.note))
    except (OSError, ValueError) as e:
        print(f"generations: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
