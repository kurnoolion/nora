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
  - Schema (golden.py): `GoldenSample` — sample_id, created_by/created_at/updated_at, area (use-case tag), `mno` (carrier tag, strand eval-studio-ux-2 [D-206] — authoring metadata for grouping/filtered runs; NOT used by recall matching, which keys on per-entry qualifiers, and deliberately outside the eval-set digests so tag edits don't invalidate run comparability), query, `kind` (`standard` default | `version` — release-handling design, strand golden-eval: scoring-relevant, so inside the eval-set digest; a standard sample is a question about current behaviour and matches WITHOUT the release, a version sample's entries carry an absolute `MMMYYYY` or relative `N` / `N-<k>` release — relative needs the entry's `mno`, is version-only, and is not mixed with absolute in one sample; grammar helpers `is_relative_release` / `relative_offset`), `ground_truth: list[GroundTruthEntry]` (req_id + optional `(mno, release, plan)` qualifiers — picker-sourced entries are always fully qualified, direct-entry may be bare; on a standard sample the release is provenance only), golden_response (None until curated), golden_meta (curated_at, chat_turns, model, `edited` — manual-edit provenance flag, eval-studio-ux-2; `review` — per-MNO flag written by a release move, `needs_review(sample)`; the runner skips Stage 2 for a flagged sample with GEV-W002 and leaves it out of the Stage-2 digest), status (`draft | stage1-ready | golden-ready`); `load_samples(env_dir)`, `save_sample(env_dir, sample)` — one JSON file per sample under `<env_dir>/eval/golden/samples/<sample_id>.json`
  - Runner (golden_runner.py): `GoldenRunner(stack_url, pipeline, judge_llm)` — `run_stage1(sample)` (POST `/sira-query` → per-sample recall + per-hit ranks, so recall@5/@10 derive from one run), `scoring_entries(sample, release_map)` (binds entries for a run: version entries' relative releases resolved — GEV-E005 without a map or out of range, raised before any stack call; standard entries bound to their MNO's `N` for the scope check, or `""` without a map), match rule **m2** (`MATCH_RULE`): standard samples match on `(req_id, mno)`, version samples on `(req_id, mno, resolved release)` — `match_ground_truth(..., ignore_release)`, `classify_scope(ground_truth, misses, resolved_cells)` (field-found: a miss because the stack never searched the entry's cell looked like a retrieval miss — compares the cells the entries name with the stack's `resolved_cells`, tags each miss `miss_kind` `scope` / `retrieval`, and returns the sample's scope `ok | extra | miss | miss+extra | n/a` plus the missing and extra cell lists, carried on `Stage1Result`; diagnosis only — recall is unchanged and nothing enters the comparability keys; the GEV block gains a `scope:` count line, omitted when no sample has scope data so cell-less stacks' blocks stay byte-identical), `run_stage2(sample, stage1_result)` (pin Stage-1 retrieved req_ids into `QueryPipeline` via `pinned_chunk_ids` → judge candidate vs golden_response), `run_all(samples) -> GoldenRunReport`; run artifacts under `<env_dir>/eval/golden/runs/<run_id>/`. `SiraRowsPipeline` (strand golden-eval, field-found) — duck-typed Stage-2 pipeline for the SIRA-only lane, which never builds `out/graph`/`out/vectorstore`: synthesizes with the production `LLMSynthesizer` prompt over the rows Stage-1 actually retrieved (full text via the service's `text_chars` knob; rows retained TRANSIENTLY on `Stage1Result.retrieved_rows`, never serialized), empty-graph context enrichment (the established RAG-only degradation); the CLI auto-selects it when the vectorstore dir is absent. `GoldenRunReport.stage2_mode` (`"" | "pipeline" | "sira-rows"`) + `stage2_skip_reason` make a Stage-2 that never ran distinguishable in report.json from one that ran and scored nothing (field-found silent-skip defect); the GEV block prints `s2: SKIPPED (<reason>)`. `StackStamp` (D-DRAFT-14, strand golden-eval) — runtime-captured identity of the stack a run scored against (serve label, data fingerprint, code version, SIRA prompt scheme, answer-prompt version, LLM identity, reasoning effort, retrieval knobs, fallback counter snapshot), built via `StackStamp.from_healthz(healthz, ...)` with caller-supplied fields winning; comparability is code — `stage1_key()` (data + code + knobs) / `stage2_key()` (+ prompt + LLM, judge version compared alongside); empty field = "not comparable on this axis", never a guess; `compact_line()` renders the redaction-safe GEV `id:` line. Release handling: `stage1_key()` also carries `match_rule` (bumped on any matching-rule change — stored samples can be byte-identical across one); the eval-set digests cover `(id, query, kind, digest_view(sample, map))` — standard entries with the release blanked (a provenance re-tag or a move of `N` keeps comparability), version entries with the resolved release (a move of `N` that re-binds them breaks it); `release_map_version` / `release_map_mismatch` are stamped for attribution (`match=m2 rmap=v<N>` on the `id:` line, `!` when scored despite a disagreement), not keyed. `run_all(..., release_map, allow_map_mismatch)` checks the map's `N` against the stack's latest served release per MNO the samples use (`check_map_against_stack`, from healthz `cells`) BEFORE scoring and raises GEV-E006 on a disagreement unless allowed. Stamped on every `GoldenRunReport`; serving-side healthz adoption of the canonical keys (`data_fingerprint`, `code_version`, `serve_label`, `sira_prompt_scheme`) is incremental follow-up work
  - Release map (release_map.py, release-handling design): `ReleaseMap(version, mnos, updated_at, updated_by)` — per MNO the releases oldest first; `current(mno)` = `N`, `resolve(mno, ref)` binds `N-<k>` (GEV-E005 out of range), `validate()`, `digest()`; `load_release_map(golden_dir, version=None)` (current file, or a pinned version from the history; None when the set has no map yet) and `save_release_map(golden_dir, rmap)` (atomic current + append-only history; refuses an invalid or non-advancing version). Files `<golden>/release-map.json` + `release-map.history.jsonl` — golden-set data beside the samples (NFR-8). Explicit, never derived from a stack: all stacks share one set and flip at different times, and a stack stuck on an old label reports that label as its own latest
  - CLI: `golden_release_cli.main` — release map maintenance: `show`, `migrate` (write v1 for an existing set; `--n MNO=REL` overrides), `advance --mno --release` (after ingestion + flip), `review` (list / `--clear` flags); `migrate`/`advance` are dry runs without `--yes`. The **text check** (`text_check` → `apply_check`): every standard entry of the MNO whose stored release is older than the new `N` is compared by whitespace-normalised text between the two releases — unchanged (stored release moved forward; on a standard sample the stored release is provenance only) / changed / absent / unverified; any non-unchanged entry, and every relative version sample on an advance, flags the sample in `golden_meta["review"][mno]`. Reads parse trees through its own per-cell reader (`parse_cells`, `cell_texts`) — web's `req_tree` is not importable from eval (web depends on eval). Output names ids: local only; the counts line is chat-pasteable
  - Judge prompt: `prompts/judge_v<N>.txt` — versioned committed artifact (generic wording; sees proprietary content only at runtime); version recorded in every run report
  - CLI: `golden_report_cli.main` — run-dir inspector: per-sample expected (ground-truth) vs retrieved req_ids side by side from a run's `report.json` (`--misses` filters to samples with missed entries; `--samples-dir` adds query text, defaulting to the run dir's sibling `samples/`). Read-only, stdlib-only (copyable to a field machine and runnable directly); output is run-dir material — proprietary, local inspection only, never chat-pasteable (NFR-8)
  - CLI: `golden_cli.main` — batch entrypoint (stack URL(s), stage selection, env_dir); runnable standalone as a build step or as a final pipeline stage. `--mno` filters the run to samples tagged with that carrier (filter applies BEFORE the stamp freezes the eval set, so filtered runs key apart from full runs). `--release-map-version K` scores with a pinned map version (a stack that has not flipped; re-scoring an old run); `--allow-map-mismatch` scores past a GEV-E006 disagreement and stamps it; a GEV-E006 refusal or an unloadable map exits 2 with nothing scored. `--reasoning {none,low,medium,high}` sets the reasoning effort for **Stage-2 synthesis only** (openai-compatible endpoints; the judge keeps the endpoint default, so scoring behaviour stays fixed while generation varies)

**Invariants**
- Reasoning effort is recorded on the stamp and printed as `rsn=` on the GEV `id:` line, but is deliberately **NOT** a comparability key — two runs at different levels still pool. Restricting the comparison is the analyst's call, not the tool's; the printed line is what makes the difference visible. Tests assert both halves (`test_eval_golden_runner.py::TestStackStamp`).
- The A/B modes compare `graph_scoped` vs. `pure_rag` on the **same questions, same embedder, same store, same LLM** — only the scoping differs. Any variable beyond scope drift invalidates the comparison.
- `EvalReport.scores` is ordered to match `questions` — position `i` in both lists is the same question. `ABComparison` relies on this zipping.
- `score_question()` returns a `QuestionScore` with per-dimension sub-scores and an aggregated `overall`. Aggregation is deterministic; regressions trace to a specific sub-score.
- Eval never mutates the graph, vector store, or corrections. It reads; it reports.
- User-supplied questions from Excel (`<env_dir>/eval/*.xlsx` per D-022) are loaded through the same `EvalQuestion` schema as built-ins — no parallel code path.
- Golden samples and run reports live under `<env_dir>/eval/golden/` — proprietary content (queries, req_ids, golden responses). Never in the repo; chat-pasteable compact summaries carry counts/percentages only (NFR-8).
- Standard samples never use the release to match (m2); the release check is the scope check against the map's `N`. Version samples always match the resolved release. Any change to either rule bumps `MATCH_RULE`.
- Stage-1 scores the serving stack **black-box over HTTP** (`POST /sira-query`) — never an in-process reconstruction of retrieval. The metric measures what the stack actually serves; pointing the runner at different stack URLs is the release A/B.
- The judge-prompt version is recorded in every Stage-2 report. Judge scores are comparable only within the same judge version; a report never mixes versions.
- Stage-2's candidate response is generated through `QueryPipeline` with `pinned_chunk_ids` — by construction the same synthesis prompt the production web lane uses. Any drift between eval-side and web-side synthesis invalidates the judge score.

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
- `_RELATIVE_RE` — constant — internal
- `_SAMPLE_ID_RE` — constant — internal
- `golden_root` — function — pub
- `is_relative_release` — function — pub — True for ``N`` / ``N-<k>`` (k >= 1) — an MNO's current release and
- `load_sample` — function — pub — Load one sample file. Raises GoldenEvalError (GEV-E001) on
- `load_samples` — function — pub — Load every sample under <env_dir>/eval/golden/samples/, sorted by
- `logger` — constant — pub
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
- `logger` — constant — pub
- `main` — function — pub

`golden_release_cli.py`
- `CheckReport` — dataclass — pub
  - `counts` — method — pub
- `SampleCheck` — dataclass — pub
  - `flagged` — property — pub
- `_NAMED_RELEASE_RE` — constant — internal
- `_now` — function — internal
- `_parse_n` — function — internal
- `_user` — function — internal
- `apply_check` — function — pub — Write the check's outcome into the samples: move unchanged entries'
- `cell_texts` — function — pub — ``{req_id: normalized title + text}`` for one cell. Whitespace is
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
- `Stage1Result` — dataclass — pub — Per-sample Stage-1 outcome. Full detail (req_ids, ranks) is run-dir
  - `gt_count` — property — pub
  - `recall_at` — method — pub
  - `to_dict` — method — pub
- `Stage2Result` — dataclass — pub — Per-sample Stage-2 outcome. `missing` / `contradicting` lists are
  - `to_dict` — method — pub
- `MATCH_RULE` — constant — pub
- `_DEFAULT_TIMEOUT` — constant — internal
- `_JUDGE_FILE_RE` — constant — internal
- `_PROMPTS_DIR` — constant — internal
- `_cell_name` — function — internal
- `_entry_matches` — function — internal — A result row satisfies an entry when req_ids match and every
- `_get_json` — function — internal
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
[query](../query/MODULE.md) (consumes `QueryPipeline`, `QueryResponse`; golden Stage-2 uses `pinned_chunk_ids`), [llm](../llm/MODULE.md) (A/B across providers via `LLMProvider` injection; golden judge is a separately injectable `LLMProvider` defaulting to the synthesis provider), [vectorstore](../vectorstore/MODULE.md) and [graph](../graph/MODULE.md) as pre-built inputs, [extraction](../extraction/MODULE.md) (`release_key` — the single MMMYYYY validity/ordering rule, for the release map and the map/stack check).
Runtime service edge (not an import): **sira-query service** over HTTP — golden Stage-1 POSTs `/sira-query` on the configured stack URL(s); one run per stack is the release A/B. Same edge kind as the web module's; degrades fail-loud (`GEV-` error), never silently skips a sample.

**Depended on by**
[pipeline](../pipeline/MODULE.md) (eval stage), [web](../web/MODULE.md) (eval-invocation routes).
