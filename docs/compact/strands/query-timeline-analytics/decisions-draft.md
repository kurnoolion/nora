# Decisions draft — query-timeline-analytics

Promoted to canonical `DECISIONS.md` with sequential D-XXX ids at
`/land-strand`.

---

## D-DRAFT-1 — The query timeline's denominator is the route's wall clock, not the sum of the stages

**Context.** Per-stage timings never cover the whole request. The NORA
lane's `total_ms` measures `QueryPipeline.query()` only, excluding
pipeline construction (~5-15s cold, per the `query.py` cache comment).
The SIRA lane's `expand_ms` / `search_ms` / `rerank_ms` come from the
SIRA service and exclude the HTTP round-trip. Rendering percentages of
the measured sum would produce a bar that always totals 100% while
silently hiding the time the user actually waited.

**Decision.** `web.timeline.build_timeline` takes the route-measured
wall clock as `total_ms` and emits `total_ms - sum(stages)` as an
explicit `unaccounted` segment, rendered in the bar and named in the
breakdown table.

**Alternatives rejected.**

- *Denominator = sum of measured stages.* Segments would sum cleanly
  and the bar would look tidier. Rejected: it hides exactly the latency
  the strand exists to explain. A cold request whose time went almost
  entirely into pipeline construction would render as a
  fast, fully-accounted query — the opposite of the truth.
- *Distribute the remainder proportionally across the stages.* Rejected
  as actively misleading: it invents attribution the timers did not
  measure, and would make a construction-dominated request look like a
  uniformly slow pipeline.
- *Omit the remainder and renormalize.* Same defect as the first
  option, with the added problem that the SIRA lane's large
  round-trip gap would vanish without trace.

**Consequence.** On cold requests the `unaccounted` segment dominates.
That is intended — it is the signal that the time is going somewhere
the stage timers do not reach.

---

## D-DRAFT-2 — A skipped stage is absent from `timings_ms`, not zero

**Context.** Stages 3 (graph scoping), 3.5 (rewrite), 4.5 (threshold)
and 4.7 (grouping) are all conditional. Stage 3 in particular is fully
bypassed in pure-RAG mode.

**Decision.** `_StageTimer` only records stages that execute, so a
bypassed stage has no key in `QueryResponse.timings_ms`. Consumers
treat a missing key as "did not run". `build_timeline` tests
`is None` rather than truthiness, so a stage that genuinely ran in
under a millisecond still renders.

**Alternative rejected.** *Emit `0` for every stage in `STAGE_ORDER`,
so the dict shape is uniform.* Rejected: a uniform shape is easier to
consume but renders a bypassed stage as instantaneous, which is a
different and wrong claim. Distinguishing "instant" from "did not
happen" is the whole diagnostic value on the RAG-only path.

---

## D-DRAFT-3 — One normalized timeline shape for both lanes, with coarse bands for comparability

**Context.** The two `/test` lanes do not share a stage vocabulary.
NORA has ten labelled stages (Stage 1..6.5); SIRA has four from its own
service (`expand` / `search` / `rerank` / `synth`). The user's stated
interest is in reading BOTH flows.

**Decision.** `build_timeline(stages_ms, total_ms, lane)` maps either
vocabulary onto one structure, and every stage carries a coarse `band`
(`prep` / `retrieval` / `synthesis` / `post`, plus `unaccounted`).
Stage detail stays lane-native in the breakdown table; the band
summary above the bar is what makes the two lanes comparable at a
glance. `STAGE_ORDER` and `BAND_ORDER` live in `query.schema` as the
single naming authority.

**Alternatives rejected.**

- *Two separate components, one per lane.* Rejected: it defeats the
  side-by-side reading the strand was opened for, and doubles the
  template surface for no gain.
- *Force SIRA's four stages into NORA's ten-slug vocabulary.* Rejected:
  the mapping would be a lie in both directions — SIRA's `search`
  is not NORA's Stage 4, and NORA has no equivalent of SIRA's
  service-side expand. Bands abstract at the level where the two
  genuinely correspond.

---

## D-DRAFT-4 — The timeline renders above the engineering-details fold

**Context.** `test/_answer.html` collapses retrieval internals,
citations, chunks, prompts and timings behind one "Engineering
details" toggle, explicitly "not for the everyday ask-a-question
user". The SIRA per-stage timing line lived inside that collapse.

**Decision.** The timeline bar, total, and band summary render
immediately below the answer body, visible by default; the per-stage
table sits behind its own small "breakdown" toggle.

**Rationale.** The strand's motivating complaint — "it takes a lot of
time" — comes from the everyday user, not from engineering review. A
latency explanation that is only visible to someone who knows to
expand engineering details does not reach the person complaining.

**Consequence.** The old `SIRA timing:` text line was reduced to just
its rerank call-distribution stats (`count` / `mean` / `p50` / `p95` /
`max`), which the timeline does not carry. The per-stage numbers it
used to print are now in the timeline, so keeping both would have been
duplication.

---

## Flag (not a decision) — `/query` vs `/test` naming

`/test` is labelled "Ask Requirement Questions" in the nav and is what
the team calls "the Ask page"; `/query` is labelled "Query" and is the
legacy admin-only single-lane page, hidden in team mode. The collision
cost a round-trip at strand open. Proposed one-line fix, deliberately
NOT taken in this strand: relabel the `/query` nav entry
(`base.html:62-65`) to "Query (legacy)". That deprecates nothing and
needs no decision about the page's future.
