"""Data stores behind service interfaces (strand corp-db-layer,
store-split-design). One protocol per store; this package holds the
local backends. S3 ships the Overlay store (review edits); S4 the Trees
store (parsed requirement trees)."""

from core.src.stores.overlay import (
    JsonOverlayStore,
    OverlayStore,
    SqliteOverlayStore,
    open_overlay_store,
)
from core.src.stores.trees import (
    FileTreeStore,
    SqliteTreeStore,
    TreeStore,
    open_tree_store,
)

__all__ = [
    "FileTreeStore",
    "JsonOverlayStore",
    "OverlayStore",
    "SqliteOverlayStore",
    "SqliteTreeStore",
    "TreeStore",
    "open_overlay_store",
    "open_tree_store",
]
