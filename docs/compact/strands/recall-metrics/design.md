# Silver recall: design

Strand `recall-metrics`, issue #24. Branched from main after #25 merged (2026-11-13). Settled in the 2026-11-11 grilling session.

## 1. Goal

After every SIRA select-synth answer, measure how deep in the BM25 fetch the evidence sat, store the numbers on that query's row, and show them under the answer. Lets anyone asking a question see retrieval quality at a glance, and lets a report average it over time and by intent.

**Silver** = the requirements the LLM cited in its answer are treated as the gold set. There are no human labels on live traffic.

What it shows and what it can't:
- Shows **depth**: whether the cited evidence came from the top 10 or needed the top 40.
- Can't show **misses**: a requirement BM25 never fetched can't be cited, so it is invisible. Live numbers overstate true recall.
- LLM picks aren't perfect gold. Good enough for a trend view.

## 2. Definitions

- **Fetched list**: SIRA's `results` for the query, in the service's order, with its `rank` field (1..N). Used as is, single-cell and multi-cell alike. Multi-cell results are round-robin interleaved by the service; no per-cell logic here (settled 2026-11-11: keep it simple).
- **Rank of a cited requirement**: the `rank` of the first fetched result with that `req_id`. When the same `req_id` sits in two cells (two releases), the better (lower) rank counts.
- **Outside**: a cited `req_id` that is not in the fetched list (a named-ID pin BM25 didn't return, or an invented id). It counts in the denominator, so it lowers recall at every k, and is also reported on its own.
- **k steps**: 10, 20, 30 … up to the number fetched, capped at 40. When the fetched count isn't a multiple of 10, it is the last step (fetched 25 → 10, 20, 25). Fetched under 10 → one step at the fetched count.
- **Silver recall@k** = cited with rank ≤ k ÷ all cited.
- **Fetched** = length of SIRA's results. **Packed** = how many SIRA results made it into the LLM context after budget packing (named-ID pins excluded). Packed < fetched means the context budget cut candidates.

The fetch size is `NORA_SIRA_SELECT_SYNTH_TOP_K` (default 40; `sandbox/run_local.sh` sets 20, so local cards show @10 and @20).

Worked example: fetched 40, packed 31, cited at ranks 2, 7, 18, 35 and one outside → cited 5, outside 1, @10 = 2/5 = 40%, @20 = 60%, @30 = 60%, @40 = 80%.

## 3. Shape in code

New `core/src/web/routes/silver_recall.py`, pure functions, no I/O (same pattern as `sira_intent.py`):

- `compute(results, packed, cited_ids) -> dict | None`: returns `None` when nothing was cited. Otherwise
  `{"recall": {"10": 0.4, "20": 0.6, "30": 0.6, "40": 0.8}, "cited": 5, "outside": 1, "fetched": 40, "packed": 31}`.
  Recall values rounded to 4 places.
- `line(lane, lane_config) -> dict | None`: reads `silver_recall` back from a stored `lane_config` for the templates; `None` for other lanes, older rows, or no citations. Takes `lane` like `sira_intent.intent_badge`, since `lane_config` does not carry it.
- A result without a `rank` field ranks by its list position.

## 4. Flow

1. `_run_select_synth_lane` (playground.py) already has `sira_results`, `packed` and the synth result.
2. After synthesis succeeds, it calls `silver_recall.compute(sira_results, packed[len(pinned_named):], cited_ids)` (the packed list minus the named-ID pins) and sets `lane_config["silver_recall"]` when the result isn't `None`.
3. The ask-stream route records the row as today. `lane_config` is already persisted, so no extra write.
4. Templates read it back through `silver_recall.line(...)`, the same way `intent_badge` is read today.

Synth error, refusal or no citations → no `silver_recall` key, nothing shown.

## 5. Storage

A `silver_recall` key inside the existing `lane_config` JSON on `test_feedback`. Only the computed numbers, no rank lists, no text.

Why `lane_config` and not a new column: it follows the intent precedent (`intent`, `intent_confidence` live there), needs no schema migration, and the shared page and History pane already read `lane_config` back. Grilling Q14 had leaned to a new column; this replaces that. **Confirm at review.**

Rows from before this change have no key: the UI shows nothing, the report skips them and says how many.

## 6. UI

One muted line at the bottom of SIRA answers, in `_answer.html` (Ask / `/test`) and `_shared_body.html` (`/ask/s/{id}` and the `/ask/history` detail pane, which reuses it):

`Silver recall  @10 40% · @20 60% · @30 60% · @40 80%   (5 cited · 1 outside · 31/40 in context)`

- "1 outside" only when non-zero.
- "31/40 in context" always shown, so a budget cut is visible.
- Tooltip: "Share of the requirements this answer cited that BM25 ranked within the top k. Cited requirements stand in for gold labels."

Team gate: no new route, nothing to allowlist. Verify the line with the gate on.

## 7. Report CLI (after capture and UI)

Averages stored `silver_recall` over a time window from the feedback DB. Sketch: `--since 7d`, `--bucket day|week`, filters `--intent`, `--stack`. Output: a table to stdout and an HTML chart under `reports/recall/<time>_silver/` with `run.json` (time, git commit, window, row counts, rows skipped), same pattern as `intent_report` and the `eval-report` skill. Also the share of queries where packed < fetched, per intent.

**Owner: `eval`** (`eval/recall_report.py`, next to `intent_report`; decided 2026-11-13). It reads the feedback DB read-only with sqlite and does not import `web`, since `web` already imports `eval`; its test builds the DB from web's schema to catch drift. Reports go to `reports/recall/`, gitignored: they come from live usage and the repo is public.

## 8. Build order

1. `silver_recall.compute` + `line` with unit tests (`core/tests/test_web_silver_recall.py`): the worked example, outside ids, duplicate req_id across cells, fetched 25 and 7, no citations.
2. Hook into `_run_select_synth_lane`; test that `lane_config` carries the key after an answer and is absent on synth error.
3. UI line in `_answer.html` and `_shared_body.html`; check in the browser on `/test`, a shared answer and the History pane, gate on.
4. Update `web/MODULE.md` (lane_config key, UI line) in the same branch.
5. Report CLI (§7).

## 9. Out of scope

Golden eval changes; ID-lookup and History lanes; the NORA lanes; per-cell recall; tuning `top_k` or the context budget (that comes after the numbers).

## 10. Tests (outline)

- `compute`: worked example; all cited in top 10; all outside; duplicate req_id keeps best rank; k steps for fetched 40 / 25 / 7 / 120 (capped at 40); empty citations → `None`.
- `line`: missing key, non-SIRA lane, older row → `None`.
- Lane hook: key present after a stubbed select-synth answer; absent on synth error.
- Run `pytest core/tests/` before calling it done.
