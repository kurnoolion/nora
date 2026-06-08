## 2026-05-28 — Build phase complete: merged-tab live

### Done this session
- Backend foundation (commit f625286):
  - Extended `core/src/web/feedback_db.py` test_feedback schema with 8 new
    nullable columns (lane, user_name, retrieved_ids, reranked_ids,
    cited_ids, user_score, user_categories, lane_config). Idempotent
    upgrade path via PRAGMA-driven ADD COLUMN — legacy DBs migrate in
    place on next service start; row preservation verified.
  - CATEGORIES module constant (4 frozen keys + labels).
  - record_qa extended with optional merged-tab kwargs (legacy callers
    unchanged). New record_user_feedback for 0–9 score + categories +
    comment + user_name; does not touch the legacy vote column. 22 tests.
- Citation + snapshot helpers in playground.py (commit 54b7d58):
  _flatten_cited_ids, _pick_sira_snapshot, async _snapshot_sira_lane_config
  (fault-tolerant: returns {"_error": ...} on failure rather than blocking
  the Ask), _snapshot_nora_lane_config. 10 tests.
- Merged-tab UI + endpoint (commit ae1719f):
  - Templates: extracted _feedback_legacy.html, new _feedback_merged.html
    (score select + 4 checkboxes + comment), _answer.html feedback widget
    became conditional include, new _merged_answer.html (responsive
    col-md-6 / col-md-12), rewrote index.html with the merged form
    (question + NORA/SIRA checkboxes default-checked + optional name +
    Ask). _feedback_ack.html acknowledges merged successes.
  - Endpoint: section=='merged' branch in playground_ask runs enabled
    lanes in parallel (fault-isolated — one lane's failure renders an
    error alert, the other still works), inserts one test_feedback row
    per (question x lane), pre-renders each lane's _answer.html to a
    string, returns _merged_answer.html. playground_feedback dispatches
    on user_score presence (merged path) vs vote (legacy path).
- Work-PC smoke passed for the basic flow.

### In progress
- (none — build phase done, strand transitions to feedback-collection mode)

### Next
- Team begins using the merged tab for ad-hoc questions; feedback rows
  accumulate in <env_dir>/state/nora_test_feedback.db.
- When meaningful feedback volume arrives, run SQL analyses
  (NORA-vs-SIRA score deltas by question; category clustering by lane;
  comment patterns) and journal findings here.
- Decide whether to retire the legacy section-tab URLs
  (?section=requirement_bot / ?section=sira_retrieval) once any
  bookmarks are migrated.
- Deferred (carry forward): export CLI for SQLite → CSV/JSONL sharing
  outside the env_dir.

### Flags
- Visual nesting (border-in-border) inside each lane column was accepted
  for the pilot — the per-lane _answer.html keeps its own border + Q
  heading, which appear inside the merged container's wrapper. Flag for
  polish if the team reports it reads cluttered; otherwise ships as is.
- Lane-config snapshot for NORA is intentionally minimal for the pilot
  (llm_model + query_intent + candidate_count + 5 env vars). Will likely
  need to grow as analyses identify which knobs matter on which questions.
- `core/src/web/MODULE.md` Public surface section does not yet enumerate
  the new `feedback_db.py` additions (`CATEGORIES`, `record_user_feedback`,
  `_MERGED_TAB_COLUMNS`). Deferred to a future `/drift-check dev-module web`
  pass rather than fixing inline this session.

## 2026-06-01 — TODO: rule-based eval KPI tracking (parked, pick up later)

### Context

Mid-pilot strategy discussion on whether to adopt RAGAS for systematic
iteration + KPI tracking. Conclusion: **build rule-based deterministic
metrics that plug into `core/src/eval/`** rather than RAGAS, to preserve
the D-015 offline-deterministic posture and avoid LLM-judge variance
amplifying the MoE non-determinism already pinned in D-DRAFT-4 of
plan-aware-sira. RAGAS as a monthly spot-check is fine; not as the
inner-loop KPI surface.

### Two KPIs to implement

**Faithfulness** = mean of three sub-signals (or weighted; see open
question 1):
- `citation_grounding` — every `[req_id]` citation in the answer points
  to a req_id that's in `retrieved_ids`. Catches phantom citations.
- `numeric_grounding` — numeric tokens in the answer (timer values,
  `3GPP TS 24.301` version strings, etc.) appear verbatim in the cited
  chunks' text. Catches hallucinated numbers.
- `entity_grounding` — telecom acronyms / CAPS-pattern entities in the
  answer appear in cited chunks. Catches made-up acronyms.

**Answer completeness** = mean of two sub-signals:
- `req_id_coverage` — `|cited_ids ∩ expected_req_ids| / |expected_req_ids|`.
  Likely already partly computed under a different name in
  `core/src/eval/metrics.py` — confirm before duplicating.
- `keyword_coverage` — `|answer_tokens ∩ expected_keywords| / |expected_keywords|`.
  Requires `expected_keywords` on the EvalQuestion schema (likely
  doesn't exist yet — open question 2).

### Open questions to resolve before coding

1. **Weighting of faithfulness sub-signals** — equal mean, or weighted
   toward `citation_grounding` (phantom req_id is a more serious
   hallucination than a wrong number)?
2. **Does `EvalQuestion` schema already carry `expected_keywords`?**
   Check `core/src/eval/questions.py`. If absent, requires curating 5–10
   keywords per question × 18 questions before any of this is testable.
3. **Storage shape**: separate new `<env_dir>/eval/kpi_history.sqlite`
   (clean audit trail, separate from team_pilot's
   `nora_test_feedback.db`) OR extend `feedback_db.py` (one less file,
   but couples two strands). Leaning separate file — different audit
   purposes (automated vs. human).

### Implementation surface

- `core/src/eval/metrics.py` — add `score_faithfulness(question,
  response)` and `score_answer_completeness(question, response)`,
  returning 0–1 floats with a per-sub-signal breakdown dict for
  diagnostics.
- `core/src/eval/runner.py` — include the new metrics in `EvalReport`'s
  per-question + aggregate output.
- New `core/src/eval/kpi_history.py` — SQLite persistence layer.
  Two-table schema discussed in the session: `kpi_runs` (run_id +
  timestamp + commit_sha + config_snapshot + question_count + llm_model)
  and `kpi_scores` (run_id + question_id + metric + value, PK on the
  triple). Mirrors the team_pilot pattern.
- `core/src/eval/eval_cli.py` — `--write-kpi-history` flag to opt in.
- Tests: deterministic by construction — pin expected scores on
  hand-crafted (answer, ground-truth) pairs.

### Two queries the storage layer should make trivial

- Trend per metric across commits: `SELECT commit_sha, AVG(value) FROM
  kpi_scores JOIN kpi_runs USING(run_id) WHERE metric=? GROUP BY
  commit_sha ORDER BY timestamp`.
- Regression hunt between two runs: per-question delta with `JOIN
  USING (question_id, metric)` filtered to the two run_ids.

Reference: this conversation on 2026-06-01.

## 2026-06-08 — Rerank infra expansion + TEI integration end-to-end

This is a catch-up close-session — work accumulated across multiple
sessions since the 2026-05-28 build-phase entry. Two waves:

### Done this session

**Wave 1 — Rerank provider expansion (commits 16088d3, ee8b477, 66fdf79, 03bd401)**

- `sandbox/sira_incremental.py retry-failed` subcommand
  (`--stage doc-enrich|rerank|both`, `--include-all-filtered`) — evict
  `trace.failed` entries to re-process the failing subset without a
  full re-run.
- Two new reranker providers in `core/src/query/reranker.py`:
  - `OpenAIRerankChat` — vLLM-served chat-completion scoring; works
    against any OpenAI-compatible chat endpoint.
  - `OpenAIRerankDedicated` — vLLM's `/v1/rerank` route, available
    when vLLM is started with `--task=reranker`.
  - Dispatch wired in `core/src/web/routes/query.py`.
- Unified batch-size knob: `NORA_RERANK_BATCH_SIZE` env var with the
  4-tier precedence standard for LLM config (env > config-page DB >
  config/llm.json > built-in default). Deprecated
  `NORA_SIRA_RERANK_BATCH_SIZE` accepted as alias with warning.
- SSE-streamed per-lane progress on the merged /test tab — form's
  `hx-post` replaced with a JS submit handler using `fetch` +
  `ReadableStream` (EventSource is GET-only; SSE-over-POST keeps it
  one request). New `#progress-display` card with per-lane rows.

**Wave 2 — TEI reranker provider, fifth backend (commits f74b891, a4b0e7b, 65d63ad, 8fa9309)**

- New `TEIReranker` class — HuggingFace TEI's Cohere-shape `/rerank`
  endpoint (body: `{query, texts}`; response: flat `[{index, score}]`).
  Mirrors graceful-passthrough contract of the rest of the family.
- Three follow-up operational fixes from first end-to-end test on
  dgx-spark-srv:
  1. `truncate=True` on the payload — TEI returns 422 (not silent
     truncation) when chunks exceed model max sequence length (512
     for bge-reranker-large). Char-level `_truncate` retained as
     wire-size safety net.
  2. Client-side auto-batching — TEI's `--max-client-batch-size`
     defaults to 32; pipeline hands reranker 50+ chunks. Split into
     ≤32-chunk batches, score each, merge globally by score
     (cross-encoder scores are batch-independent → global sort
     correct).
  3. HTTPError body capture in error logs — `urllib.error.HTTPError`
     into its own except branch, read `exc.read()` into the log.
     Bare `<HTTPError 422>` was uninformative; now the server's
     actual validation message surfaces.
- Per-batch wall-clock instrumentation in `TEIReranker.rerank()` —
  rerank start/done with chunk + batch counts, per-batch wall-clock +
  ok/FAILED status. Designed to cross-reference against TEI's access
  log to localize any latency bug.
- Config schema: `reranker_provider` accepts `"tei"` at all three
  guard sites in `core/src/env/config.py`; `core/src/web/config_schema.py`
  adds `"tei"` to dropdown + help text mentions Cohere-shape.

### Operational findings worth journaling

- **Caddy `handle_path` strip semantics for TEI deployment.** When
  Caddy is configured `handle_path /rerank/* { reverse_proxy
  tei-reranker:80 }`, it strips the matched prefix before forwarding.
  Since TEIReranker appends `/rerank` to base_url, the route only
  matches if base_url itself ends with `/rerank` — request becomes
  `/rerank/rerank`, Caddy strips one, TEI sees `/rerank`. Setting
  base_url to server root (without `/rerank`) → 308 redirect to HTTPS
  → silent passthrough. Captured in TEIReranker docstring; logged
  here because the original instructions had it wrong and required a
  cross-chat correction from the dgx-spark-srv side to catch.

- **TEI on CPU/arm64 ORT observed at ~150s for 50 chunks vs RUNBOOK
  baseline of 300-900ms per 32-chunk batch** — ~100× over expected.
  Two hypotheses surfaced (dgx-spark chat): inside-TEI contention
  with vLLM during synthesis, or transport-side overhead between
  NORA host and spark. Resolution pending the new instrumentation's
  first slow-query logs.

- **Cross-chat coordination pattern.** dgx-spark-srv changes and
  NORA-side changes were driven by two separate Claude sessions, with
  the user shuttling responses via `~/work/scan/reranker-instructions-*.txt`
  files. Worked well — the dgx-spark chat caught two factual errors
  in NORA-side recommendations that this chat couldn't have
  identified from its side (Caddy strip semantics + TEI RUNBOOK
  baseline numbers).

### In progress

- **TEI rerank latency investigation.** Awaiting next slow-query's
  logs from both sides (TEI access log via `docker compose logs
  tei-reranker`; NORA per-batch instrumentation just landed).
  Three-way diagnosis table: server-side / transport-side /
  NORA-side overhead.

### Next

- Run a slow query with both logs captured; diagnose the 100× gap.
- A/B verify TEI rerank actually reorders top-K (vs
  `NORA_RERANKER_ENABLED=false`) once latency is workable.
- Eval KPI work (2026-06-01 parked TODO) — three open questions to
  resolve before code (faithfulness weighting; `expected_keywords`
  schema; storage location).

### Flags

- Latency investigation is the active gate on declaring TEI
  integration fully ready for pilot use. Operational but too slow
  for interactive queries at the observed rate.
- 2026-06-01 eval-KPI TODO remains parked.
