"""Query-scope resolution for multi-MNO SIRA (multi-mno-sira D-DRAFT-9/10).

Turns a `QueryIntent` (from NORA's query analyzer, reused via
`core.src.query.analyzer.make_query_analyzer`) into the target set of
`(MNO, release)` cells the fusion loop should retrieve from, applying
FR-multi-6 release resolution.

Two pieces:
  - `normalize_release` — the D-DRAFT-9 seam: maps the analyzer's
    human phrasing ("February 2026", "2026 feb", "latest") to the
    structured cell label ("Feb2026", "latest").
  - `resolve_cells` — the FR-multi-6 cross-product: (MNO set) x
    (release set) against the available cells, with latest/all defaults,
    returning (resolved, unresolved) so requested-but-unavailable cells
    are surfaced rather than silently dropped (fail-visible at query;
    mirror of D-DRAFT-5's fail-loud at ingest).

Cell identity / ordering primitives live in `sandbox.sira_cells`; this
module owns only the query-side scope logic.
"""

from __future__ import annotations

import re
from typing import Iterable, Protocol

from sandbox.sira_cells import CellKey, is_valid_release, latest_release

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class _IntentLike(Protocol):
    """The slice of QueryIntent that scope resolution reads."""
    mnos: list[str]
    releases: list[str]


def normalize_release(raw: str) -> str | None:
    """Map a human release phrase to a cell label, or None if unparseable.

    "February 2026" / "feb 2026" / "2026 february" -> "Feb2026"
    "latest" / "current"                            -> "latest"
    anything without a recognizable month + 20xx year -> None

    The analyzer's `_extract_releases` produces month-year-shaped strings
    (or "latest"); this bridges that phrasing to the MMMYYYY directory
    label the cells use (D-DRAFT-9). Output is validated against the
    cell-label convention before being returned.
    """
    s = (raw or "").strip().lower()
    if s in ("latest", "current"):
        return "latest"
    mon = next((m for m in _MONTHS if m.lower() in s), None)
    yr = re.search(r"20\d{2}", s)
    if not (mon and yr):
        return None
    candidate = f"{mon}{yr.group(0)}"
    return candidate if is_valid_release(candidate) else None


def resolve_cells(
    intent: _IntentLike,
    available: Iterable[CellKey],
) -> tuple[set[CellKey], list[tuple[str, str]]]:
    """Resolve a QueryIntent to target cells + the requested-but-missing list.

    FR-multi-6:
      - MNO scope: named (canonical) -> those; none named -> ALL available
        MNOs (FR-10's "missing scope expands to all").
      - Release scope: named -> exactly those (after normalization);
        none named -> each MNO's own latest (independent per MNO);
        "latest"/"current" -> that MNO's latest.

    Returns (resolved, unresolved):
      - resolved: set of (mno, release) cells to query.
      - unresolved: list of (mno, release) requests that could not be
        satisfied — a named MNO with no cells ('(mno, "*")'), an
        unparseable release phrase ('("*", raw)'), or a specific
        (mno, release) that isn't ingested. Surfaced by the caller
        (FR-multi-5) rather than silently dropped. The caller errors
        only if `resolved` is empty.
    """
    available = set(available)
    all_mnos = {m for (m, _) in available}
    resolved: set[CellKey] = set()
    unresolved: list[tuple[str, str]] = []

    # MNO scope. Named -> the available subset (absent ones surfaced as
    # misses); none named -> ALL available MNOs (FR-10). The "all"
    # fallback fires ONLY on an empty intent.mnos — naming an MNO that
    # has no cells must resolve to nothing-but-surfaced, not silently
    # expand to every MNO.
    if intent.mnos:
        target_mnos = [m for m in intent.mnos if m in all_mnos]
        for m in intent.mnos:
            if m not in all_mnos:
                unresolved.append((m, "*"))   # named MNO, no cells
    else:
        target_mnos = sorted(all_mnos)

    # Release scope — normalize; unparseable phrases become misses.
    req_releases: list[str] = []
    for r in intent.releases:
        norm = normalize_release(r)
        if norm is None:
            unresolved.append(("*", r))
        else:
            req_releases.append(norm)

    for mno in target_mnos:
        if not req_releases:                       # none named -> latest each
            latest = latest_release(mno, available)
            if latest:
                resolved.add((mno, latest))
            else:
                unresolved.append((mno, "latest"))
        else:
            for rel in req_releases:
                if rel == "latest":
                    latest = latest_release(mno, available)
                    if latest:
                        resolved.add((mno, latest))
                    else:
                        unresolved.append((mno, "latest"))
                elif (mno, rel) in available:
                    resolved.add((mno, rel))
                else:
                    unresolved.append((mno, rel))   # requested, not ingested

    return resolved, unresolved
