"""Shared (MNO, release) cell primitives for multi-MNO SIRA.

Single home for the cell identity / ordering / enumeration logic used by:
  - the adapter (`nora_to_beir.py`) — partitions trees into cells,
  - the batch orchestrator (`sira_multi.py`) — enumerates cells to run,
  - the runtime query service (`sira_query/service.py`) — loads cells +
    resolves query scope to a target cell set.

Centralizing avoids three drifting copies of the MMMYYYY convention.
See multi-mno-sira D-DRAFT-3/4/5/6.

Release identity + ordering come from the input directory name
convention `<env_dir>/input/<MNO>/<MMMYYYY>/` — MMM is a 3-letter
title-case month, YYYY a 4-digit year (e.g. "Feb2026"). The free-form
document `release_date` ("February 2026") is display-only and is NEVER
used for ordering.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

CellKey = tuple[str, str]  # (mno, release), e.g. ("VZW", "Feb2026")

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_MONTH_IDX = {m: i + 1 for i, m in enumerate(_MONTHS)}

# A conforming release label: 3-letter title-case month + 4-digit year.
RELEASE_RE = re.compile(r"^(?:" + "|".join(_MONTHS) + r")\d{4}$")


def is_valid_release(release: str) -> bool:
    """True iff `release` matches the MMMYYYY convention (e.g. 'Feb2026')."""
    return bool(RELEASE_RE.match(release or ""))


def cell_dirname(cell: CellKey) -> str:
    """`<mno>__<release>`, source-case preserved (e.g. 'VZW__Feb2026').

    Double-underscore separator; case is preserved so the name
    round-trips exactly against `infer_metadata_from_path`-derived
    (mno, release). See D-DRAFT-6.
    """
    return f"{cell[0]}__{cell[1]}"


def parse_cell_dirname(name: str) -> CellKey | None:
    """Inverse of `cell_dirname`: 'VZW__Feb2026' -> ('VZW', 'Feb2026').

    Returns None for any name that isn't `<mno>__<MMMYYYY>` (so callers
    can skip non-cell directories silently). Splits on the FIRST '__'
    so an MNO token never contains it; the release is validated against
    the MMMYYYY convention.
    """
    if "__" not in name:
        return None
    mno, _, release = name.partition("__")
    if not mno or not is_valid_release(release):
        return None
    return (mno, release)


def order_key(release: str) -> tuple[int, int] | None:
    """Sortable key for a release label: 'Feb2026' -> (2026, 2).

    Returns None for a non-conforming label. Sorting strictly off the
    MMMYYYY directory label — NEVER the free-form `release_date`
    (D-DRAFT-5 trap).
    """
    m = re.match(r"^([A-Z][a-z]{2})(\d{4})$", release or "")
    if not m:
        return None
    idx = _MONTH_IDX.get(m.group(1))
    return (int(m.group(2)), idx) if idx else None


def latest_release(mno: str, cells: Iterable[CellKey]) -> str | None:
    """The newest release for `mno` among `cells`, by `order_key` max.

    Returns None if `mno` has no cells. Resolution of an unspecified
    release to "latest per MNO" (FR-multi-6, D-DRAFT-2) is structural —
    pick the max-ordered cell, not a within-index filter.
    """
    rels = [r for (m, r) in cells if m == mno]
    rels = [r for r in rels if order_key(r) is not None]
    return max(rels, key=order_key) if rels else None


def enumerate_cells(db_root: Path) -> list[CellKey]:
    """Scan `db_root` for `<mno>__<MMMYYYY>/` cell datasets.

    A directory qualifies as a cell iff its name parses as a cell key
    AND it contains `raw/metadata.json` (the adapter's marker that the
    cell was emitted). Non-conforming directories are skipped silently —
    `db_root` may legitimately hold other things. Returns a sorted list.
    """
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
