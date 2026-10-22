"""Data stores behind service interfaces (strand corp-db-layer,
store-split-design). One protocol per store; this package holds the
local backends. S3 ships the Overlay store (review edits); S4 the Trees
store (parsed requirement trees); S5 the History store (release lineage)."""

from core.src.stores.history import (
    HistoryStore,
    SqliteHistoryStore,
    open_history_store,
)
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
    "HistoryStore",
    "JsonOverlayStore",
    "OverlayStore",
    "SqliteHistoryStore",
    "SqliteOverlayStore",
    "SqliteTreeStore",
    "TreeStore",
    "open_history_store",
    "open_overlay_store",
    "open_tree_store",
]
