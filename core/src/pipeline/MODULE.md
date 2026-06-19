# pipeline

**Purpose**
Staged, re-runnable pipeline that drives the nine-stage offline flow: `extract → profile → parse → resolve → taxonomy → standards → graph → vectorstore → eval`. Owns the error-code catalog, compact-report formatters, stage dispatch table, and the shared `PipelineContext`. Serves FR-15 (corrections override on re-run), FR-17 (PipelineError stable codes), FR-18 (RPT/MET/FIX/QC reports), FR-28 (env_dir parameterization), FR-29 (env_dir partition layout), FR-30 (input/<MNO>/<release>/ source layout); covers NFR-8 (no proprietary content), NFR-9 (new artifacts ship with compact format + QC), NFR-13 (re-runnable + idempotent). This is the layer that makes D-012 real — stable prefixed error codes + compact reports that the user can paste back from a work laptop without exposing proprietary content (D-022 governs path layout).

**Public surface**
- Orchestration (runner.py):
  - `PipelineContext` — shared state (documents_dir, corrections_dir, eval_dir, stage_dirs, model_provider/name/timeout, mnos, releases, state); `stage_output(stage, cell=None)`, `input_cells()`, `correction(filename)`, `create_llm_provider(require_real=False)`
  - `PipelineRunner(ctx)` — `run(stages, continue_on_error=False) -> list[StageResult]`
- Cell primitives (cells.py, D-DRAFT-6): `Cell(mno, release)` with `.relpath`; `PER_CELL_STAGES` / `GLOBAL_STAGES` partition + `is_per_cell_stage(stage)`; `enumerate_input_cells(documents_dir) -> list[Cell]` (validated + sorted by `release_order_key`)
- Stage functions (stages.py): `run_extract`, `run_profile`, `run_parse`, `run_resolve`, `run_taxonomy`, `run_standards`, `run_graph`, `run_vectorstore`, `run_eval` — each `PipelineContext -> StageResult`
- `StageResult` — `stage`, `status` (`OK | WARN | FAIL | SKIP`), `elapsed_seconds`, `stats`, optional `error_code`, `error_message`
- `STAGE_FUNCS` — the stage-name → function dispatch table
- Error catalog (error_codes.py):
  - `ErrorDef` (code, message, hint), `PipelineError` (raised by stages)
  - `CODES` dict — every EXT-, PRF-, PRS-, RES-, TAX-, STD-, GRF-, VEC-, EVL-, PIP-, ENV-, MDL- code with human-readable hint
- Reporting (report.py): `format_compact_report()`, `format_verbose_report()`, `print_qc_template()`, `print_fix_template()`
- CLI: `run_cli.main` — `stages | detect-hw | run ...`

**Invariants**
- **Every failure surfaces a stable prefixed code** registered in `error_codes.CODES`. Ad-hoc strings are a D-012 violation — the user can't diagnose chat-pasted logs without the code.
- Each stage is **re-runnable and idempotent** over the same inputs. Outputs go under `<env_dir>/out/<stage>/`; `<env_dir>/corrections/*.json` is picked up automatically on the next run (D-011, D-022). `run_parse` additionally writes `<env_dir>/reports/parse_log/<doc_id>_parse_log.json` per document (parse transparency log).
- **Per-cell vs global stage outputs** (D-DRAFT-6, strand `multi-mno-nora`): `stage_output(stage, cell)` resolves a **per-cell** stage (`extract`/`profile`/`parse`/`resolve`/`vectorstore`) to `out/<stage>/<mno>/<rel>/` when a `Cell` is supplied, and a **global** stage (`taxonomy`/`standards`/`graph`/`eval`) — or any stage with no cell — to the flat `out/<stage>/`. The per-cell/global partition is exhaustive + disjoint over `STAGE_NAMES`. The per-cell stages now write to cell subdirs: `run_extract` routes each IR by its `(mno, release)`; `run_parse` reads each cell's profile + IRs and writes per-cell trees; `run_resolve` runs per cell; `run_vectorstore` builds one ChromaDB per cell (D-DRAFT-11 slice 1). **Global readers use `rglob`** (`run_taxonomy`, the graph + vectorstore builders, the standards collector) so they find nested per-cell trees/manifests *and* the legacy flat layout.
- **Profile stage is binding-driven, not auto-profiling** (D-DRAFT-7): `run_profile` resolves each cell's profile from `<env_dir>/profiles.json` (or a `--profile` / `corrections/profile.json` override applied to every cell), placeholder-substitutes it once, and materializes `out/profile/<mno>/<rel>/profile.json`. An uncovered cell **fails loud** (`PIP-E003`) listing every miss — the previous auto-`DocumentProfiler` fallback is gone (hand-authored profiles are the contract). `run_parse` reads the materialized per-cell profile **raw** (substitution already applied).
- **Cross-reference resolution is cell-scoped** (D-DRAFT-10): `run_resolve` resolves within one cell's trees at a time, so a cross-plan reference never matches across MNOs/releases; cross-cell relationships live in the global graph.
- **Global taxonomy is fingerprint-cached** (D-DRAFT-9): `run_taxonomy` is a global stage — it derives ONE union feature set over every cell's trees (rglob), so cross-MNO comparison shares a feature space. Because it's LLM-driven, expensive, and run-to-run non-deterministic, derivation is gated on a **corpus fingerprint** (hash of the contributing tree set, path + content; written to `out/taxonomy/.corpus_fingerprint`): an unchanged tree set reuses the cached `taxonomy.json` (`stats.source == "cache"`), a changed set re-derives (`"derived"`); `--force` busts the cache. Extraction runs at `temperature=0` (already in `FeatureExtractor`) for reproducibility. So an incremental cell add doesn't silently re-derive (and re-shuffle) the whole corpus's features unless the trees actually changed.
- **Incremental ingestion: scope + skip** (D-DRAFT-8): `--mno` / `--release` (`PipelineContext.scope_mnos` / `scope_releases`, comma-separable) restrict which cells the **per-cell** stages process — `input_cells()` and `run_extract` filter by `cell_in_scope()`; global stages still read the whole union. Per-cell stages also **skip-if-unchanged**: `run_extract` reuses an IR newer than its source; `run_parse` reuses a tree newer than its IR whose stamped `RequirementTree.profile_fingerprint` (hash of the substituted cell profile) matches the current profile — so a profile/mapping edit (different fingerprint) forces a re-parse that mtime alone couldn't detect. `--force` (`ctx.force`) overrides the skip. So a re-run after dropping a new cell reprocesses only the new/changed cells while the global graph rebuilds over the union.
- Stage order is fixed and matches `env.config.PIPELINE_STAGES`. Running a downstream stage without its prerequisites emits `PIP-E002` (required input missing) rather than silently failing later.
- `StageResult.status` uses four discrete values: `OK` (clean), `WARN` (completed but flagged), `FAIL` (aborted), `SKIP` (not run this invocation). Collapsing WARN into OK loses the signal that drives compact QC reports.
- `PipelineContext.create_llm_provider()` falls back to `MockLLMProvider` when Ollama is unreachable unless `require_real=True`. This keeps offline stages runnable on work laptops without an LLM server.
- Compact reports (`format_compact_report`) contain **no proprietary content** — only stage names, codes, counts, and timings. Verbose reports (separate function) may contain content and go to disk, not chat.

**Key choices**
- Plain functions per stage + dispatch dict instead of a class hierarchy — each stage is independently runnable from the CLI or tests without instantiating anything upstream.
- Prefix-per-module error codes (EXT / PRF / PRS / RES / TAX / STD / GRF / VEC / EVL / PIP / ENV / MDL) — the prefix points the user at the right module without needing to read the message.
- LLM provider creation is centralized in `PipelineContext` so stages don't each invent their own fallback policy; the "Ollama → Mock" fallback lives in one place.
- Two report formats (compact vs verbose): compact is chat-pasteable and content-free; verbose goes to disk and may include context. The split is explicit because mixing them is exactly how proprietary content leaks.
- `continue_on_error` is opt-in — default is fail-fast, because downstream stages usually can't recover from a missing input.

**Non-goals**
- Not a DAG/parallel executor — stages are serial and batch. Quarterly release cadence doesn't justify parallel infrastructure.
- Not a scheduler — the Web UI's job queue ([web](../web/MODULE.md)) handles submission/persistence; pipeline just runs to completion when invoked.
- No metrics persistence. Observability IDs (REQ/LLM/PIP/RES/MET) and the SQLite metrics DB live in [web](../web/MODULE.md); pipeline emits timings and counts but doesn't own storage.
- No retry logic. A failed stage is re-runnable — rerun via CLI or fix the underlying input. Automated retries hide real errors.

<!-- BEGIN:STRUCTURE -->
_Alphabetical, regenerated by regen-map._


`cells.py`
- `Cell` — class — pub — A `(MNO, release)` pair, e.g. `Cell("VZW-OA", "Feb2026")`.
  - `relpath` — property — pub — The cell's directory fragment: `<mno>/<release>`.
- `GLOBAL_STAGES` — constant — pub
- `PER_CELL_STAGES` — constant — pub
- `enumerate_input_cells` — function — pub — Scan `input/<MNO>/<MMMYYYY>/` → the cells present, validated + sorted.
- `is_per_cell_stage` — function — pub — True iff `stage` writes per-cell output (`out/<stage>/<mno>/<rel>/`).

`error_codes.py`
- `CODES` — constant — pub
- `ErrorDef` — dataclass — pub
  - `format` — method — pub — Format message with context variables.
- `PipelineError` — class — pub — Structured pipeline error with error code.
  - `__init__` — constructor — internal
- `_DEFS` — constant — internal

`report.py`
- `FIX_TEMPLATES` — constant — pub
- `QC_TEMPLATES` — constant — pub
- `_SHORT` — constant — internal
- `_format_stats_compact` — function — internal — Format stage stats into a compact key=value string.
- `format_compact_report` — function — pub — Generate a compact report suitable for pasting in chat.
- `format_verbose_report` — function — pub — Generate a verbose terminal report.
- `print_fix_template` — function — pub — Get the correction feedback template for an artifact.
- `print_qc_template` — function — pub — Get the quality check template for a stage.

`run_cli.py`
- `_detect_hw` — function — internal
- `_list_stages` — function — internal
- `logger` — constant — pub
- `main` — function — pub

`runner.py`
- `PipelineContext` — dataclass — pub — Shared context passed through all pipeline stages.
  - `_resolve_model` — method — internal — Resolve 'auto' model name using hardware detection.
  - `cell_in_scope` — method — pub — True iff `(mno, release)` passes the `--mno`/`--release` scope (D-DRAFT-8).
  - `correction` — method — pub — Get a correction file path if it exists.
  - `create_llm_provider` — method — pub — Create an LLM provider based on config.
  - `from_env` — classmethod — pub — Create context from an EnvironmentConfig.
  - `input_cells` — method — pub — The in-scope `(MNO, release)` cells present under `input/`, validated + sorted.
  - `stage_output` — method — pub — Get the output directory for a stage.
  - `standalone` — classmethod — pub — Create context for standalone (no EnvironmentConfig) mode.
- `PipelineRunner` — class — pub — Orchestrates pipeline stage execution.
  - `__init__` — constructor — internal
  - `run` — method — pub — Run the specified stages in order.
- `logger` — constant — pub

`stages.py`
- `STAGE_FUNCS` — constant — pub
- `StageResult` — dataclass — pub — Result from running a single pipeline stage.
  - `ok` — property — pub
- `_corpus_fingerprint` — function — internal — Hash of the contributing tree set (D-DRAFT-9).
- `_fail` — function — internal
- `_load_user_eval_questions` — function — internal — Load user-supplied evaluation questions from Excel files.
- `_split_csv` — function — internal — Split comma-separated string into list, stripping whitespace.
- `logger` — constant — pub
- `run_eval` — function — pub — Run evaluation.
- `run_extract` — function — pub — Extract documents into normalized IR.
- `run_graph` — function — pub — Build the knowledge graph.
- `run_parse` — function — pub — Parse each cell's IRs into requirement trees (D-DRAFT-6/7).
- `run_profile` — function — pub — Resolve + materialize each cell's parse profile (D-DRAFT-7).
- `run_resolve` — function — pub — Resolve cross-references across parsed trees.
- `run_standards` — function — pub — Ingest referenced 3GPP standards.
- `run_taxonomy` — function — pub — Extract feature taxonomy from parsed trees.
- `run_vectorstore` — function — pub — Build the vector store.
<!-- END:STRUCTURE -->

**Depends on**
[env](../env/MODULE.md), [corrections](../corrections/MODULE.md), [extraction](../extraction/MODULE.md), [profiler](../profiler/MODULE.md), [parser](../parser/MODULE.md), [resolver](../resolver/MODULE.md), [taxonomy](../taxonomy/MODULE.md), [standards](../standards/MODULE.md), [graph](../graph/MODULE.md), [vectorstore](../vectorstore/MODULE.md), [eval](../eval/MODULE.md), [llm](../llm/MODULE.md) (via `PipelineContext.create_llm_provider`).

**Depended on by**
[web](../web/MODULE.md) (submits jobs via the pipeline runner).

**Deferred**
- Declare `models` and `query` in Depends on (deferred: imports are thin — `DocumentIR` for type, `query.pipeline`/`query.synthesizer` for the eval stage — and pipeline's Depends on list is already long — revisit: during a Depends-on audit or if the imports deepen)
