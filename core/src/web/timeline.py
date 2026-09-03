"""Normalize per-stage query timings into a renderable timeline.

Both `/test` lanes report where their seconds went, but they do not
agree on vocabulary:

  - the NORA lane emits `QueryResponse.timings_ms` — the Stage 1..6.5
    slugs listed in `core.src.query.schema.STAGE_ORDER`;
  - the SIRA lane emits `timings_ms` from the SIRA service —
    `expand_ms` / `search_ms` / `rerank_ms`, plus a `synth_ms` the
    playground route injects for the NORA-side synthesizer call.

This module maps both onto one shape so a single template partial can
render either, and so the two can be read side by side via the coarse
`band` (prep / retrieval / synthesis / post).

Two honesty rules are enforced here rather than left to the template:

  1. **The parts do not sum to the whole.** Neither lane instruments
     everything — the NORA total excludes pipeline construction, and
     the SIRA numbers exclude the HTTP round-trip. The remainder is
     emitted as an explicit `unaccounted` segment instead of being
     silently dropped, which would make the bar over-claim its
     coverage. On a cold request that segment is large; that is a
     finding, not a defect to hide.
  2. **A skipped stage is absent, not zero.** Callers pass only the
     stages that ran. A stage that did not run gets no segment at all,
     so a bypassed stage never renders as "instantaneous".
"""

from __future__ import annotations

from core.src.query.schema import BAND_ORDER, STAGE_ORDER

#: SIRA's own timing keys → (display label, band). SIRA runs its own
#: retrieval stack, so its vocabulary is fixed by the service; the band
#: is what makes it comparable to the NORA lane.
_SIRA_STAGES: dict[str, tuple[str, str]] = {
    "expand_ms": ("Expand", "prep"),
    "search_ms": ("Search", "retrieval"),
    "rerank_ms": ("Rerank", "retrieval"),
    "synth_ms": ("Synthesize", "synthesis"),
}

#: Label and band for the remainder segment.
_UNACCOUNTED_LABEL = "Unaccounted"
_UNACCOUNTED_BAND = "unaccounted"


def _pct(part_ms: int, total_ms: int) -> float:
    """Percentage of `total_ms`, rounded to one decimal. 0.0 when the
    total is zero or negative — a sub-millisecond query is possible on
    a mock pipeline and must not divide by zero."""
    if total_ms <= 0:
        return 0.0
    return round(100.0 * part_ms / total_ms, 1)


def build_timeline(
    stages_ms: dict | None,
    total_ms: int | None,
    lane: str,
) -> dict | None:
    """Build one renderable timeline.

    Args:
        stages_ms: Per-stage milliseconds as reported by the lane. Keys
            are NORA stage slugs or SIRA `*_ms` keys depending on
            `lane`. Unknown keys are ignored rather than guessed at.
            A `total_ms` key (NORA's own pipeline total) is not treated
            as a stage.
        total_ms: Wall-clock milliseconds the user actually waited, as
            measured by the route. This — not the sum of the stages —
            is the denominator, so setup and transport show up in the
            `unaccounted` segment instead of vanishing.
        lane: `"nora"` or `"sira"`. Selects the stage vocabulary.

    Returns:
        A dict with `total_ms`, ordered `segments`, and aggregated
        `bands`; or None when there is nothing worth drawing (no
        stages reported, or no positive total).
    """
    if not stages_ms or not total_ms or total_ms <= 0:
        return None

    if lane == "sira":
        known = [
            (key, label, band) for key, (label, band) in _SIRA_STAGES.items()
        ]
    else:
        known = list(STAGE_ORDER)

    segments = []
    for key, label, band in known:
        value = stages_ms.get(key)
        # Absent means the stage did not run. `is None` rather than a
        # falsy check, so a genuine 0 ms stage that DID run still
        # renders (it just renders as a hairline).
        if value is None:
            continue
        ms = max(0, int(value))
        segments.append({
            "slug": key,
            "label": label,
            "band": band,
            "ms": ms,
            "pct": _pct(ms, total_ms),
        })

    if not segments:
        return None

    measured = sum(s["ms"] for s in segments)
    unaccounted = max(0, total_ms - measured)
    segments.append({
        "slug": _UNACCOUNTED_BAND,
        "label": _UNACCOUNTED_LABEL,
        "band": _UNACCOUNTED_BAND,
        "ms": unaccounted,
        "pct": _pct(unaccounted, total_ms),
    })

    band_totals: dict[str, int] = {}
    for seg in segments:
        band_totals[seg["band"]] = band_totals.get(seg["band"], 0) + seg["ms"]
    bands = [
        {"band": b, "ms": band_totals[b], "pct": _pct(band_totals[b], total_ms)}
        for b in BAND_ORDER
        if b in band_totals
    ]

    return {
        "lane": lane,
        "total_ms": int(total_ms),
        "measured_ms": measured,
        "unaccounted_ms": unaccounted,
        "segments": segments,
        "bands": bands,
    }
