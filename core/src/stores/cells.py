"""Cell identity for the SIRA-side data: `(mno, release)` keys, the
`<mno>__<release>` directory name, latest-release selection and cell
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
    """`<mno>__<release>`, source case preserved (`MNO-A__Jan2031`).

    Double-underscore separator; case is preserved so the name round-trips
    exactly against the (mno, release) the trees carry.
    """
    return f"{cell[0]}__{cell[1]}"


def parse_cell_dirname(name: str) -> CellKey | None:
    """Inverse of `cell_dirname`: `MNO-A__Jan2031` -> ("MNO-A", "Jan2031").

    Returns None for any name that is not `<mno>__<MMMYYYY>` (callers skip
    non-cell directories silently). Splits on the FIRST '__' so an MNO
    token never contains it; the release is validated against MMMYYYY.
    """
    if "__" not in name:
        return None
    mno, _, release = name.partition("__")
    if not mno or not is_valid_release(release):
        return None
    return (mno, release)


def latest_release(mno: str, cells: Iterable[CellKey]) -> str | None:
    """The newest release for `mno` among `cells`, by `order_key`; None when
    `mno` has no cells. "Latest per MNO" is structural: the max-ordered
    cell, not a within-index filter."""
    rels = [r for (m, r) in cells if m == mno]
    rels = [r for r in rels if order_key(r) is not None]
    return max(rels, key=order_key) if rels else None


def enumerate_cells(db_root: Path) -> list[CellKey]:
    """Scan `db_root` for `<mno>__<MMMYYYY>/` cell datasets: a directory
    whose name parses as a cell key AND holds `raw/metadata.json` (the
    adapter's marker). Other entries are skipped. Sorted."""
    if not db_root.is_dir():
        return []
    cells: list[CellKey] = []
    for child in sorted(db_root.iterdir()):
        if not child.is_dir():
            continue
        ck = parse_cell_dirname(child.name)
        if ck and (child / "raw" / "metadata.json").is_file():
            cells.append(ck)
    return cells
