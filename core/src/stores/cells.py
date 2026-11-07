"""Cell identity for the SIRA-side data: `(mno, release)` keys, the
`<mno>/<release>` name (one spelling for the directory on disk, the
registry unit, the store's API and every command line — since
2026-11-06; `<mno>__<release>` is still parsed on input for older
registries, samples and links), latest-release selection and cell
enumeration under a db-root.

One home for the convention shared by the adapter (`sandbox/adapter`),
the batch orchestrator (`sandbox/sira_multi.py`), the converter
(`sandbox/cell_db_convert.py`) and the store service
(`core/src/store_service`). `sandbox/sira_cells.py` re-exports this
module for the sandbox callers.

Release identity and ordering come from the input directory convention
`<env_dir>/input/<MNO>/<MMMYYYY>/` (`core.src.extraction.release_key`):
MMM is a 3-letter title-case month, YYYY a 4-digit year (`Jan2031`). The
free-form document `release_date` is display-only and never orders.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from core.src.extraction.release_key import (  # noqa: F401  (re-exported)
    RELEASE_RE,
    is_valid_release,
    release_order_key as order_key,
)

CellKey = tuple[str, str]  # (mno, release), e.g. ("MNO-A", "Jan2031")


def cell_dirname(cell: CellKey) -> str:
    """`<mno>/<release>`, source case preserved (`MNO-A/Jan2031`): the
    cell's name everywhere — the directory under a root (`cell_dir`), the
    registry unit, the store API, the page, the command line."""
    return f"{cell[0]}/{cell[1]}"


def cell_dir(root: "Path | str", cell: CellKey) -> Path:
    """The cell's directory under `root`: `<root>/<mno>/<release>/`. A root
    written before 2026-11-06 holds `<root>/<mno>__<release>/` instead;
    that directory is returned when it exists and the nested one does not."""
    nested = Path(root) / cell[0] / cell[1]
    if nested.is_dir():
        return nested
    flat = Path(root) / f"{cell[0]}__{cell[1]}"
    return flat if flat.is_dir() else nested


def parse_cell_dirname(name: str) -> CellKey | None:
    """Inverse of `cell_dirname`: `MNO-A/Jan2031` -> ("MNO-A", "Jan2031").
    The older `MNO-A__Jan2031` spelling is accepted too (registries and
    links written before 2026-11-06).

    Returns None for any name that is not `<mno>/<MMMYYYY>` (callers skip
    non-cell names silently). Splits on the FIRST separator so an MNO
    token never contains it; the release is validated against MMMYYYY.
    """
    name = str(name or "").strip().strip("/")
    if "/" in name:
        mno, _, release = name.partition("/")
    elif "__" in name:
        mno, _, release = name.partition("__")
    else:
        return None
    if not mno or "/" in release or not is_valid_release(release):
        return None
    return (mno, release)


def latest_release(mno: str, cells: Iterable[CellKey]) -> str | None:
    """The newest release for `mno` among `cells`, by `order_key`; None when
    `mno` has no cells. "Latest per MNO" is structural: the max-ordered
    cell, not a within-index filter."""
    rels = [r for (m, r) in cells if m == mno]
    rels = [r for r in rels if order_key(r) is not None]
    return max(rels, key=order_key) if rels else None


def enumerate_cells(db_root: Path, require_marker: bool = True) -> list[CellKey]:
    """Scan `db_root` for cell datasets: `<mno>/<MMMYYYY>/` directories
    (and, for roots written before 2026-11-06, flat `<mno>__<MMMYYYY>/`)
    that hold `raw/metadata.json` (the adapter's marker; `require_marker=False`
    lists every directory shaped like a cell, for repair tools that run
    before the adapter). Other entries are skipped. Sorted."""
    db_root = Path(db_root)
    if not db_root.is_dir():
        return []

    def _is_cell(d: Path) -> bool:
        return (d / "raw" / "metadata.json").is_file() if require_marker else True

    cells: set[CellKey] = set()
    for child in sorted(db_root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        ck = parse_cell_dirname(child.name)
        if ck and _is_cell(child):
            cells.add(ck)
            continue
        if ck:
            continue
        for rel_dir in sorted(child.iterdir()):
            if rel_dir.is_dir() and is_valid_release(rel_dir.name) and _is_cell(rel_dir):
                cells.add((child.name, rel_dir.name))
    return sorted(cells)
