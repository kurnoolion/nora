"""(MNO, release) cell primitives for the NORA pipeline (D-DRAFT-6).

The pipeline organizes **per-cell** stage outputs as
``out/<stage>/<mno>/<rel>/``, keyed on the input convention
``input/<MNO>/<MMMYYYY>/`` (see `core.src.extraction.release_key`). Stages
split into two classes:

- **per-cell** — document- / retrieval-scoped output, one copy per cell:
  ``extract, profile, parse, resolve, vectorstore``.
- **global** — the cross-cell KG layer + its shared inputs + cross-cell eval,
  one copy for the whole env: ``taxonomy, standards, graph, eval``.

The cell is the same `(MNO, release)` unit `multi-mno-sira` uses (its
D-DRAFT-3); the two strands share the vocabulary.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from core.src.extraction.release_key import is_valid_release, release_order_key

# D-DRAFT-6 stage classification. The union is the full STAGE_NAMES set
# (a test guards that this partition stays exhaustive + disjoint).
PER_CELL_STAGES = frozenset({"extract", "profile", "parse", "resolve", "vectorstore"})
GLOBAL_STAGES = frozenset({"taxonomy", "standards", "graph", "eval"})


class Cell(NamedTuple):
    """A `(MNO, release)` pair, e.g. ``Cell("VZW-OA", "Feb2026")``."""

    mno: str
    release: str

    @property
    def relpath(self) -> Path:
        """The cell's directory fragment: ``<mno>/<release>``."""
        return Path(self.mno) / self.release


def is_per_cell_stage(stage: str) -> bool:
    """True iff `stage` writes per-cell output (`out/<stage>/<mno>/<rel>/`)."""
    return stage in PER_CELL_STAGES


def enumerate_input_cells(documents_dir: Path) -> list[Cell]:
    """Scan ``input/<MNO>/<MMMYYYY>/`` → the cells present, validated + sorted.

    A `(mno, release)` qualifies iff the release directory matches the
    MMMYYYY convention **and** the cell holds at least one file.
    Non-conforming release directories are skipped here — enumeration is a
    soft scan; the fail-loud enforcement is at extract time via
    ``infer_metadata_from_path``. ``mno`` is upper-cased to match the
    metadata stamped on IRs/trees. Sorted by ``(mno, release_order_key)`` so
    "latest release" is the last cell for each MNO.
    """
    if not documents_dir.is_dir():
        return []
    cells: list[Cell] = []
    for mno_dir in sorted(documents_dir.iterdir()):
        if not mno_dir.is_dir():
            continue
        for rel_dir in sorted(mno_dir.iterdir()):
            if not rel_dir.is_dir() or not is_valid_release(rel_dir.name):
                continue
            if any(f.is_file() for f in rel_dir.rglob("*")):
                cells.append(Cell(mno_dir.name.upper(), rel_dir.name))
    cells.sort(key=lambda c: (c.mno, release_order_key(c.release)))
    return cells
