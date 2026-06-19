"""MMMYYYY release-label convention — parse, validate, order.

The ``(MNO, release)`` **cell** convention (multi-mno-nora D-DRAFT-6,
mirroring multi-mno-sira D-DRAFT-5) keys releases on the input directory
name ``<env_dir>/input/<MNO>/<MMMYYYY>/`` — MMM is a 3-letter title-case
month (Jan..Dec), YYYY a 4-digit year (e.g. ``"Feb2026"``). The directory
name is **both** the release label and the sort key:
``Feb2026 -> (2026, 2)``; "latest release" = max order key.

This is the single **core** home for the convention. ``infer_metadata_from_path``
enforces it at ingest (fail-loud), so a mis-named release directory is
rejected at the earliest point rather than silently mis-ordered downstream.
The convention logic was first proven in ``sandbox/sira_cells.py``; that
module reconciles onto this util when the ``multi-mno-sira`` strand lands
(``is_valid_release`` / ``release_order_key`` mirror its
``is_valid_release`` / ``order_key``). The free-form document
``release_date`` ("February 2026") is display-only and NEVER an ordering key.
"""

from __future__ import annotations

import re

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_MONTH_IDX = {m: i + 1 for i, m in enumerate(_MONTHS)}

# A conforming release label: 3-letter title-case month + 4-digit year.
RELEASE_RE = re.compile(r"^(" + "|".join(_MONTHS) + r")(\d{4})$")


def is_valid_release(release: str) -> bool:
    """True iff `release` matches the MMMYYYY convention (e.g. 'Feb2026')."""
    return RELEASE_RE.match(release or "") is not None


def release_order_key(release: str) -> tuple[int, int] | None:
    """Sortable key for a release label: 'Feb2026' -> (2026, 2).

    Returns ``None`` for a non-conforming label — the soft check, for
    callers that enumerate/skip. Ordering is strictly off the MMMYYYY
    label, never the free-form ``release_date``.
    """
    m = RELEASE_RE.match(release or "")
    if not m:
        return None
    return (int(m.group(2)), _MONTH_IDX[m.group(1)])


def release_key(release: str) -> tuple[str, tuple[int, int]]:
    """Validate and return ``(label, order_key)`` for a release; raise on miss.

    The fail-loud ingest validator (D-DRAFT-6): ``infer_metadata_from_path``
    calls this so a non-MMMYYYY release directory is rejected at the
    earliest point. Raises ``ValueError`` (prefixed ``EXT-E004``) otherwise.
    """
    ok = release_order_key(release)
    if ok is None:
        raise ValueError(
            f"EXT-E004: release {release!r} is not MMMYYYY — expected a "
            f"3-letter title-case month (Jan..Dec) + 4-digit year, "
            f"e.g. 'Feb2026'."
        )
    return (release, ok)
