# eval

**Purpose**
Evaluation framework for the query pipeline. Runs a labeled question set through the full `QueryPipeline`, scores each response against ground truth, and — critically — supports A/B comparison between `graph_scoped` (the D-001 default) and `pure_rag` (graph scoping bypassed, metadata-only RAG) so the KG+RAG hybrid's value can be measured, not assumed. Serves FR-21 (eval framework with 5 metrics + A/B); covers NFR-15 (≥ 90% weighted-overall accuracy bar per D-015), NFR-16 (acceptance measured on user-curated Q&A only).
Also owns the **golden eval set** (strand golden-eval; serves FR-38): expert-curated samples (target 50 → 200) scoring the SIRA-only serving lane across releases. Two stages — Stage 1: retrieval recall (% of ground-truth req_ids present in the stack's retrieved results); Stage 2: LLM-judged similarity between a response regenerated from Stage-1 retrieved chunks (via the production synthesis path) and the expert's golden response. Sample authoring/curation lives in [web](../web/MODULE.md)'s Eval Studio; this module owns the schema, the runners, and the judge.

**Public surface**
- Runner: `EvalRunner(graph, embedder, store, ...)` (runner.py) — `run_all(questions)` returns `EvalReport`; `run_ab_comparison(questions)` returns `ABComparison`
- `ABComparison` — holds two `EvalReport`s; exposes `graph_wins`, `rag_wins`, `ties`, `graph_avg_overall`, `rag_avg_overall`, `to_dict()`
- Questions: `EvalQuestion`, `GroundTruth`, `ALL_QUESTIONS` (questions.py) — curated Q&A fixture + loader for user-supplied Excel eval sets
- Metrics: `QuestionScore`, `EvalReport`, `score_question(question, response)` (metrics.py) — per-question scoring + aggregate report
- CLI: `eval_cli.main`
- Golden eval (strand golden-eval; operator runbook: [docs/golden-eval-guide.md](../../../docs/golden-eval-guide.md)):
  - Schema (golden.py): `GoldenSample` — sample_id, created_by/created_at/updated_at, area (use-case tag), `mno` (carrier tag, strand eval-studio-ux-2 [D-206] — authoring metadata for grouping/filtered runs; NOT used by recall matching, which keys on per-entry qualifiers, and deliberately outside the eval-set digests so tag edits don't invalidate run comparability), query, `kind` (`standard` default | `version` — release-handling design, strand golden-eval: scoring-relevant, so inside the eval-set digest; a standard sample is a question about current behaviour and matches WITHOUT the release, a version sample's entries carry an absolute `MMMYYYY` or relative `N` / `N-<k>` release — relative needs the entry's `mno`, is version-only, and is not mixed with absolute in one sample; grammar helpers `is_relative_release` / `relative_offset`; `names_release(text)` — the month + year shape the query analyzer reads as a named release, used by migrate's "probably version samples" list and Eval Studio's QC warning), `ground_truth: list[GroundTruthEntry]` (req_id + optional `(mno, release, plan)` qualifiers — picker-sourced entries are always fully qualified, direct-entry may be bare; on a standard sample the release is provenance only), golden_response (None until curated), golden_meta (curated_at, chat_turns, model, `edited` — manual-edit provenance flag, eval-studio-ux-2; `review` — per-MNO flag written by a release move, `needs_review(sample)`; the runner skips Stage 2 for a flagged sample with GEV-W002 and leaves it out of the Stage-2 digest), status (`draft | stage1-ready | golden-ready`); `load_samples(env_dir)`, `save_sample(env_dir, sample)` — one JSON file per sample under `<env_dir>/eval/golden/samples/<sample_id>.json`
  - Runner (golden_runner.py): `GoldenRunner(stack_url, pipeline, judge_llm)` — `run_stage1(sample)` (POST `/sira-query` → per-sample recall + per-hit ranks, so recall@5/@10 derive from one run), `scoring_entries(sample, release_map)` (binds entries for a run: version entries' relative releases resolved — GEV-E005 without a map or out of range, raised before any stack call; standard entries bound to their MNO's `N` for the scope check, or `""` without a map), match rule **m3** (`MATCH_RULE`): standard samples match on `(req_id, mno)`, version samples on `(req_id, mno, resolved release)`, with `req_id` compared case-insensitively (m3 over m2 — field-found: an id typed in another case zeroed its sample since authoring; no two ids in the corpus differ only by case) — `match_ground_truth(..., ignore_release)`, `classify_scope(ground_truth, misses, resolved_cells)` (field-found: a miss because the stack never searched the entry's cell looked like a retrieval miss — compares the cells the entries name with the stack's `resolved_cells`, tags each miss `miss_kind` `scope` / `retrieval`, and returns the sample's scope `ok | extra | miss | miss+extra | n/a` plus the missing and extra cell lists, carried on `Stage1Result`; diagnosis only — recall is unchanged and nothing enters the comparability keys; the GEV block gains a `scope:` count line, omitted when no sample has scope data so cell-less stacks' blocks stay byte-identical), `run_stage2(sample, stage1_result)` (pin Stage-1 retrieved req_ids into `QueryPipeline` via `pinned_chunk_ids` → judge candidate vs golden_response), `run_all(samples) -> GoldenRunReport`; run artifacts under `<env_dir>/eval/golden/runs/<run_id>/`. `SiraRowsPipeline` (strand golden-eval, field-found) — duck-typed Stage-2 pipeline for the SIRA-only lane, which never builds `out/graph`/`out/vectorstore`: synthesizes with the production `LLMSynthesizer` prompt over the rows Stage-1 actually retrieved (full text via the service's `text_chars` knob; rows retained TRANSIENTLY on `Stage1Result.retrieved_rows`, never serialized), empty-graph context enrichment (the established RAG-only degradation); the CLI auto-selects it when the vectorstore dir is absent. A failed synthesis (`QueryResponse.synthesis_error`, which includes empty LLM content) is never judged: `run_stage2` raises GEV-E007 for the sample (field-found — during a refusal-fallback outage the error stub was judged and scored 0; with a thinking fallback an empty answer plus the provenance epilogue was judged 10). `Stage2Result.synth_rerouted` / `judge_rerouted` record whether the answer or the verdict came from the refusal fallback — eval-side use the stack's `fb_*` counter cannot see — and the GEV block adds `fallback: synth=K judge=J` when either is non-zero. Rows are identified by `row_key(row)` = `req_id@<mno>__<release>` (F10, release-handling design): Stage 1 records `retrieved_row_keys` (rank order, deduped by id + cell) beside the id-only `retrieved_req_ids`, and the sira-rows Stage 2 pins and binds rows by that key, so a requirement retrieved from two releases reaches synthesis in both versions (the context builder labels each with its MNO and release); a bare `req:<id>` pin selects every bound row of that id. Changes Stage-2 input only for questions that retrieve one id from more than one cell — none under latest-only scoping of release-less questions. `GoldenRunReport.stage2_mode` (`"" | "pipeline" | "sira-rows"`) + `stage2_skip_reason` make a Stage-2 that never ran distinguishable in report.json from one that ran and scored nothing (field-found silent-skip defect); the GEV block prints `s2: SKIPPED (<reason>)`. `StackStamp` (D-DRAFT-14, strand golden-eval) — runtime-captured identity of the stack a run scored against (serve label, data fingerprint, code version, SIRA prompt scheme, answer-prompt version, LLM identity, reasoning effort, retrieval knobs, fallback counter snapshot), built via `StackStamp.from_healthz(healthz, ...)` with caller-supplied fields winning; comparability is code — `stage1_key()` (data + code + knobs) / `stage2_key()` (+ prompt + LLM, judge version compared alongside); empty field = "not comparable on this axis", never a guess; `compact_line()` renders the redaction-safe GEV `id:` line. Release handling: `stage1_key()` also carries `match_rule` (bumped on any matching-rule change — stored samples can be byte-identical across one); the eval-set digests cover `(id, query, kind, digest_view(sample, map))` — standard entries with the release blanked (a provenance re-tag or a move of `N` keeps comparability), version entries with the resolved release (a move of `N` that re-binds them breaks it); `release_map_version` / `release_map_mismatch` are stamped for attribution (`match=m3 rmap=v<N>` on the `id:` line, `!` when scored despite a disagreement), not keyed. `run_all(..., release_map, allow_map_mismatch)` checks the map's `N` against the stack's latest served release per MNO the samples use (`check_map_against_stack`, from healthz `cells`) BEFORE scoring and raises GEV-E006 on a disagreement unless allowed. Stamped on every `GoldenRunReport`; serving-side healthz adoption of the canonical keys (`data_fingerprint`, `code_version`, `serve_label`, `sira_prompt_scheme`) is incremental follow-up work
  - Release map (release_map.py, release-handling design): `ReleaseMap(version, mnos, updated_at, updated_by)` — per MNO the releases oldest first; `current(mno)` = `N`, `resolve(mno, ref)` binds `N-<k>` (GEV-E005 out of range), `validate()`, `digest()`; `load_release_map(golden_dir, version=None)` (current file, or a pinned version from the history; None when the set has no map yet) and `save_release_map(golden_dir, rmap)` (atomic current + append-only history; refuses an invalid or non-advancing version). Files `<golden>/release-map.json` + `release-map.history.jsonl` — golden-set data beside the samples (NFR-8). Explicit, never derived from a stack: all stacks share one set and flip at different times, and a stack stuck on an old label reports that label as its own latest
  - CLI: `golden_release_cli.main` — release map maintenance: `show`, `migrate` (write v1 for an existing set; `--n MNO=REL` overrides), `advance --mno --release` (after ingestion + flip), `review` (list / `--clear` flags); `migrate`/`advance` are dry runs without `--yes`. The **text check** (`text_check` → `apply_check`): every standard entry of the MNO whose stored release is older than the new `N` is compared between the two releases after NFKC normalisation, removal of zero-width characters and byte-order marks, and whitespace collapse (field-found: a stray byte-order mark compared "changed") — unchanged / title_only (same body, different title; carried over like unchanged, not flagged) / operator_name (differs only by the entry's own operator name in a form `query.analyzer.mno_surface_forms` lists; carried over, not flagged — narrow, no similarity threshold) / changed / absent / unverified; ids are looked up by the parse tree's casing, and a stored id that differs only by case is reported as `case` and rewritten to the tree form on `--yes` (entries already at `N` included); unchanged and title_only entries have their stored release moved forward (on a standard sample the stored release is provenance only); any non-unchanged entry, and every relative version sample on an advance, flags the sample in `golden_meta["review"][mno]`. Reads parse trees through its own per-cell reader (`parse_cells`, `cell_texts`) — web's `req_tree` is not importable from eval (web depends on eval). Output names ids: local only; the counts line is chat-pasteable
  - Judge prompt: `prompts/judge_v<N>.txt` — versioned committed artifact (generic wording; sees proprietary content only at runtime); version recorded in every run report
  - CLI: `golden_report_cli.main` — run-dir inspector: per-sample expected (ground-truth) vs retrieved req_ids side by side from a run's `report.json` (`--misses` filters to samples with missed entries; `--samples-dir` adds query text, defaulting to the run dir's sibling `samples/`). Read-only, stdlib-only (copyable to a field machine and runnable directly); output is run-dir material — proprietary, local inspection only, never chat-pasteable (NFR-8)
  - CLI: `golden_cli.main` — batch entrypoint; one `--stack-url` per run (a release A/B is two runs). Flags: `--env-dir` (required), `--stack-url` (required), `--stack-label`, `--stage {1,all}`, `--top-k`, `--label` (overlay label view), `--timeout`, `--graph`, `--vectorstore-dir`, `--llm-model`, `--reasoning`, `--judge-model`, `--judge-prompt-version`, `--env-name`, `--mno`, `--answer-prompt-version`, `--sira-prompt-scheme`, `--release-map-version`, `--allow-map-mismatch`. Runnable standalone as a build step or as a final pipeline stage. `--mno` filters the run to samples tagged with that carrier (filter applies BEFORE the stamp freezes the eval set, so filtered runs key apart from full runs). `--release-map-version K` scores with a pinned map version (a stack that has not flipped; re-scoring an old run); `--allow-map-mismatch` scores past a GEV-E006 disagreement and stamps it; a GEV-E006 refusal or an unloadable map exits 2 with nothing scored. `--reasoning {none,low,medium,high}` sets the reasoning effort for **Stage-2 synthesis only** (openai-compatible endpoints; the judge keeps the endpoint default, so scoring behaviour stays fixed while generation varies); the default `""` sends no reasoning field at all
  - Other GEV codes: GEV-E003 — Stage 2 has nothing to synthesize from (no vector store config or an empty store, so no NORA index to score against; no row text bound in the sira-rows mode; or the pinned synthesis produced no answer); GEV-E004 — judge prompt missing, or the judge call failed or its verdict was unparseable after the strict-format retry; GEV-W001 — sample skipped (status draft, no ground truth, or no golden_response for Stage 2)
- Query-classification harness (strand jev-experimentation, phase 1; design `docs/compact/strands/jev-experimentation/decision-model-design.md` §6):
  - Schema + set (classify.py): `ClassifySample` — sample_id, query, expected `query_type`, expected `doc_type_scope`; `load_classify_set(path=None)` loads `DEFAULT_SET_PATH` (`core/src/eval/classify_set.json`, 50 synthetic samples, 5 per `QueryType`). Query text carries MNO codes as the placeholders in `MNO_PLACEHOLDERS` (`{MNO_A}` / `{MNO_B}` / `{MNO_C}`); the loader substitutes them from `query.analyzer._MNO_ALIASES`, pairing each placeholder with the alias table's distinct codes in first-appearance order, so the committed file names no MNO. Reordering the alias table re-pairs the placeholders. Release labels in the set are fictitious future months. Labels are human-read intent, never rule output — a set derived from the rules scores them at 100% and measures nothing.
  - Divergence weighting (classify.py): `KNOB_NAMES`, `KNOB_PROFILE`, `divergence_cost(expected, predicted)` — the fraction of the five per-type retrieval knobs (top_k bucket, BM25 weight, rewrite, max distance, grouping-disable) that differ between two classes, so a confusion is scored by downstream damage rather than label mismatch. Derived at import from `env.config._BROAD_QUERY_TYPES` / `DEFAULT_BM25_WEIGHT_BY_TYPE` and `query.pipeline._TYPE_*`, never copied. Rerank is excluded because `_TYPE_RERANK_ENABLED` is empty by design and a constant column adds 0.0 to every distance; a test fails if an entry appears. `profile_digest()` stamps every report, so reports scored against different tuning are not pooled.
  - Runner + report (classify.py): `run_classify(analyzer, samples, repeats=1, arm="", set_id="")` drives any `analyze(query) -> QueryIntent` object; `ClassifyResult`, `ClassifyReport` — `accuracy`, `weighted_cost`, `scope_accuracy`, `deterministic`, `latency_p50_ms`, `latency_p95_ms`, `confusion()` (all ten classes, present whether or not they occur), `per_class()`, `matrix_text()`, `to_dict()`. With `repeats > 1` the FIRST run is the one scored and the rest only probe determinism, so the score does not move with the repeat count.
  - CLI: `classify_cli.main` — `--arm {rules,llm,decision,confidence-routed}` (default `rules`), `--set PATH` (labelled set JSON, default `DEFAULT_SET_PATH`), `--repeat N` (default 1; >1 probes determinism), `--out PATH` (full report JSON); `llm` arm: `--llm-provider`, `--model`, `--base-url`, `--api-key`, `--timeout`; `decision` / `confidence-routed` arms: `--decision-provider` (`mock` | `systemone`; this CLI falls back to `mock` when neither the flag nor `NORA_DECISION_PROVIDER` names one — the one exception to the served-path rule "mock only when named", because this is an offline harness: the mock skips the head check and claims no result), `--decision-model`, `--decision-base-url`, `--decision-api-key`, `--weights-dir`, `--confidence-floor F` (`confidence-routed` only, default 0.50 / `NORA_DECISION_CONFIDENCE_FLOOR`); `--verbose` / `-v`. Bad values for the arm knobs raise through the `env.config` resolvers rather than degrading, so a typo is never measured as the rules. Builds its LLM provider through the `resolve_llm_*` chain directly rather than `PipelineContext.create_llm_provider`, which requires a `documents_dir` this corpus-free harness has no use for. The `decision` arm is gated three ways before a number is recorded: `assert_remote_head` on the server (the authority — it asks `/v1/models` for a loaded adapter and a calibration temperature, over the same connection the decisions travel, and raises on an unreachable host), `assert_decision_head_present` on `--weights-dir` when given (optional, since the serving host is usually not the eval client), and a non-zero `fallback_count` after the run, which exits 2. The third exists because per-query fallback is correct *serving* behaviour that makes a dead provider look like a successful run reporting the rule-based numbers; the first exists because a passing local file check is what let that happen once already (D-DRAFT-20, D-DRAFT-22). Exit 2 on `DecisionError`, matching `golden_cli`.
  - `confidence-routed` takes the same provider and the same three gates as `decision` — routing changes which answers are used, not whether the server is serving a head — and adds `--confidence-floor` (default 0.50, `NORA_DECISION_CONFIDENCE_FLOOR`). It prints a routing block and writes it under a `routing` key in `--out`: the floor, and per-field coverage, meaning the fraction of that field's answers that came from the model rather than the rules. **Coverage is what makes a routed accuracy readable** — an arm that routes 2 % of fields to the model and scores well is reporting the rules under the model's name, and the accuracy alone cannot show that. The block ends with the in-sample caveat, printed by construction rather than by anyone remembering to repeat it. The `routing` key is written by the CLI rather than added to `ClassifyReport`, because routing is one arm's provenance and the report type is shared by four.

**Invariants**
- Reasoning effort is recorded on the stamp and printed as `rsn=` on the GEV `id:` line, but is deliberately **NOT** a comparability key — two runs at different levels still pool. Restricting the comparison is the analyst's call, not the tool's; the printed line is what makes the difference visible. Tests assert both halves (`test_eval_golden_runner.py::TestStackStamp`).
- The A/B modes compare `graph_scoped` vs. `pure_rag` on the **same questions, same embedder, same store, same LLM** — only the scoping differs. Any variable beyond scope drift invalidates the comparison.
- `EvalReport.scores` is ordered to match `questions` — position `i` in both lists is the same question. `ABComparison` relies on this zipping.
- `score_question()` returns a `QuestionScore` with per-dimension sub-scores and an aggregated `overall`. Aggregation is deterministic; regressions trace to a specific sub-score.
- Eval never mutates the graph, vector store, or corrections. It reads; it reports.
- User-supplied questions from Excel (`<env_dir>/eval/*.xlsx` per D-022) are loaded through the same `EvalQuestion` schema as built-ins — no parallel code path.
- Golden samples and run reports live under `<env_dir>/eval/golden/` — proprietary content (queries, req_ids, golden responses). Never in the repo; chat-pasteable compact summaries carry counts/percentages only (NFR-8).
- Standard samples never use the release to match (m2, kept in m3); the release check is the scope check against the map's `N`. Version samples always match the resolved release. Any change to either rule bumps `MATCH_RULE`.
- Stage-1 scores the serving stack **black-box over HTTP** (`POST /sira-query`) — never an in-process reconstruction of retrieval. The metric measures what the stack actually serves; pointing the runner at different stack URLs is the release A/B.
- The judge-prompt version is recorded in every Stage-2 report. Judge scores are comparable only within the same judge version; a report never mixes versions.
- Stage-2's candidate response is generated through `QueryPipeline` with `pinned_chunk_ids` when a vector store exists, and through `SiraRowsPipeline` (the Stage-1 rows) otherwise — in both modes the same synthesis prompt the production web lane uses. Any drift between eval-side and web-side synthesis invalidates the judge score.
- The classification set is committed and synthetic: query text only, placeholder requirement IDs shaped to match `query.analyzer._REQ_ID_PATTERN`, MNO-code placeholders filled at load, fictitious release months, no real plan codes. The hygiene tests read the real codes out of `query.analyzer._PLAN_ALIASES` rather than restating them, so the test file carries none either. The corpus-derived set is `<env_dir>` material.
- `ClassifyReport.matrix_text()` carries class names and counts only, so it is chat-pasteable (NFR-8). `classify_cli --out` JSON carries query text and stays out of the repo.
- Classification accuracy and weighted cost are reported side by side and are allowed to disagree — that disagreement is the signal the weighting exists to expose, not an inconsistency to reconcile.

**Key choices**
- Bundled A/B path: the whole point of D-001 was the hybrid hypothesis. Keeping `pure_rag` runnable means we re-measure the hypothesis every time the pipeline changes, not only once.
- Per-question timing is captured so regressions in latency surface alongside regressions in correctness — remote chat-mediated debugging (D-012) needs both signals.
- Legacy 5-metric scoring is rule-based, not LLM-judged: `score_question` checks for expected entity mentions, citation coverage, and numeric hits. Avoids the judge-LLM cost and keeps eval runnable offline. **Amended by strand golden-eval (D-DRAFT-4):** golden Stage-2 adds an LLM judge — the judge runs on the local on-prem provider (injected `LLMProvider`, defaulting to the synthesis provider), so the offline/no-external-call property survives; the rule-based path stays for the legacy 5-metric questions.
- Golden samples are per-sample JSON files, not an Excel workbook (D-DRAFT-1): concurrent expert authoring without merge conflicts, diff-able sync between environments, save-time validation in the Eval Studio. The legacy `.xlsx` loader stays for the old path — the two schemas never mix.
- Golden artifact triple per NFR-9 (D-DRAFT-1): error-code prefix `GEV-` (registered in pipeline's `CODES` catalog), a GEV compact run report (RPT/MET style — sample counts by status, recall aggregates per stack, judge mean/median per stack, cross-stack delta; no proprietary content), and a fixed-field QC template (all ids resolve, qualifier coverage, ≥1 independently-sourced ground-truth entry per sample, single judge version per report).
- `EvalReport` is emitted as structured JSON plus a compact text summary — the JSON is for CI, the summary is chat-pasteable.

**Non-goals**
- Not a benchmark against external models or datasets — scoped to NORA's own questions and corpus.
- No training data curation tools. If the question set is too small, the fix is to add `.xlsx` pairs to `<env_dir>/eval/`, not to mutate code.
- No synthetic question generation — every question either ships with the repo or comes from a user's eval workbook / expert-curated golden sample.
- No authoring/curation UI in this module — sample CRUD, the ground-truth picker, and the Stage-2 curation chat are [web](../web/MODULE.md)'s Eval Studio. Eval owns the schema both sides share; web writes samples only through `golden.py` (no parallel schema).
- No inline regression alerts — CI / reporting is the caller's responsibility; eval just prints and exits.

<!-- BEGIN:STRUCTURE -->
_Alphabetical, regenerated by regen-map._


`classify.py`
- `ClassifyReport` — dataclass — pub — One arm's results over one labelled set.
  - `accuracy` — property — pub
  - `confusion` — method — pub — Expected (row) against predicted (column), all ten classes
  - `deterministic` — property — pub
  - `latency_p50_ms` — property — pub
  - `latency_p95_ms` — property — pub
  - `matrix_text` — method — pub — Fixed-width confusion matrix. Carries class names and counts
  - `per_class` — method — pub
  - `scope_accuracy` — property — pub
  - `to_dict` — method — pub
  - `weighted_cost` — property — pub — Mean divergence cost. Lower is better; 0.0 means every
- `ClassifyResult` — dataclass — pub — One sample's outcome under one arm.
  - `correct` — property — pub
  - `scope_correct` — property — pub
- `ClassifySample` — dataclass — pub — One labelled query. Labels are human-read intent, not rule output.
- `DEFAULT_SET_PATH` — constant — pub
- `KNOB_NAMES` — constant — pub
- `KNOB_PROFILE` — constant — pub
- `MNO_PLACEHOLDERS` — constant — pub
- `_fill_placeholders` — function — internal
- `_knob_row` — function — internal — The five knob values a QueryType selects, read from the live
- `_mno_codes` — function — internal — Distinct operator codes in alias-table first-appearance order.
- `divergence_cost` — function — pub — Downstream cost of predicting ``predicted`` when the truth is
- `load_classify_set` — function — pub — Load a labelled classification set from JSON.
- `profile_digest` — function — pub — Short digest of the knob profile.
- `run_classify` — function — pub — Run one analyzer over a labelled set.

`classify_cli.py`
- `_build_analyzer` — function — internal — Returns (analyzer, model_identity).
- `_build_decision_arm` — function — internal — Build the decision analyzer, refusing to proceed unless the server
- `_build_llm_provider` — function — internal — Construct the LLM provider for the ``llm`` arm.
- `_routing_summary` — function — internal — Coverage per field, plus the floor it was measured at.
- `_routing_text` — function — internal — The routing block for stdout, caveat included.
- `logger` — constant — pub
- `main` — function — pub

`eval_cli.py`
- `_check_target` — function — internal — Check if a metric meets its TDD target.
- `_create_runner` — function — internal — Create the evaluation runner.
- `_display_ab_comparison` — function — internal — Display A/B comparison results.
- `_display_report` — function — internal — Display an evaluation report.
- `logger` — constant — pub
- `main` — function — pub

`golden.py`
- `GoldenEvalError` — class — pub — Structured golden-eval failure carrying a stable GEV- code.
  - `__init__` — constructor — internal
- `GoldenSample` — dataclass — pub — An expert-curated eval sample (FR-38).
  - `from_dict` — classmethod — pub
  - `to_dict` — method — pub
- `GroundTruthEntry` — dataclass — pub — One expected requirement. Qualifiers are optional: picker-sourced
  - `from_dict` — classmethod — pub
  - `to_dict` — method — pub
- `KINDS` — constant — pub
- `KIND_STANDARD` — constant — pub
- `KIND_VERSION` — constant — pub
- `STATUSES` — constant — pub
- `STATUS_DRAFT` — constant — pub
- `STATUS_GOLDEN_READY` — constant — pub
- `STATUS_STAGE1_READY` — constant — pub
- `_NAMED_RELEASE_RE` — constant — internal
- `_RELATIVE_RE` — constant — internal
- `_SAMPLE_ID_RE` — constant — internal
- `golden_root` — function — pub
- `is_relative_release` — function — pub — True for ``N`` / ``N-<k>`` (k >= 1) — an MNO's current release and
- `load_sample` — function — pub — Load one sample file. Raises GoldenEvalError (GEV-E001) on
- `load_samples` — function — pub — Load every sample under <env_dir>/eval/golden/samples/, sorted by
- `logger` — constant — pub
- `names_release` — function — pub — True when ``text`` names a release by month and year.
- `needs_review` — function — pub — True when a release move flagged the sample for expert review
- `next_sample_id` — function — pub — Allocate the next sequential id (gs-0001, gs-0002, ...) from the
- `relative_offset` — function — pub — ``N`` -> 0, ``N-2`` -> 2. Raises ValueError for anything else.
- `runs_dir` — function — pub
- `sample_path` — function — pub
- `samples_dir` — function — pub
- `save_sample` — function — pub — Validate + write one sample atomically (tmp + rename). Stamps
- `validate_sample` — function — pub — Schema-level validation. Returns a list of problems (empty = valid).

`golden_cli.py`
- `_build_llm` — function — internal — Construct the LLM provider via the public env resolver chain
- `_build_query_pipeline` — function — internal — Mirror the web pipeline construction (per-cell stores, stub-graph
- `_run` — function — internal
- `logger` — constant — pub
- `main` — function — pub

`golden_release_cli.py`
- `CheckReport` — dataclass — pub
  - `counts` — method — pub
- `SampleCheck` — dataclass — pub
  - `flagged` — property — pub
- `_INVISIBLE` — constant — internal
- `_norm` — function — internal — Compatibility-normalise, drop invisible characters, collapse
- `_now` — function — internal
- `_parse_n` — function — internal
- `_strip_operator` — function — internal — ``text`` (already ``_norm``-ed) with every written form of ``mno``
- `_user` — function — internal
- `apply_check` — function — pub — Write the check's outcome into the samples: move unchanged (and
- `cell_texts` — function — pub — ``{req_id: (title, body)}`` for one cell, each normalised by
- `cmd_advance` — function — pub
- `cmd_migrate` — function — pub
- `cmd_review` — function — pub
- `cmd_show` — function — pub
- `main` — function — pub
- `parse_cells` — function — pub — ``{mno: [release, ...]}`` (oldest first) for every cell with at
- `print_check` — function — pub
- `text_check` — function — pub — Classify every entry of ``mno`` that moving its ``N`` to

`golden_report_cli.py`
- `format_sample` — function — pub — Render one stage1 entry as side-by-side lines.
- `load_queries` — function — pub — Map sample_id -> query text from per-sample JSON files. Missing or
- `main` — function — pub

`golden_runner.py`
- `GoldenRunReport` — dataclass — pub — One stack's run. Full detail goes to the run dir (proprietary);
  - `_judge_scores` — method — internal
  - `compact_report` — method — pub — The GEV compact block (strand golden-eval design note). No
  - `run_id` — property — pub
  - `to_dict` — method — pub
- `MATCH_RULE` — constant — pub
- `SiraRowsPipeline` — class — pub — Duck-typed Stage-2 pipeline for the SIRA-only lane.
  - `__init__` — constructor — internal
  - `bind_rows` — method — pub
  - `query` — method — pub
- `StackStamp` — dataclass — pub — Runtime-captured identity of the stack a run scored against.
  - `_knobs_key` — method — internal
  - `compact_line` — method — pub — One redaction-safe line for the GEV block — identifiers and
  - `from_healthz` — classmethod — pub — Build a stamp from a healthz snapshot + caller-known fields.
  - `set_eval_set` — method — pub — Freeze the scored sample set's identity into the stamp.
  - `stage1_key` — method — pub — Two runs' Stage-1 scores are comparable iff these match.
  - `stage2_key` — method — pub — Stage-2 comparability: Stage-1 axes plus the generation
  - `to_dict` — method — pub
- `Stage1Result` — dataclass — pub — Per-sample Stage-1 outcome. Full detail (req_ids, ranks) is run-dir
  - `gt_count` — property — pub
  - `recall_at` — method — pub
  - `to_dict` — method — pub
- `Stage2Result` — dataclass — pub — Per-sample Stage-2 outcome. `missing` / `contradicting` lists are
  - `to_dict` — method — pub
- `_DEFAULT_TIMEOUT` — constant — internal
- `_JUDGE_FILE_RE` — constant — internal
- `_PROMPTS_DIR` — constant — internal
- `_STAGE2_ROW_TEXT_CHARS` — constant — internal
- `_cell_name` — function — internal
- `_entry_matches` — function — internal — A result row satisfies an entry when req_ids match and every
- `_get_json` — function — internal
- `_iter_balanced_objects` — function — internal — Yield every balanced top-level ``{...}`` substring of *raw*, string-
- `_parse_judge_verdict` — function — internal — Extract the JSON verdict from a judge completion. Raises ValueError
- `_post_json` — function — internal
- `check_map_against_stack` — function — pub — Compare the map's ``N`` with the stack's latest served release for
- `classify_scope` — function — pub — Compare the cells a sample's entries expect with the cells the
- `digest_view` — function — pub — The ground truth as it bears on scoring — what the eval-set digest
- `fetch_healthz` — function — pub — Best-effort /healthz snapshot for run-report provenance (D-DRAFT-2).
- `format_ab_delta` — function — pub — One delta line comparing two runs (same samples, same judge version
- `load_judge_prompt` — function — pub — Return ``(version, prompt_text)``. Default: the highest
- `logger` — constant — pub
- `match_ground_truth` — function — pub — Match each ground-truth entry against ranked result rows.
- `query_stack` — function — pub — POST /sira-query on a stack. Raises GoldenEvalError (GEV-E002) on
- `recall_at` — function — pub — Fraction of ground-truth entries hit at rank <= k.
- `row_key` — function — pub — Identity of one retrieved row: ``req_id@<mno>__<release>``, or the
- `run_all` — function — pub — Batch run against one stack. Stage-1 for every non-draft sample;
- `run_stage1` — function — pub — Score one sample's retrieval recall against one stack.
- `run_stage2` — function — pub — Regenerate from Stage-1's retrieved chunks and judge vs golden.
- `scoring_entries` — function — pub — The entries Stage 1 scores, with releases bound for this run.
- `write_run` — function — pub — Persist a run under <env_dir>/eval/golden/runs/<run_id>/ — full JSON

`metrics.py`
- `EvalReport` — dataclass — pub — Aggregate evaluation report.
  - `avg_accuracy` — property — pub
  - `avg_citation_quality` — property — pub
  - `avg_completeness` — property — pub
  - `avg_hallucination_free` — property — pub
  - `avg_overall` — property — pub
  - `avg_standards_integration` — property — pub
  - `by_category` — method — pub
  - `category_averages` — method — pub — Average scores per category.
  - `to_dict` — method — pub
- `QuestionScore` — dataclass — pub — Scores for a single question.
  - `overall` — property — pub — Weighted average of all metrics.
  - `to_dict` — method — pub
- `_REQ_ID_PATTERN` — constant — internal
- `score_question` — function — pub — Score a pipeline response against ground truth.

`questions.py`
- `ALL_QUESTIONS` — constant — pub
- `EvalQuestion` — dataclass — pub — A test question for evaluation.
- `GroundTruth` — dataclass — pub — Expected results for a test question.
- `QUESTIONS_BY_CATEGORY` — constant — pub
- `Q_CROSS_01` — constant — pub
- `Q_CROSS_02` — constant — pub
- `Q_CROSS_03` — constant — pub
- `Q_CROSS_04` — constant — pub
- `Q_FEATURE_01` — constant — pub
- `Q_FEATURE_02` — constant — pub
- `Q_FEATURE_03` — constant — pub
- `Q_FEATURE_04` — constant — pub
- `Q_SINGLE_01` — constant — pub
- `Q_SINGLE_02` — constant — pub
- `Q_SINGLE_03` — constant — pub
- `Q_SINGLE_04` — constant — pub
- `Q_STANDARDS_01` — constant — pub
- `Q_STANDARDS_02` — constant — pub
- `Q_STANDARDS_03` — constant — pub
- `Q_TRACE_01` — constant — pub
- `Q_TRACE_02` — constant — pub
- `Q_TRACE_03` — constant — pub

`release_map.py`
- `HISTORY_FILE` — constant — pub
- `MAP_FILE` — constant — pub
- `ReleaseMap` — dataclass — pub
  - `current` — method — pub — The MNO's ``N``, or None when the map does not list it.
  - `digest` — method — pub
  - `from_dict` — classmethod — pub
  - `resolve` — method — pub — Absolute label for a relative reference. Raises GoldenEvalError
  - `to_dict` — method — pub
  - `validate` — method — pub
- `load_release_map` — function — pub — The current map, or the pinned ``version`` from the history.
- `save_release_map` — function — pub — Write the map as current (atomically) and append it to the history.

`runner.py`
- `ABComparison` — dataclass — pub — A/B comparison between graph-scoped and pure-RAG.
  - `_category_comparison` — method — internal
  - `graph_wins` — property — pub — Questions where graph-scoped outperforms pure RAG.
  - `rag_wins` — property — pub — Questions where pure RAG outperforms graph-scoped.
  - `ties` — property — pub
  - `to_dict` — method — pub
- `EvalRunner` — class — pub — Runs evaluation questions through the query pipeline.
  - `__init__` — constructor — internal
  - `_make_pipeline` — method — internal — Create a pipeline, optionally bypassing graph scoping.
  - `run_ab_comparison` — method — pub — Run A/B comparison: graph-scoped vs pure RAG.
  - `run_all` — method — pub — Run all questions and return an evaluation report.
  - `run_question` — method — pub — Run a single question and score it.
- `logger` — constant — pub
<!-- END:STRUCTURE -->

**Depends on**
[query](../query/MODULE.md) (consumes `QueryPipeline`, `QueryResponse`; golden Stage-2 uses `pinned_chunk_ids`), [llm](../llm/MODULE.md) (A/B across providers via `LLMProvider` injection; golden judge is a separately injectable `LLMProvider` defaulting to the synthesis provider), [vectorstore](../vectorstore/MODULE.md) and [graph](../graph/MODULE.md) as pre-built inputs, [extraction](../extraction/MODULE.md) (`release_key` — the single MMMYYYY validity/ordering rule, for the release map and the map/stack check), [env](../env/MODULE.md) (`golden_cli` reads `env.config` resolvers), [decision](../decision/MODULE.md) (`classify_cli` constructs `DecisionQueryAnalyzer` / `ConfidenceRoutedQueryAnalyzer` and calls `assert_remote_head` / `assert_decision_head_present` before recording any number — the design §7 gate).
Runtime service edge (not an import): **sira-query service** over HTTP — golden Stage-1 POSTs `/sira-query` on the configured stack URL; one run per stack is the release A/B. Same edge kind as the web module's; degrades fail-loud (`GEV-` error), never silently skips a sample.

**Depended on by**
[pipeline](../pipeline/MODULE.md) (eval stage), [web](../web/MODULE.md) (eval-invocation routes).
