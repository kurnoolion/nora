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

**Browser + gate verification (done this session).** App run against
`~/work/env_demo` (`ENV_DIR=...`), real queries through
`POST /api/test/ask`:

- **Cold query: 2475 ms total, 97.3% unaccounted, retrieve 66 ms.**
  This is D-DRAFT-1 earning its keep on the first real query. The
  seconds were almost entirely `QueryPipeline` construction, which no
  stage timer covers. Under the rejected stage-sum denominator this
  would have rendered as "66 ms, fully accounted" — a flat lie about a
  2.5-second wait.
- **Warm query (pipeline cached on `app.state`): 9 ms total, 11%
  unaccounted, retrieve 88.9%.** The timeline cleanly separates
  cold-start cost from steady-state cost, which is the distinction the
  latency complaint actually needs.
- The warm run took the threshold-not-found early return (no
  synthesis rows), so that short-circuit's timings were exercised for
  real, not just in tests.
- **Team-mode gate ON** (`NORA_WEB_TEAM_MODE=1`): `/test` serves 200
  and renders the timeline; `/query` correctly 302s. No allowlist
  change was needed — the strand adds fields to existing responses,
  no new routes.
- Browser render confirmed in Chrome: segments lay out side by side
  with correct widths, hover titles carry exact ms + pct, the
  breakdown toggle works. Screenshot in the session transcript.

**Two defects caught after the first commit, both now fixed.**

1. *Collapse-id collision.* The merged tab composes BOTH lanes'
   `_answer.html` fragments into one DOM
   (`_render_template_to_string`), and `row_id` is None whenever
   `record_qa` raises. Keyed on `row_id` alone, the two cards shared
   `id="timeline-detail-None"` and Bootstrap's collapse targets the
   first match — clicking "breakdown" on the SIRA card would toggle
   the NORA card's table. Target is now lane-scoped; regression test
   added. **Pre-existing instances of the same flaw that this strand
   did NOT touch**, surfaced by the same check: `eng-details-None`,
   `ff-None`, `vote-up-None`, `vote-down-None` all collide in the
   merged DOM when both lanes fail to record. Worth a follow-up.
2. *Sub-second totals rendered as "0.0 s".* A warm 9 ms query read as
   a broken timer. Now shows ms below 1 s, seconds above.

**Checked and cleared (raised in review, not defects).**

- Vendored Bootstrap is **5.3.3**, where multiple `.progress-bar`
  children inside one `.progress` is the older markup. Verified
  against the vendored CSS: `.progress` is `display:flex` and the
  `width:100%` override applies only under `.progress-stacked > 
  .progress`, so the inline per-segment widths govern. Confirmed
  visually in the browser.
- The merged-tab ctx block cannot fire on a lane error — the loop
  `continue`s on `if "error" in out` before reaching it.

**Not done / next.**

- **The SIRA lane's timeline has not been seen on a live query.** The
  local box has no `sandbox/sira_query` service running (and no
  ollama), so only the NORA lane was exercised end-to-end. SIRA is
  covered by unit tests and a Jinja render, but its real
  `timings_ms` payload has not round-tripped through the page. First
  task on a box with the service up.
- No accumulated cross-query comparison. Display-only by decision;
  `feedback_db` already has `query_elapsed_ms` if that is wanted
  later, but per-stage persistence would be a schema change.

---

## 2026-09-03 — close-session: live LLM profile, ollama two-model bring-up

### Done this session

- Strand opened, worktree `query-timeline-analytics` branched off `origin/main` at `c1a6d8c`.
- Per-stage timing capture on the NORA lane + normalized two-lane timeline
  rendered on `/test` (commit `88e667f`).
- Collapse-id collision and sub-second formatting fixed after browser
  verification (commit `ece772c`).
- MODULE.md updated for `query` and `web`; four decision drafts written.
- **Ollama brought up locally serving two chat models concurrently**
  (`OLLAMA_MAX_LOADED_MODELS=2`, `OLLAMA_KEEP_ALIVE=30m`): `qwen3:1.7b`
  pulled fresh, `qwen3:8b` already local. Both resident, 7.8 GB combined.
- Web app run on port **8010** (`--port`) so it cannot collide with the
  session working `llm-roster-deploy` on 8000.

### The measurement that justifies the strand

With a real LLM and a warm pipeline, the same question profiles as:

| Model | Total | Synthesize | Retrieve | Unaccounted |
|---|---|---|---|---|
| `qwen3:1.7b` | 3.1 s | 3059 ms (99.7%) | 8 ms | 2 ms |
| `qwen3:8b` | 7.6 s | 7552 ms (99.8%) | 12 ms | 2 ms |

**Warm, the answer latency is ~99.8% LLM synthesis.** Retrieval is 8-12 ms.
Two consequences worth carrying forward:

1. Reasoning effort and model choice are effectively the ENTIRE latency
   budget. There is no retrieval optimization worth doing for latency —
   it would be fighting over 12 ms of a 7600 ms wait. This is direct
   support for the Fast/Think toggle (D-217) being the right lever.
2. Cold start is a separate, invisible second lever: the first query after
   a process restart carried **2922 ms** of pipeline construction on top,
   which never appears in warm numbers. It is why the first user of the
   day complains loudest.

The embedding side never touches ollama — `env_demo`'s vectorstore was
built with `sentence-transformers/all-MiniLM-L6-v2` and that is pinned at
build time, so both ollama slots are free for chat models.

### Next

- **SIRA lane timeline still unseen on a live query** — no local
  `sandbox/sira_query` service. Unit-tested and Jinja-rendered only. First
  task on a box with the service up.
- Re-run the cold-vs-warm split on the work PC against the real corpus;
  the `env_demo` numbers are shape-correct but tiny-corpus.
- Optional: per-stage persistence for cross-query comparison. Display-only
  by decision this cut; `feedback_db.query_elapsed_ms` already exists.

### Flags

- **Pre-existing DOM id collisions on the merged tab, not fixed here.**
  `eng-details-None`, `ff-None`, `vote-up-None`, `vote-down-None` all
  collide when both lanes render and `record_qa` fails, same root cause as
  the timeline bug fixed in `ece772c` (both lanes compose into one DOM via
  `_render_template_to_string`, `row_id` is None on failure). Worth a
  follow-up pass; out of scope for this strand.
- **`/query` vs `/test` naming** — carried from strand open. `/test` is
  labelled "Ask Requirement Questions"; `/query` is the legacy admin-only
  single-lane page. Proposed one-line relabel to "Query (legacy)" at
  `base.html:62-65`, deliberately not taken here.
