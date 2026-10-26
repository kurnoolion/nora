"""Requirement fingerprints — the join key across stores and releases.

Design: docs/compact/strands/corp-db-layer/store-split-design.md §3.

- ``text_fp(title, text)`` — the requirement's own content: title and body
  (the parser inlines tables into the body) in canonical form. The shared
  join key: the SIRA corpus sidecar (Cells), parsed trees (Trees), review
  edits (Overlay). Section number, hierarchy, plan, context and
  cross-references are excluded, so a renumbering or a change to a
  neighbouring requirement leaves it unchanged.
- ``node_fp`` / ``subtree_fp`` / ``child_set_fp`` — History only: did the
  node itself change, did anything under it change, was it only a reorder.

Every value carries its canonical-form version (``v2:<sha256 hex>``), so
values from different versions never compare equal. Callers treat the
values as opaque strings.

Canonical form v3: Unicode format characters (category Cf — byte-order
marks, zero-width characters, soft hyphens, directional marks) removed,
then NFKC, typographic quotes/dashes/bullets unified to ASCII, case fold,
whitespace runs collapsed to one space, stripped. Display text is never
altered. Numbers, units, modal verbs and negations stay significant.

v2 → v3 (2026-10-25): format characters stripped. Two releases of one
corpus differed only by byte-order marks in 194 of 326 "changed"
requirements; v2 counted every one as an edit. Values stamped `v2:` never
equal `v3:` values; the Overlay restamp (`core.src.stores.overlay_restamp`)
recomputes stored record fingerprints from the current trees.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Iterable

FINGERPRINT_VERSION = "v3"
_PREFIX = FINGERPRINT_VERSION + ":"

_WS = re.compile(r"\s+")

# Applied after NFKC (which already maps NBSP to a space, U+2011 to U+2010,
# fullwidth forms to ASCII and the small em dash to U+2014).
_TYPO = str.maketrans({
    # curly single quotes
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    # curly double quotes
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
    # the dash family: hyphen, non-breaking hyphen, figure/en/em dash,
    # horizontal bar, minus sign, small hyphen-minus
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2015": "-", "\u2212": "-", "\ufe63": "-",
    # bullet glyphs
    "\u2022": "*", "\u00b7": "*", "\u25aa": "*", "\u2023": "*",
    "\u25e6": "*", "\u25cf": "*",
})


def strip_format_chars(s: "str | None") -> str:
    """Remove Unicode format characters (category Cf: byte-order marks,
    zero-width space/joiner/non-joiner, soft hyphen, directional marks).
    They carry no content and arrive unevenly from extraction, so they
    are stripped from the IR at extraction and from the canonical form."""
    if not s:
        return ""
    return "".join(ch for ch in s if unicodedata.category(ch) != "Cf")


def canonical_text(s: "str | None") -> str:
    """Canonical form v3 of one string (module docstring)."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", strip_format_chars(s)).translate(_TYPO).casefold()
    return _WS.sub(" ", s).strip()


def _h(payload: str) -> str:
    return _PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def text_fp(title: "str | None", text: "str | None") -> str:
    """``v2:`` + sha256 of the canonical title and body."""
    return _h(canonical_text(title) + "\n" + canonical_text(text))


def node_fp(text_fp_value: str, priority: "str | None" = "",
            applicability: "Iterable[str] | None" = None) -> str:
    """``text_fp`` + priority + applicability (a sorted, canonical set)."""
    app = sorted({canonical_text(a) for a in (applicability or []) if canonical_text(a)})
    return _h(json.dumps([text_fp_value, canonical_text(priority), app],
                         ensure_ascii=False))


def subtree_fp(node_fp_value: str, child_subtree_fps: Iterable[str]) -> str:
    """``node_fp`` + the children's ``subtree_fp`` in the given (document)
    order: a reorder changes it."""
    return _h(json.dumps([node_fp_value, list(child_subtree_fps)]))


def child_set_fp(child_subtree_fps: Iterable[str]) -> str:
    """The children's ``subtree_fp`` sorted: order-insensitive, so equal
    ``child_set_fp`` with a different ``subtree_fp`` means a reorder."""
    return _h(json.dumps(sorted(child_subtree_fps)))
