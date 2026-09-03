# query-timeline-analytics

**Status:** in-flight
**Opened:** 2026-09-03
**Landed:**
**Assignees:** Hanif
**Target modules:** query, web
**Active phase:**

## Summary

Surface per-stage query timing to the user on the `/test` page as a visual
timeline — where the seconds actually go, for both lanes.

Motivation is a standing user complaint that answers take a long time. Today
the page shows only a total (`elapsed_ms`) plus, on the SIRA lane, a plain text
line of `expand / search / rerank / synth`. The NORA lane has no per-stage
timing at all: `QueryPipeline.query()` has ten labelled stage boundaries in
comments but instruments none of them, and `QueryResponse` carries no timing
field.

Scope: capture per-stage timings on the NORA lane, plumb both lanes' timings to
the answer card, and render one shared stacked-bar timeline with per-segment
percentage and absolute ms.

This is pure analytics and display-only. It is independent of the Fast/Think
reasoning toggle (D-217) and of the provider roster (D-216) — it renders
whatever the query actually did. No accumulated cross-query comparison in this
cut; no new routes, so no team-mode gate work.

Explicitly out of scope: the legacy `/query` page (admin-only, hidden in team
mode, single-lane). It is ignored here by decision, not oversight.

## Notes

- `/test` is the page the team calls "the Ask page" — its nav label is "Ask
  Requirement Questions" (`base.html:92-97`) while its route is `/test`, and
  `playground.py` also owns `/ask/history` and `/ask/s/{row_id}`. The separate
  `/query` route is labelled "Query" and is the legacy single-lane page. This
  naming collision cost a round-trip at strand open; see the flag below.
- Flag (out of scope here, one-line fix): relabel the `/query` nav entry at
  `base.html:62-65` to "Query (legacy)" to end the ambiguity without deciding
  the page's future.
