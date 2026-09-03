# Journal — query-timeline-analytics

## 2026-09-03 — strand opened; per-query stage timeline shipped on /test

**Where the idea started.** User wanted per-query analytics surfaced
visually — "a timeline in terms of percentage" — driven by a standing
complaint that answers take a long time. Working assumption going in
was that the data is already captured and merely unsurfaced, and that
the total seconds are not captured at all.

**What the code actually said.** Both premises needed correcting, and
the correction reshaped the scope:

- Total seconds ARE captured and displayed. `web/routes/query.py`
  measures `elapsed`; `templates/partials/query_result.html` renders
  `took {{ timing }}s`. The ask was never "add a total" — it was
  "break the total down".
- "Captured but unsurfaced" was true for exactly one lane. SIRA emits
  `expand_ms` / `search_ms` / `rerank_ms` from
  `sandbox/sira_query/service.py` and already printed them as a text
  line. The NORA lane had **zero** per-stage timing: ten labelled
  stage boundaries in `QueryPipeline.query()` comments, none
  instrumented, and no timing field on `QueryResponse` at all.

**The /query vs /test question.** User could not find "the Ask page"
and suspected the two pages were redundant. Resolved from the routes:
`/test` (playground.py) carries the nav label "Ask Requirement
Questions" and owns `/ask/history` + `/ask/s/{row_id}`; `/query`
(query.py) is labelled "Query", is single-lane, and sits inside
`base.html`'s `{% if not team %}` block so it is invisible in team
mode and absent from `_TEAM_ALLOWED`. So "the Ask page" IS `/test`,
and both lanes the strand cares about live there. `/query` is ignored
by decision — see the flag in `decisions-draft.md` for the one-line
relabel that would end the confusion without deciding the page's fate.

**What shipped.**

- `_StageTimer` in `pipeline.py` + `timings_ms` on `QueryResponse`.
  All five return paths populate it, including the pinned-chunks,
  threshold-not-found and disambiguation short-circuits.
- `core/src/web/timeline.py` — `build_timeline` normalizes either
  lane's vocabulary into one renderable shape with coarse bands.
- `templates/test/_timeline.html` — stacked bar + band summary
  visible by default, per-stage table behind a "breakdown" toggle.
- `STAGE_ORDER` / `BAND_ORDER` in `query/schema.py` as the naming
  authority for both.

**One ordering trap worth remembering.** Stage 4.7's disambiguation
`return` originally sat inside the region I wanted to time. A
`return QueryResponse(..., timings_ms=timer.as_dict())` inside a
`with timer.stage(...)` block evaluates `as_dict()` BEFORE the context
manager's `finally` records the stage — so `group` would have been
missing from the very response that spent the time in it. Restructured
to decide inside the timed block and return after it. Same hazard
applies to any future stage that short-circuits.

**Tests.** 8 new in `test_query.py` (`TestStageTimings`) covering the
absent-not-zero contract on the bypassed-graph and pinned-chunks
paths; 22 in a new `test_web_timeline.py` covering the builder plus
Jinja render of the partial for both lanes.

**Suite state.** Full run: 1889 passed, 8 failed — all 8 verified
pre-existing by running the same tests on the pristine checkout at the
same base commit (`c1a6d8c`): 6 `test_web_config` path assertions
(macOS `/private/tmp` symlink), 1 `test_embedding_ollama` protocol
generator, 1 `test_enrich_overlay_store` route registration. A further
5 `test_playground_helpers` event-loop failures appear only when that
file is run alongside `test_query.py` / `test_web_playground.py`; also
reproduced on the pristine checkout, so it is a pre-existing
test-ordering artifact, not a regression.

**Not done / next.**

- Not verified in a running browser against `~/work/env_demo`. The
  partial is render-tested through Jinja for both lanes, but nobody
  has looked at the bar on a real query yet. That is the first task
  next session.
- Not verified with the team-mode gate ON. No new routes were added
  (fields on existing responses only), so no allowlist change is
  needed — but per CLAUDE.md's branch-flow rule this should still be
  eyeballed with the gate on before the strand is called shipped.
- No accumulated cross-query comparison. Display-only by decision;
  `feedback_db` already has `query_elapsed_ms` if that is wanted
  later, but per-stage persistence would be a schema change.
