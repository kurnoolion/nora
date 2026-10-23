"""Exceptions shared by the store backends and their HTTP clients
(strand corp-db-layer S6; store-split-design §5, §9). Stdlib only."""

from __future__ import annotations


class StoreUnavailable(RuntimeError):
    """An HTTP store client could not get an answer from its service
    (connection refused, timeout, 5xx). Callers that degrade without a
    store (nora-store composing without an overlay, the web's optional
    surfaces) keep degrading; others surface it."""


class SchemaVersionError(RuntimeError):
    """A store file carries a `schema_version` this reader does not
    support (design §9: breaking changes bump the version; the reader
    refuses with a code naming the store to rebuild)."""

    def __init__(self, code: str, store: str, name: str, found: str,
                 supported: str, remedy: str):
        self.code = code
        super().__init__(
            f"[{code}] {store} DB {name} has schema_version {found!r}; "
            f"this reader supports {supported} — {remedy}")
