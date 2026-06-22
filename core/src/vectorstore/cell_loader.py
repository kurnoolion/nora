"""Load per-cell vector stores from a vectorstore root (D-DRAFT-11).

The vectorstore stage builds one ChromaDB per `(mno, release)` cell at
`out/vectorstore/<mno>/<rel>/`. This loader enumerates those per-cell stores
for the query side (cell routing + fusion — slice 2). It **falls back to a
single flat store** at the root when no per-cell directories exist, so a
legacy single-store layout (or a transitional env) still loads.

Keys are plain `(mno, release)` tuples — not `pipeline.cells.Cell` — to avoid a
`vectorstore -> pipeline` import cycle (`pipeline` already imports
`vectorstore`). The query side maps tuples to whatever it needs.
"""

from __future__ import annotations

from pathlib import Path

from core.src.vectorstore.config import VectorStoreConfig
from core.src.vectorstore.store_chroma import ChromaDBStore

CellKey = tuple[str, str]  # (mno, release)

# Flat-fallback sentinel key — a single store that isn't cell-partitioned.
FLAT_CELL: CellKey = ("", "")


def _open(store_dir: Path) -> ChromaDBStore:
    """Open the ChromaDB at `store_dir`, honoring its persisted config."""
    cfg_path = store_dir / "config.json"
    if cfg_path.is_file():
        cfg = VectorStoreConfig.load_json(cfg_path)
    else:
        cfg = VectorStoreConfig()
    return ChromaDBStore(
        persist_directory=str(store_dir),
        collection_name=cfg.collection_name,
        distance_metric=cfg.distance_metric,
    )


def embedder_config_path(vectorstore_root) -> "Path | None":
    """Path to a `config.json` to read the (shared) embedder config from.

    Prefers the flat `<root>/config.json` (legacy single store); else any
    per-cell `<mno>/<rel>/config.json` (D-DRAFT-11) — the embedder is shared
    across cells, so any cell's config carries the right
    embedding_provider/model. Returns `None` when neither exists (caller then
    falls back to a default `VectorStoreConfig`). Without this, a per-cell
    layout has no flat config and callers default to the `sentence-transformers`
    provider → `make_embedder` tries to download from HuggingFace.
    """
    root = Path(vectorstore_root)
    flat = root / "config.json"
    if flat.exists():
        return flat
    cell_cfgs = sorted(root.glob("*/*/config.json"))
    return cell_cfgs[0] if cell_cfgs else None


def load_cell_stores(vectorstore_root) -> dict[CellKey, ChromaDBStore]:
    """Enumerate per-cell stores under `vectorstore_root`.

    Returns `{(mno, release): ChromaDBStore}`. A directory is a cell store iff
    it is nested two levels deep (`<root>/<mno>/<rel>/`) and carries a
    `config.json` (written by the build). When none are found, falls back to a
    single flat store keyed `FLAT_CELL` if `<root>/config.json` exists; else an
    empty dict.
    """
    root = Path(vectorstore_root)
    if not root.is_dir():
        return {}

    cells: dict[CellKey, ChromaDBStore] = {}
    for mno_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for rel_dir in sorted(p for p in mno_dir.iterdir() if p.is_dir()):
            if (rel_dir / "config.json").is_file():
                cells[(mno_dir.name, rel_dir.name)] = _open(rel_dir)
    if cells:
        return cells

    # Legacy / transitional flat single store.
    if (root / "config.json").is_file():
        return {FLAT_CELL: _open(root)}
    return {}
