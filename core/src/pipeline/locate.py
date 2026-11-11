"""Where a stage's output for a cell is (strand stage-graph, design §10c).

    locate(build, stage, cell) -> Path | None

1. the build's ``out/<stage>/<MNO>/<release>/`` (``out/<stage>/`` for a
   global stage) when it exists;
2. else the stage store entry the build's ``STAGES.json`` names, when the
   store holds it and the entry's origin is not ``forced``;
3. else None.

The build is asked first, so every build made before step 4 reads as it
always has, and so outputs the store never takes (enrich, graph, vector
store, taxonomy from an unversioned image, a degraded or forced run) are
found where the build keeps them. A promoted label's ``nora/`` directory
is read the same way: its ``out/`` is complete.

The stages root is the caller's: ``NORA_STAGES_ROOT`` inside a container,
the wiring env's ``STAGES_ROOT`` on the host. Standard library only, so
the host-side driver, promote and the runner can call the CLI::

    python3 -m core.src.pipeline.locate <build> <stage> [<MNO>/<release>] [--stages-root DIR]
    python3 -m core.src.pipeline.locate <build> <stage> --cells [--stages-root DIR]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from core.src.pipeline.cells import Cell
from core.src.pipeline.error_codes import PipelineError
from core.src.pipeline.manifest import MANIFEST_NAME, BuildManifest

STAGES_ROOT_ENV = "NORA_STAGES_ROOT"
ORIGIN_FORCED = "forced"     # pipeline/fingerprints.py; repeated so this stays light


def _stages_root(stages_root: Path | str | None) -> Path | None:
    if stages_root:
        return Path(stages_root)
    env = os.environ.get(STAGES_ROOT_ENV, "")
    return Path(env) if env else None


def _manifest(build: Path) -> BuildManifest | None:
    path = build / MANIFEST_NAME
    return BuildManifest(path) if path.is_file() else None


def _cell_key(cell: Cell | None) -> str:
    return "*" if cell is None else f"{cell.mno}/{cell.release}"


def _from_store(build: Path, stage: str, cell: Cell, stages_root: Path | None) -> Path | None:
    if stages_root is None:
        return None
    manifest = _manifest(build)
    entry = manifest.entry(stage, _cell_key(cell)) if manifest else None
    if not entry or entry.get("origin") == ORIGIN_FORCED:
        return None
    path = stages_root / stage / cell.mno / cell.release / entry["fp"]
    return path if path.is_dir() else None


def locate(build: Path | str, stage: str, cell: Cell | None = None,
           stages_root: Path | str | None = None) -> Path | None:
    """The directory holding `stage`'s output for `cell` in `build`
    (`cell` None for a global stage), or None when there is none.
    Raises PipelineError PIP-E004 only when the answer depends on an
    unreadable manifest."""
    build = Path(build)
    own = build / "out" / stage if cell is None else build / "out" / stage / cell.mno / cell.release
    if own.is_dir():
        return own
    if cell is None:
        return None
    return _from_store(build, stage, cell, _stages_root(stages_root))


def cells(build: Path | str, stage: str, stages_root: Path | str | None = None) -> list[Cell]:
    """Every cell `stage` has an output for in `build`: those under its
    ``out/<stage>/`` plus those its manifest names and the store holds."""
    build = Path(build)
    found: set[Cell] = set()
    own = build / "out" / stage
    if own.is_dir():
        for mno in own.iterdir():
            if mno.is_dir():
                found.update(Cell(mno.name, rel.name) for rel in mno.iterdir() if rel.is_dir())
    root = _stages_root(stages_root)
    manifest = _manifest(build) if root is not None else None
    if manifest is not None:
        for key in manifest.fingerprints(stage):
            if key == "*" or "/" not in key:
                continue
            cell = Cell(*key.split("/", 1))
            if cell not in found and _from_store(build, stage, cell, root) is not None:
                found.add(cell)
    return sorted(found)


def stage_dirs(build: Path | str, stage: str, stages_root: Path | str | None = None
               ) -> list[tuple[Cell, Path]]:
    """``(cell, dir)`` for every cell `stage` has an output for in `build`."""
    out = []
    for c in cells(build, stage, stages_root):
        d = locate(build, stage, c, stages_root)
        if d is not None:
            out.append((c, d))
    return out


def stage_files(build: Path | str, stage: str, pattern: str, cell: Cell | None = None,
                stages_root: Path | str | None = None) -> list[Path]:
    """Files matching `pattern` in `stage`'s output: one cell's when `cell`
    is given, else the build's flat ``out/<stage>/`` (the layout from
    before per-cell outputs) followed by every cell's, in cell order. For
    the debug and audit tools, which take a document name, not a cell."""
    if cell is not None:
        d = locate(build, stage, cell, stages_root)
        return sorted(d.glob(pattern)) if d is not None else []
    flat = Path(build) / "out" / stage
    found = sorted(p for p in flat.glob(pattern) if p.is_file()) if flat.is_dir() else []
    for _, d in stage_dirs(build, stage, stages_root):
        found += sorted(d.glob(pattern))
    return found


def cell_of(path: Path, stage: str) -> Cell | None:
    """The cell a located per-cell file sits in, read from its path:
    ``.../out/<stage>/<MNO>/<release>/<file>`` or
    ``<stages-root>/<stage>/<MNO>/<release>/<fp>/<file>``."""
    parts = Path(path).parts
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] != stage:
            continue
        rest = parts[i + 1:]
        if len(rest) == 3 or (len(rest) == 4 and parts[i - 1] != "out"):
            return Cell(rest[0], rest[1])
    return None


def _parse_cell(text: str) -> Cell:
    mno, sep, release = text.partition("/")
    if not sep or not mno or not release or "/" in release:
        raise argparse.ArgumentTypeError(f"not <MNO>/<release>: {text!r}")
    return Cell(mno, release)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m core.src.pipeline.locate",
                                 description="Print where a stage's output is for a build or label.")
    ap.add_argument("build", type=Path, help="a build directory, or a label's nora/ directory")
    ap.add_argument("stage")
    ap.add_argument("cell", nargs="?", type=_parse_cell, help="<MNO>/<release>; omit for a global stage")
    ap.add_argument("--cells", action="store_true", help="list the cells the stage has an output for")
    ap.add_argument("--stages-root", type=Path, default=None,
                    help=f"the host's stage store (default: ${STAGES_ROOT_ENV})")
    args = ap.parse_args(argv)
    try:
        if args.cells:
            for c in cells(args.build, args.stage, args.stages_root):
                print(f"{c.mno}/{c.release}")
            return 0
        path = locate(args.build, args.stage, args.cell, args.stages_root)
    except PipelineError as e:
        print(f"locate: {e}", file=sys.stderr)
        return 2
    if path is None:
        where = f" {args.cell.mno}/{args.cell.release}" if args.cell else ""
        print(f"locate: no {args.stage}{where} output in {args.build}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
