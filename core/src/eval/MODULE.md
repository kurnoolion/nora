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
- Golden eval (strand golden-eval — design; code pending):
  - Schema (golden.py): `GoldenSample` — sample_id, created_by/created_at/updated_at, area (use-case tag), query, `ground_truth: list[GroundTruthEntry]` (req_id + optional `(mno, release, plan)` qualifiers — picker-sourced entries are always fully qualified, direct-entry may be bare), golden_response (None until curated), golden_meta (curated_at, chat_turns, model), status (`draft | stage1-ready | golden-ready`); `load_samples(env_dir)`, `save_sample(env_dir, sample)` — one JSON file per sample under `<env_dir>/eval/golden/samples/<sample_id>.json`
  - Runner (golden_runner.py): `GoldenRunner(stack_url, pipeline, judge_llm)` — `run_stage1(sample)` (POST `/sira-query` → per-sample recall + per-hit ranks, so recall@5/@10 derive from one run), `run_stage2(sample, stage1_result)` (pin Stage-1 retrieved req_ids into `QueryPipeline` via `pinned_chunk_ids` → judge candidate vs golden_response), `run_all(samples) -> GoldenRunReport`; run artifacts under `<env_dir>/eval/golden/runs/<run_id>/`
  - Judge prompt: `prompts/judge_v<N>.txt` — versioned committed artifact (generic wording; sees proprietary content only at runtime); version recorded in every run report
  - CLI: `golden_cli.main` — batch entrypoint (stack URL(s), stage selection, env_dir); runnable standalone as a build step or as a final pipeline stage

**Invariants**
- The A/B modes compare `graph_scoped` vs. `pure_rag` on the **same questions, same embedder, same store, same LLM** — only the scoping differs. Any variable beyond scope drift invalidates the comparison.
- `EvalReport.scores` is ordered to match `questions` — position `i` in both lists is the same question. `ABComparison` relies on this zipping.
- `score_question()` returns a `QuestionScore` with per-dimension sub-scores and an aggregated `overall`. Aggregation is deterministic; regressions trace to a specific sub-score.
- Eval never mutates the graph, vector store, or corrections. It reads; it reports.
- User-supplied questions from Excel (`<env_dir>/eval/*.xlsx` per D-022) are loaded through the same `EvalQuestion` schema as built-ins — no parallel code path.
- Golden samples and run reports live under `<env_dir>/eval/golden/` — proprietary content (queries, req_ids, golden responses). Never in the repo; chat-pasteable compact summaries carry counts/percentages only (NFR-8).
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
[query](../query/MODULE.md) (consumes `QueryPipeline`, `QueryResponse`; golden Stage-2 uses `pinned_chunk_ids`), [llm](../llm/MODULE.md) (A/B across providers via `LLMProvider` injection; golden judge is a separately injectable `LLMProvider` defaulting to the synthesis provider), [vectorstore](../vectorstore/MODULE.md) and [graph](../graph/MODULE.md) as pre-built inputs.
Runtime service edge (not an import): **sira-query service** over HTTP — golden Stage-1 POSTs `/sira-query` on the configured stack URL(s); one run per stack is the release A/B. Same edge kind as [web](../web/MODULE.md)'s; degrades fail-loud (`GEV-` error), never silently skips a sample.

**Depended on by**
[pipeline](../pipeline/MODULE.md) (eval stage), [web](../web/MODULE.md) (eval-invocation routes).
