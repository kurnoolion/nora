"""Requirement content fingerprint — the join key across stores and releases.

One function, used by every store that carries a requirement: the SIRA
corpus sidecar (Cells), parsed trees (Trees), release lineage (History) and
review edits (Overlay). Design: docs/compact/strands/corp-db-layer/
store-split-design.md §3.

The fingerprint covers the requirement's own content only — title and body
(the parser inlines tables into the body). Section number, hierarchy,
context and cross-references are excluded, so renumbering or a change to a
neighbouring requirement leaves it unchanged.
"""

from __future__ import annotations

import hashlib
import re

_WS = re.compile(r"\s+")


def normalize_content(text: str) -> str:
    """Case-folded, whitespace-collapsed, stripped."""
    return _WS.sub(" ", (text or "").casefold()).strip()


def req_fingerprint(title: str, text: str) -> str:
    """sha256 hex of the normalized title and body."""
    payload = normalize_content(title) + "\n" + normalize_content(text)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
