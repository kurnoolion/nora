"""Data stores behind service interfaces (strand corp-db-layer,
store-split-design). One protocol per store; this package holds the
local backends. S3 ships the Overlay store (review edits)."""

from core.src.stores.overlay import (
    JsonOverlayStore,
    OverlayStore,
    SqliteOverlayStore,
    open_overlay_store,
)

__all__ = [
    "JsonOverlayStore",
    "OverlayStore",
    "SqliteOverlayStore",
    "open_overlay_store",
]
