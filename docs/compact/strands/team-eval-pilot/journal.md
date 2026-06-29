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

## 2026-06-12 — TODO pointer: SIRA dedicated /rerank backend (parked)

### Context

Mid-session pivot on SIRA's LLM-routing architecture. We landed
per-stage env-var routing (commit `004e86b`) so SIRA can talk directly
to OpenAI-compat LLM endpoints without `openai_shim`. While walking
through which backends `NORA_SIRA_RERANK_LLM_URL` accepts, the question
came up: can SIRA use TEI's `/rerank` (or vLLM's `/v1/rerank`) instead
of the current LLM-as-judge chat-completion path? Answer: not today —
SIRA's `llm_reranking.py` hardcodes the chat-completions shape with one
LLM call per (query, doc) pair, prompted via `relevance_requirement_v01.txt`
to emit `{"score": <0-100>}`.

### Why this matters for the eval-pilot strand

LLM-as-judge per-pair latency dominates SIRA rerank wall-clock: ~1-5s
per call × 50-200 candidates per query = 1-15 min/query just on rerank.
At the 18-Q × N-iteration eval cadence the team-eval-pilot strand is
building toward, that's a real bottleneck. A bulk cross-encoder rerank
(TEI / vLLM) is 10-100× faster — single HTTP call per query, bulk-scored.

### Where the TODO lives

Full design + four open questions parked at
[`sandbox/sira_patches/README.md`](../../../../sandbox/sira_patches/README.md)
under the **TODO — Future patches** section. Headlines:

1. New env var `NORA_SIRA_RERANK_BACKEND={chat,tei,openai-dedicated}`,
   default `chat` for backwards compatibility.
2. Per-backend code branch in SIRA's `llm_reranking.py` patch file.
3. **Score-scale alignment** — SIRA's 0-100 normative rubric vs.
   cross-encoder raw similarity scores; `NORA_SIRA_PIN_*` thresholds
   are tuned to 0-100 today.
4. **Trigger to land**: when eval-pilot ground-truth has enough density
   to retune `NORA_SIRA_PIN_*` against the new score distribution AND
   rerank latency is the dominant cost.

### Cross-link

This entry is just a pointer — the patches README is the source of
truth. Maintained there because the design is patch-adjacent and the
team-eval-pilot strand's data is the trigger, not the work itself.

## 2026-06-12 — Close session: SIRA per-stage routing + TEI embedding + ops tuning

Two distinct waves landed today plus the morning's TODO pointer:

### Done this session

**TEI embedding integration (commits 8cb8e91, 96ad0c7).**

- `probe_tei` stdlib diagnostic — `python -m core.src.vectorstore.probe_tei
  --embed-base ... --rerank-base ...` POSTs to candidate TEI endpoints and
  reports compact `SPK` lines for OpenAI-compat embedding, TEI native
  embedding, and TEI Cohere-style rerank. Confirms wire shape + dimension
  before NORA wires the provider in.
- `TEIEmbedder` provider in `core/src/vectorstore/embedding_tei.py` —
  fifth member of the EmbeddingProvider family. Probe results drove the
  decision to use TEI's native `/embed` shape over OpenAI-compat: both
  worked on the operator's deployment but native ran ~8× faster (6ms vs
  51ms for dim=1024). Mirrors `TEIReranker` from the prior session:
  client-side auto-batching at `max_batch_size=32` (matches TEI's
  `--max-client-batch-size` default), `truncate=True` server-side,
  reachability check at init via one small embed call.
- Env var resolvers added: `NORA_EMBEDDING_BASE_URL`,
  `NORA_EMBEDDING_API_KEY`, symmetric with the rerank-side `NORA_RERANKER_*`
  pattern landed earlier. 21 new tests, 125 + 7 skipped pass across
  regression set.

**SIRA pipeline operational tuning (commits b3ebf80, 0cca2b2).**

- Enrich + rerank concurrency 1 → 4 in `sandbox/sira_configs/{enrich,rerank}/nora.yaml`.
  The strict-serial default was set for a corporate-proxy environment that
  no longer applies; bumped after diagnosing why `++enrich.concurrency=N`
  on the CLI was silently re-stomped (dataset YAML loads AFTER Hydra
  config resolution — last writer wins). Comment block in each YAML now
  carries that footgun warning explicitly so the next person to tune
  concurrency finds it documented at the source.
- English-only directives in all three SIRA prompts
  (`sandbox/prompts/{doc,query,relevance}_requirement_v01.txt`). Internal
  OpenAI-compat LLM was returning non-English keywords, breaking the JSON
  parser (which expects English keys `keywords` / `score`). Two-position
  directives: `LANGUAGE:` block at top (first instruction the model
  reads) + `ENGLISH ONLY:` in the existing Rules block (recency anchor
  just before generation).

**SIRA per-stage LLM routing — the big shift (commits 004e86b, bebd7c0, be94b84, 9f9e916, ce65cf3, 2aed460).**

- New `sandbox/sira_patches/` directory: `per-stage-routing.patch`
  (unified diff against the gitignored SIRA clone), `README.md` (env-var
  reference + usage examples), `test/probe_per_stage_endpoints.py`
  (stdlib SPK-format probe per stage).
- Six env vars exposed across SIRA's four LLM-calling scripts:
  `NORA_SIRA_{ENRICH,RERANK}_LLM_{URL,MODEL,TIMEOUT}`. When unset,
  behavior is identical to pre-patch (sglang config from Hydra). When
  set, that stage routes to the configured endpoint with the configured
  model and timeout — and `run_pipeline.py`'s localhost-sglang
  reachability check + fallback spawn skip entirely.
- `sandbox/install_configs.sh` now applies the patch idempotently —
  sentinel-grep on `NORA_SIRA_ENRICH_LLM_URL` in the patched file detects
  already-applied state and skips silently; otherwise `git -C $SIRA_CLONE
  apply` runs cleanly with bail-out + reset instructions if the SIRA
  clone was hand-edited.
- Timeout knobs added as patch extension (sets both aiohttp `total` and
  `sock_read` to the same value). Diagnosed via the operator running an
  internal LLM ~1min per call against SIRA's hardcoded `sock_read=60s`
  cut-off. Logged at runtime as `LLM timeout: total=Xs sock_read=Xs`.
- Documentation in lockstep: `sandbox/README.md` Layout table updated
  with `sira_patches/` rows + shim demoted to "optional fallback for
  header-injection / model-rewrite / non-OpenAI adapter mode";
  `sandbox/SETUP.md` got new section 5.0 "Per-stage routing —
  recommended" preceding the existing 5a (pass-through) and 5b (adapter
  mode) shim sections, plus D.1/D.2 split in the full-pipeline section,
  plus a fresh troubleshooting subsection covering the new failure
  modes (patch not applied, probe failures, skip-spawn guard not
  tripping, install_configs.sh sentinel-skip-on-old-patch trap).

### In progress

- Operator pulling the extended patch + timeout knobs on the work PC.
  Critical step they need: `cd sandbox/sira && git checkout -- scripts/`
  BEFORE re-running `install_configs.sh`, because the sentinel grep
  would otherwise find the old patch's marker and skip applying the
  extended version.
- Eval-pilot ground-truth collection (the strand's core mission) —
  unchanged from prior sessions; the routing/timeout/concurrency work
  today is infrastructure to make those eval passes complete in tractable
  wall-clock time, not pilot work directly.

### Next

- Confirm timeout env vars take effect on the operator's work PC and the
  internal LLM no longer cuts off mid-stream. Look for runtime log
  `LLM timeout: total=600s sock_read=600s` (or whatever value is set).
- If the proprietary LLM is now responsive within the configured
  timeout, run the full pipeline end-to-end (corpus enrichment + query
  enrichment + LLM rerank) against the unthrottled shim path with
  concurrency=4 + timeout-tuned values. Compare eval recall against
  the A4 baseline.
- Continue eval-pilot feedback collection through the merged /test tab
  (the strand's actual mission — independent of today's infrastructure
  work).

### Flags

- Two earlier parked TODOs unchanged: 2026-06-01 eval-KPI tracking,
  2026-06-12 SIRA dedicated `/rerank` backend (the one pointed at this
  morning).
- `openai_shim` not yet retired — still load-bearing for any deployment
  that needs header injection or model-name rewriting. The path is now
  optional rather than required for the per-stage-routing-compatible
  setups.
- The extended-patch upgrade path on existing pulls requires the SIRA
  clone reset (`git checkout -- scripts/`) before `install_configs.sh`
  re-applies. Documented in SETUP.md troubleshooting. Worth flagging
  again if anyone else pulls and hits the skip-on-sentinel surprise.

## 2026-06-29 — Team-mode gate (SIRA-only for team, full app for admin) + test-page accuracy

### Done this session
- **Team-mode access gate** (`5af9c2b`): `NORA_WEB_TEAM_MODE` + `TeamModeMiddleware`
  (new `core/src/web/team_mode.py`). Team members are restricted to `/test`
  (+`/api/test`, `/static`, health); every other path → redirect to `/test`. The
  test page is **SIRA-locked**: NORA checkbox disabled+unchecked, SIRA
  checked+disabled, a hidden `lanes=sira` input, AND the ask handlers force
  `lanes=["sira"]` server-side (defense in depth — a crafted POST can't run NORA).
  The admin unlocks the full app for their browser via
  `/admin-unlock?token=<NORA_WEB_ADMIN_TOKEN>` → HttpOnly cookie. No-op when the
  flag is off. → D-DRAFT-18.
- **Hidden-input fix** (`f142c11`): disabled checkboxes aren't submitted in
  FormData, so the locked SIRA lane is carried by a hidden input (passes the
  client-side "≥1 lane" check + the POST).
- **Test-page accuracy after the select-synth rename** (`13cf588`): the SIRA
  caption gated "no rerank" on the stale `sira_synth_mode == 'llm-select'` (value
  is now `select-synth`) → was wrongly showing "N reranked · pinned to synth" +
  the old "Path-B" badge; accept `select-synth`, rename badge. `_filter_sira_notes`
  suppresses the expected "rerank disabled" ⚠ in select-synth (rerank-off is by
  design there, not a fault). Conditional `/test` description (`9f6e64b`): team
  sees a SIRA-only blurb, admin sees the "selected lane(s)" version.
- **Authoritative-lanes progress** (`df61234`): the SSE stream emits a `lanes`
  event first with the lanes the server actually runs; the client renders the
  progress rows from that, not the submitted form.
- **`Cache-Control: no-cache` on `/test`** (`38e8940`): the progress logic ships
  inline in the page, so a cached copy served stale JS until a hard-refresh;
  no-cache makes the browser revalidate every load. Test asserts the header.

### In progress
- Nothing active — team eval for 2 MNOs is operational and the gate is live.

### Next
- (Cross-strand) MNO-C onboarding moves to a new `mno-c-ingestion` strand, then
  `multi-mno-sira` for the 3-way verification. Not this strand's work.
- Eventually: land team-eval-pilot once the eval round wraps (it carries 18 drafts).

### Flags
- **Cosmetic: NORA progress row still spins in team view.** The authoritative-
  lanes fix + no-cache header are in place, but the user still sees the NORA
  spinner. **NORA does NOT actually run** — the server force-locks `lanes=["sira"]`
  — so it's a misleading UI element, not execution. Most likely the client is
  still receiving stale JS via a layer above the browser cache (reverse proxy
  caching `/test`, or the web process serving a cached template). Parked as
  non-critical; revisit by confirming the served page contains the `lanes`-event
  handler (`parsed.event === "lanes"`) and, if not, purging the proxy / verifying
  the web restarted on current code.
- Strand still carries the `nora_test_feedback.db` schema overlaps (citations_json
  vs cited_ids etc.) noted in earlier drafts — reconcile at land.
