# pipeline

**Purpose**
Staged, re-runnable pipeline that drives the nine-stage offline flow: `extract → profile → parse → resolve → taxonomy → standards → graph → vectorstore → eval`. Owns the error-code catalog, compact-report formatters, stage dispatch table, and the shared `PipelineContext`. Serves FR-15 (corrections override on re-run), FR-17 (PipelineError stable codes), FR-18 (RPT/MET/FIX/QC reports), FR-28 (env_dir parameterization), FR-29 (env_dir partition layout), FR-30 (input/<MNO>/<release>/ source layout); covers NFR-8 (no proprietary content), NFR-9 (new artifacts ship with compact format + QC), NFR-13 (re-runnable + idempotent). This is the layer that makes D-012 real — stable prefixed error codes + compact reports that the user can paste back from a work laptop without exposing proprietary content (D-022 governs path layout).

**Public surface**
- Orchestration (runner.py):
  - `PipelineContext` — shared state (documents_dir, corrections_dir, eval_dir, stage_dirs, model_provider/name/timeout, llm_reasoning, embedding_provider/model, mnos, releases, state); `stage_output(stage, cell=None)`, `input_cells()`, `correction(filename)`, `create_llm_provider(require_real=False)`
  - `PipelineRunner(ctx)` — `run(stages, continue_on_error=False) -> list[StageResult]`
- Cell primitives (cells.py, D-DRAFT-6): `Cell(mno, release)` with `.relpath`; `PER_CELL_STAGES` / `GLOBAL_STAGES` partition + `is_per_cell_stage(stage)`; `enumerate_input_cells(documents_dir) -> list[Cell]` (validated + sorted by `release_order_key`)
- Stage functions (stages.py): `run_extract`, `run_profile`, `run_parse`, `run_resolve`, `run_taxonomy`, `run_standards`, `run_graph`, `run_vectorstore`, `run_eval` — each `PipelineContext -> StageResult`
- `StageResult` — `stage`, `status` (`OK | WARN | FAIL | SKIP`), `elapsed_seconds`, `stats`, optional `error_code`, `error_message`
- `STAGE_FUNCS` — the stage-name → function dispatch table
- Stage graph (stage_graph.py, strand stage-graph, D-DRAFT-1): `StageSpec(name, inputs, externals, params, uses_model)` with `.per_cell`; `SPECS` / `STAGE_GRAPH` / `STAGE_ORDER` (equals `STAGE_NAMES`); `spec_of(stage)`; `tier_of(stage) -> "corpus" | "variant"` by the rule *no model call and corpus inputs only*; `CORPUS_STAGES` (extract, profile, parse, resolve, standards), `VARIANT_STAGES`, `CORPUS_PREFIX`; `GLOBAL_KEY` (`"*"`, the manifest key of a global stage); `validate_graph() -> list[str]`.
- Fingerprints (fingerprints.py, D-DRAFT-2): `stage_fingerprint(stage, code_version, params, input_fps, external_fps)` (sha256 of canonical JSON, 16 hex), `digest`, `hash_bytes`, `hash_files(files, rel_to)`, `code_version(stage)` (a corpus stage's package `CODE_VERSION` via `CORPUS_VERSION_MODULES`; else `NORA_CODE_VERSION` or `"dev"`), `cell_key(stage, cell)`, constants `ABSENT` / `UNRESOLVED`; `Fingerprinter(ctx, manifest)` — `params`, `input_fps`, `externals` (`documents`, `profile`, `annotations`, `overview_prompt`), `current(stage, cell)`, `recorded`, `unchanged(stage, cell, fp=None)` (False under `--force`), `in_store(stage)`, `prepare(stage, cell, fp, complete=None) -> bool` (True = reuse: recorded here, or found in the store and cloned in; False = compute: the cell's directory made private, the store's building marker set), `stamp(stage, cell, fp=None)` (records with provenance and `origin`, puts a stored stage's cell into the store), `forget(stage, cell, fp=None)`, `cell_input_dir(cell)`; constants `NOT_STORED`, `ORIGIN_COMPUTED` / `ORIGIN_STORE` / `ORIGIN_FORCED`.
- Stage store (stage_store.py, step 2, D-DRAFT-5/8): `StageStore(root, wait_seconds=None, poll_seconds=5, sleep=time.sleep)` over `<stages-root>/<stage>/<MNO>/<release>/<fp>/` — `path`, `provenance_path`, `marker_path`, `cell_dir`, `has`, `provenance`, `commit(stage, cell, fp, src, provenance) -> bool` (False when the store already held `fp`; `PIP-E005` when unwritable), `link_view(stage, cell, fp, view)`, `mark_building`, `release`, `wait_for(stage, cell, fp, build) -> bool`; helpers `clone_tree(src, dst)` (hard link, else copy), `make_readonly(tree)`, `privatize(tree) -> int`; `DEFAULT_WAIT_SECONDS`, `WAIT_SECONDS_ENV`.
- Recipes (recipes.py, step 3, standard library only): `Recipe(name, stages, params, serve, note)` with `has`, `stage_params(stage)` (`generation` defaulted to 1), `param`, `enrich_uses_taxonomy`, `prompt_scheme`, `digest` (16 hex, note excluded), `to_dict`; `parse(data, name_hint)`, `load(path, check_name)`, `load_frozen(build_dir)`, `recipe_path(repo_root, name)`, `get(recipe, key)`; `RecipeError` (`RCP-E001` unreadable, `RCP-E002` invalid); `DRIVER_STAGES` (taxonomy, enrich), `RESERVED_STAGES`, `PARAMS`, `SERVE_MODES`, `SERVE_UNITS`, `RECIPES_DIR`, `RECIPE_FILE`. CLI `python3 -m core.src.pipeline.recipes check <file> | get <file> <key>` for the host-side driver.
- Build manifest (manifest.py): `BuildManifest(path)` over `<env_dir>/STAGES.json` (`MANIFEST_NAME`) — `get(stage, key)`, `entry`, `fingerprints(stage)`, `stages()`, `record(stage, key, fp, **provenance)`, `forget`, `save` (atomic); a corrupt file raises `PIP-E004`.
- `PipelineContext.build_dir` / `.env_dir` (the build directory), `.build_id`, `.recipe` (the build's frozen `RECIPE.json`, or None; lazy), `.fingerprints` (the build's `Fingerprinter`, lazy), `.profile_override()` (the `--profile` / `corrections/profile.json` single-profile override); `.stages_root` / `.stage_store` (the host's store, or None) and `.corpus_dir` with `.bindings_dir` / `.annotations_dir` (the corpus root when set, else the build), both factories reading `STAGES_ROOT_ENV` / `CORPUS_DIR_ENV`.
- Error catalog (error_codes.py):
  - `ErrorDef` (code, message, hint), `PipelineError` (raised by stages)
  - `CODES` dict — every EXT-, PRF-, PRS-, PRV-, RES-, RSV-, TAX-, STD-, GRF-, VEC-, EVL-, GEV-, OVL-, TRS-, HIS-, PUB-, REG-, RHD-, IRN-, PIP-, ENV-, MDL- code with human-readable hint. Every code raised anywhere is registered, including `PIP-E003` (uncovered profile binding; raised in stages.py and env/profile_bindings.py) and `EXT-E004` (non-MMMYYYY release dir; raised via extraction/release_key.py). `GEV-` is the golden-eval family; `OVL-` / `TRS-` / `HIS-` are the Overlay, Trees and History stores (their build/import CLIs and their readers' schema refusals, `SchemaVersionError`); `PRV-` and `RSV-` are the parse-review and resolve-review CLIs. A test scans the source for unregistered codes.
- Reporting (report.py): `format_compact_report()`, `format_verbose_report()`, `print_qc_template()`, `print_fix_template()`
- CLI: `run_cli.main` — flags only, no subcommands. One-shot modes (mutually exclusive with `--env` / `--env-dir`): `--list-stages`, `--detect-hw`, `--qc-template STAGE`, `--fix-template ARTIFACT`. Run options: `--env/-e NAME` or `--env-dir PATH` (falls back to `$ENV_DIR`); `--start` / `--end`; `--profile`; `--mno` / `--release` / `--force` (D-DRAFT-8); `--model`, `--model-timeout`, `--llm-provider`; `--embedding-provider`, `--embedding-model`; `--standards-source`; `--skip-taxonomy` / `--skip-graph` / `--skip-resolve` / `--skip-standards` (each with a `--no-` form that forces the stage on) and `--rag-only`; `--continue-on-error`; `--verbose`. `--lane {ingestion,nora}` (`lane_bounds()` — resolves a `PIPELINE_LANES` named range to start/end; mutually exclusive with `--start`/`--end`, D-DRAFT-5 docker-distro); `--requirements-dir` (documents-root override — repoints `ctx.documents_dir` via `env.resolve_requirements_dir`, D-DRAFT-6 docker-distro; reports stay at `<env_dir>/reports` regardless)
- Env vars: `ENV_DIR` (standalone env_dir when no `--env` / `--env-dir`); `NORA_REQUIREMENTS_DIR`; `NORA_LLM_PROVIDER`; `NORA_EMBEDDING_PROVIDER` / `NORA_EMBEDDING_MODEL`; `NORA_STANDARDS_SOURCE`; `NORA_SKIP_TAXONOMY` / `NORA_SKIP_GRAPH` / `NORA_SKIP_RESOLVE` / `NORA_SKIP_STANDARDS`; `NORA_TAXONOMY_OVERVIEW_DIR` (directory of per-MNO corpus-overview prompt files for the taxonomy stage); `NORA_STAGES_ROOT` (the host's stage store), `NORA_CORPUS_DIR` (the corpus root), `NORA_STAGES_WAIT_SECONDS` (the wait on another build's marker), `NORA_CODE_VERSION` (variant-stage code version). A CLI flag beats its env var.

**Invariants**
- **Every failure surfaces a stable prefixed code** registered in `error_codes.CODES`. Ad-hoc strings are a D-012 violation — the user can't diagnose chat-pasted logs without the code.
- Each stage is **re-runnable and idempotent** over the same inputs. Outputs go under `<env_dir>/out/<stage>/`; `<env_dir>/corrections/*.json` is picked up automatically on the next run (D-011, D-022). `run_parse` additionally writes `<env_dir>/reports/parse_log/<doc_id>_parse_log.json` per document (parse transparency log).
- **Per-cell vs global stage outputs** (D-DRAFT-6, strand `multi-mno-nora`): `stage_output(stage, cell)` resolves a **per-cell** stage (`extract`/`profile`/`parse`/`resolve`/`taxonomy`/`vectorstore`) to `out/<stage>/<mno>/<rel>/` when a `Cell` is supplied, and a **global** stage (`standards`/`graph`/`eval`) — or any stage with no cell — to the flat `out/<stage>/`. The per-cell/global partition is exhaustive + disjoint over `STAGE_NAMES`. The per-cell stages write to cell subdirs: `run_extract` routes each IR by its `(mno, release)`; `run_parse` reads each cell's profile + IRs and writes per-cell trees; `run_resolve` runs per cell; `run_taxonomy` runs per cell (since 2026-11-06, strand self-serve-ingestion; below); `run_vectorstore` builds one ChromaDB per cell (D-DRAFT-11 slice 1). **Global readers use `rglob`** (the graph + vectorstore builders, the standards collector) so they find nested per-cell trees/manifests *and* the legacy flat layout.
- **Profile stage is binding-driven, not auto-profiling** (D-DRAFT-7): `run_profile` resolves each cell's profile from `<env_dir>/profiles.json` (or a `--profile` / `corrections/profile.json` override applied to every cell), placeholder-substitutes it once, and materializes `out/profile/<mno>/<rel>/profile.json`. An uncovered cell **fails loud** (`PIP-E003`) listing every miss — the previous auto-`DocumentProfiler` fallback is gone (hand-authored profiles are the contract). `run_parse` reads the materialized per-cell profile **raw** (substitution already applied).
- **Cross-reference resolution is cell-scoped** (D-DRAFT-10): `run_resolve` resolves within one cell's trees at a time, so a cross-plan reference never matches across MNOs/releases; cross-cell relationships live in the global graph.
- **Every stage records a fingerprint per cell in the build manifest** (strand stage-graph, step 1; D-DRAFT-1/2): `fp(stage, cell) = sha256(stage, code version, params, the inputs' recorded fingerprints, the external inputs)[:16]`, computed the same way for every stage from `stage_graph.SPECS`. A stage stamps a cell in `<env_dir>/STAGES.json` when it completes the cell cleanly and forgets it when a run of the cell degrades (a failed document, a failed plan), so a recorded fingerprint always stands for a complete output. Corpus-tier stages hash their package's `CODE_VERSION`, never the image's, so an image rebuild for a prompt change leaves every parse fingerprint intact; variant-tier stages hash `NORA_CODE_VERSION`. A global stage takes each per-cell input's whole `{cell: fp}` map, so adding a cell changes it. An input with no recorded output enters as `"absent"`. The externals are the cell's documents (extract), the bound profile after substitution (profile), the cell's documents' annotations (parse) and the MNO's overview prompt (taxonomy). The skip rules of extract, parse and taxonomy read the manifest: a cell is reused when its recorded fingerprint equals the current one and its output is present; `--force` ignores the manifest. A source-profile edit reaches parse through the profile stage (parse reads the profile stage's recorded fingerprint), which is the pipeline's stage order; the materialized `out/profile/<cell>/profile.json` is not an input of record. Language-model outputs are not reproducible, so a fingerprint identifies the intent of an output, not its bytes.
- **Taxonomy is per cell and fingerprint-cached per cell** (D-DRAFT-9 as amended by strand self-serve-ingestion, 2026-11-06; cache key replaced by the stage-graph fingerprint, 2026-11-08): `run_taxonomy` derives one taxonomy per `(MNO, release)` from that cell's trees alone — per-plan `<plan_id>_features.json`, the cell's `taxonomy.json` and its `extraction_state.json` ledger under `out/taxonomy/<mno>/<rel>/`. The cells are the parse output's cell directories narrowed by `--mno`/`--release` (the input dir need not be mounted). A release is therefore enriched against the taxonomy of its own text, and a build can hold one cell without the rest of the corpus. Because extraction is LLM-driven, expensive and run-to-run non-deterministic, each cell's derivation is gated on its stage fingerprint (the model, the code version, the cell's parse fingerprint, the MNO's overview file): an unchanged cell is served from its output; `--force` busts it; `stats.source` is `"cache"` only when every in-scope cell was cached. The per-MNO corpus-overview file (`$NORA_TAXONOMY_OVERVIEW_DIR/corpus_overview_<MNO>_<version>.txt`, highest version, strand sira-enrichment-pe) is a prompt input of that MNO's cells only, and the directory is passed to `FeatureExtractor` (restored 2026-11-08; dropped on 2026-11-06). A run that extracts with `NORA_TAXONOMY_OVERVIEW_DIR` unset warns `TAX-W003` (it used to pass silently; the cycle driver's enrich gate refuses on that code); a run served wholly from cache does not. A **build-level** `out/taxonomy/taxonomy.json` is consolidated over every cell present in the build after each run (the graph builder's and the review editor's input; in an incremental build it holds that build's cells); `corrections/taxonomy.json` replaces that build-level file only (`TAX-W002`), per-cell extraction still runs. Extraction runs at `temperature=0` (already in `FeatureExtractor`).
- **Taxonomy: plan-unit extraction, per-plan fail-soft + resume** (strand sira-enrichment-pe; the cross-release "newest release wins" selection was removed on 2026-11-06 — releases no longer supersede each other, `_select_newest_trees` and `_release_sort_key` remain as helpers): the unit of extraction is a **plan**, not a tree file. Each tree of the cell is split via `taxonomy.split_tree_by_plan` (chapter-per-plan docs → one subtree per plan); within a cell a plan carried by two documents keeps the later file name (the layout is one `<plan_id>_features.json` per plan per cell) and the other copy counts as `superseded`. Extraction is per-plan fail-soft: an LLM error or unparseable response (`LLMParseError`) marks that unit `failed` in the cell's `extraction_state.json` (keys: `<tree file name>` for single-plan trees, `<tree file name>#<plan_id>` for split ones; written after every unit — survives hard kills) and the run continues; unchanged `ok` units (same tree hash + same overview-prompt hash) are reused on re-run, and `failed` units retry automatically — re-running the same command *is* the retry mechanism. A partial run consolidates what succeeded, returns `WARN` (`TAX-W004`), and does **not** stamp that cell's fingerprint, so a degraded cell can never cache-lock itself; every unit of every cell failed is a `FAIL`; a cell with no parse trees is skipped with `TAX-W007`; a per-cell features file the build-level consolidation cannot read is skipped with `TAX-W008`. Ledger contract (strand corp-db-layer): launch logs `TAX-I001` ("N cached / M to extract") before any LLM call; all-units-stale warns `TAX-W005`; an overview-prompt fingerprint mismatch discards the ledger loudly (`TAX-W006`) instead of silently; the state file is also saved unconditionally after the unit loop, so a zero-extraction retry can never truncate it — a stamped stage therefore implies a complete ledger. Stale `*_features.json` with no matching selected plan (including a multi-plan doc's old empty-prefix `_features.json`) are deleted on re-derive.
- **Incremental ingestion: scope + skip** (D-DRAFT-8, skip keys replaced by the stage-graph fingerprints on 2026-11-08): `--mno` / `--release` (`PipelineContext.scope_mnos` / `scope_releases`, comma-separable) restrict which cells the **per-cell** stages process — `input_cells()` and `run_extract` filter by `cell_in_scope()`; global stages still read the whole union. Per-cell stages **skip-if-unchanged** by the manifest: `run_extract` reuses a whole cell whose fingerprint is recorded and unchanged (and still reuses an IR newer than its source within a cell it does process); `run_parse` reuses a whole cell whose fingerprint is unchanged and whose trees are all present, else re-parses every document of the cell (`RequirementTree.profile_fingerprint` is still stamped on each tree for the review UI, but it is no longer the skip key). `--force` (`ctx.force`) overrides the skip. So a re-run after dropping a new cell reprocesses only the new/changed cells while the global graph rebuilds over the union.
- **The store is write-once and a stage never writes through a shared inode** (strand stage-graph, step 2): a per-cell stage calls `Fingerprinter.prepare` before it writes a cell, which reuses (this build's record, then the store) or makes every file of the cell's directory that shares an inode or is read-only a private copy; outputs enter the store only on `stamp` of a clean cell, cloned under a temp name, made read-only and renamed. The build's `out/<stage>/<cell>/` stays the place every reader looks; a stored output appears there as a clone. Without `NORA_STAGES_ROOT` nothing is stored and the reuse is the build's own. With `NORA_CORPUS_DIR` the bindings and annotations are read from the corpus root.
- Stage order is fixed and matches `env.config.PIPELINE_STAGES`. Running a downstream stage without its prerequisites emits `PIP-E002` (required input missing) rather than silently failing later.
- `StageResult.status` uses four discrete values: `OK` (clean), `WARN` (completed but flagged), `FAIL` (aborted), `SKIP` (not run this invocation). Collapsing WARN into OK loses the signal that drives compact QC reports.
- **Embedder is resolved, never defaulted**: `PipelineContext.embedding_provider` / `embedding_model` default to `""`. `from_env()` and `standalone()` both resolve them through `env.config.resolve_embedding_provider` / `resolve_embedding_model` (CLI arg > `NORA_EMBEDDING_*` env var > `config/llm.json` > `EnvironmentConfig` field), so the web pipeline route and the CLI resolve the same way. An empty result makes the vectorstore stage refuse (`make_embedder` raises); there is no built-in sentence-transformers fallback.
- `PipelineContext.create_llm_provider()` falls back to `MockLLMProvider` when Ollama is unreachable unless `require_real=True`. This keeps offline stages runnable on work laptops without an LLM server.
- `PipelineContext.create_llm_provider()` is also the single site where the permanent-refusal fallback is applied (`llm/refusal.py`, `NORA_LLM_REFUSAL_MARKERS` + `NORA_LLM_FALLBACK_*`): every LLM-using stage and CLI that constructs its provider here inherits refusal coverage. Constructing a provider directly (bypassing this method) opts out — don't.
- Compact reports (`format_compact_report`) contain **no proprietary content** — only stage names, codes, counts, and timings. Verbose reports (separate function) may contain content and go to disk, not chat.

**Key choices**
- Plain functions per stage + dispatch dict instead of a class hierarchy — each stage is independently runnable from the CLI or tests without instantiating anything upstream.
- The stage graph is data, and the tier of a stage is derived by one rule rather than listed (strand stage-graph, D-DRAFT-1): a stage that gains a model call moves to the variant tier by definition, and `validate_graph` keeps the order equal to `PIPELINE_STAGES` and per-cell stages free of global inputs. The build manifest is `STAGES.json`, not `MANIFEST.json`, because the promoted snapshot under the labels root already has a `MANIFEST.json` of another shape.
- A recipe names variant stages only and is validated by a standard-library module (D-DRAFT-9): the host-side driver and runner import it with the host's python3. Its parameters for a stage enter that stage's fingerprint only when the build has a recipe, so builds without one keep their fingerprints. A model named in a recipe overrides the env file's for that stage; endpoints and keys never move into a recipe.
- Builds keep their `out/` tree and the store is cloned into it (D-DRAFT-8): about thirty modules outside the pipeline read that layout. Clones are hard links on one mount and copies across mounts (the batch containers mount the build and the store separately). The vector store and variant stages from an unversioned image stay out of the store. `--force` keeps its own output and leaves the store's first result.
- Stages stamp their own cells (the stage knows which cells completed cleanly) rather than the runner stamping after a stage returns: a degraded taxonomy run or a parse with one failed document must leave that cell unrecorded while the others are.
- Prefix-per-module error codes (EXT / PRF / PRS / PRV / RES / RSV / TAX / STD / GRF / VEC / EVL / GEV / OVL / TRS / HIS / PIP / ENV / MDL) — the prefix points the user at the right module without needing to read the message.
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
- `Cell` — class — pub — A `(MNO, release)` pair, e.g. ``Cell("VZW-OA", "Feb2026")``.
  - `relpath` — property — pub — The cell's directory fragment: ``<mno>/<release>``.
- `GLOBAL_STAGES` — constant — pub
- `PER_CELL_STAGES` — constant — pub
- `enumerate_input_cells` — function — pub — Scan ``input/<MNO>/<MMMYYYY>/`` → the cells present, validated + sorted.
- `is_per_cell_stage` — function — pub — True iff `stage` writes per-cell output (`out/<stage>/<mno>/<rel>/`).

`error_codes.py`
- `CODES` — constant — pub
- `ErrorDef` — dataclass — pub
  - `format` — method — pub — Format message with context variables.
- `PipelineError` — class — pub — Structured pipeline error with error code.
  - `__init__` — constructor — internal
- `_DEFS` — constant — internal

`fingerprints.py`
- `ABSENT` — constant — pub
- `CORPUS_VERSION_MODULES` — constant — pub
- `DEFAULT_VARIANT_CODE_VERSION` — constant — pub
- `FP_LEN` — constant — pub
- `Fingerprinter` — class — pub — Computes, compares and records stage fingerprints for one build.
  - `__init__` — constructor — internal
  - `_annotations` — method — internal — The user annotations (D-061) of the cell's documents.
  - `_documents` — method — internal
  - `_env_dir` — method — internal
  - `_overview_prompt` — method — internal — The MNO's corpus-overview prompt file the taxonomy extractor
  - `_profile` — method — internal — Hash of the cell's bound profile after placeholder substitution,
  - `_provenance` — method — internal
  - `cell_input_dir` — method — pub — The cell's directory under the documents root (MNO dir matched
  - `current` — method — pub
  - `externals` — method — pub
  - `forget` — method — pub — Drop the cell's entry (a degraded run) and release this build's
  - `in_store` — method — pub — True when `stage`'s per-cell outputs are found in and put into
  - `input_fps` — method — pub
  - `params` — method — pub — The stage's parameters: its spec's context fields and, when the
  - `prepare` — method — pub — Decide whether `stage` must compute `cell` under `fp`.
  - `recorded` — method — pub
  - `stamp` — method — pub — Record the current fingerprint (or `fp`) with its provenance and,
  - `unchanged` — method — pub — True when the recorded fingerprint equals the current one and
- `NOT_STORED` — constant — pub
- `ORIGIN_COMPUTED` — constant — pub
- `ORIGIN_FORCED` — constant — pub
- `ORIGIN_STORE` — constant — pub
- `UNRESOLVED` — constant — pub
- `VARIANT_CODE_VERSION_ENV` — constant — pub
- `canonical` — function — pub — Canonical JSON bytes (sorted keys, no whitespace).
- `cell_key` — function — pub — The manifest key: ``<MNO>/<release>`` for a per-cell stage, ``*`` for
- `code_version` — function — pub — A corpus stage's own ``CODE_VERSION``; the image version otherwise.
- `digest` — function — pub
- `hash_bytes` — function — pub
- `hash_files` — function — pub — Hash a file set: each file's path (relative to `rel_to`, else its
- `stage_fingerprint` — function — pub — The rule. `input_fps` values are strings (per-cell) or ``{cell: fp}``

`manifest.py`
- `BuildManifest` — class — pub — Read-modify-write view of one build's ``STAGES.json``.
  - `__init__` — constructor — internal
  - `_load` — method — internal
  - `entry` — method — pub
  - `fingerprints` — method — pub — ``{key: fp}`` for every recorded output of `stage`.
  - `forget` — method — pub — Drop the entry for (`stage`, `key`) if present and save.
  - `get` — method — pub — The recorded fingerprint, or None.
  - `record` — method — pub — Record `fp` for (`stage`, `key`) with its provenance and save.
  - `save` — method — pub
  - `stages` — method — pub
- `MANIFEST_NAME` — constant — pub
- `MANIFEST_VERSION` — constant — pub
- `_now_iso` — function — internal

`recipes.py`
- `DRIVER_STAGES` — constant — pub
- `PARAMS` — constant — pub
- `RECIPES_DIR` — constant — pub
- `RECIPE_FILE` — constant — pub
- `RESERVED_STAGES` — constant — pub
- `Recipe` — dataclass — pub — A validated recipe.
  - `digest` — property — pub — sha256 of the canonical JSON, 16 hex: two recipes with one
  - `enrich_uses_taxonomy` — property — pub — Whether enrich reads the cell's taxonomy: its `taxonomy` param,
  - `has` — method — pub
  - `param` — method — pub
  - `prompt_scheme` — property — pub
  - `stage_params` — method — pub — The stage's parameters with `generation` filled in (default 1);
  - `to_dict` — method — pub
- `RecipeError` — class — pub — A recipe that cannot be read or is not valid (RCP-E001 / RCP-E002).
  - `__init__` — constructor — internal
- `SERVE_MODES` — constant — pub
- `SERVE_UNITS` — constant — pub
- `_NAME_RE` — constant — internal
- `_TOP_KEYS` — constant — internal
- `_bad` — function — internal
- `_canonical` — function — internal
- `get` — function — pub — One value as text for the shell: ``name``, ``digest``, ``stages``
- `load` — function — pub — Read and validate a recipe file. `check_name` requires the name to
- `load_frozen` — function — pub — The recipe frozen in a build (``<build>/RECIPE.json``), or None.
- `main` — function — pub
- `parse` — function — pub — Validate `data` (the parsed JSON) and return the `Recipe`.
- `recipe_path` — function — pub

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
- `lane_bounds` — function — pub — Map a --lane alias to its (start, end) stage names (PIPELINE_LANES).
- `logger` — constant — pub
- `main` — function — pub

`runner.py`
- `CORPUS_DIR_ENV` — constant — pub
- `PipelineContext` — dataclass — pub — Shared context passed through all pipeline stages.
  - `_construct_llm_provider` — method — internal — Provider construction proper (no refusal wrap).
  - `_resolve_model` — method — internal — Resolve 'auto' model name using hardware detection.
  - `annotations_dir` — property — pub — The user annotations (D-061) the parse stage applies.
  - `bindings_dir` — property — pub — Where `profiles.json` is read: the corpus root, else the build.
  - `build_id` — property — pub — The build's name (its directory name), as recorded in the store.
  - `cell_in_scope` — method — pub — True iff `(mno, release)` passes the `--mno`/`--release` scope (D-DRAFT-8).
  - `correction` — method — pub — Get a correction file path if it exists.
  - `create_llm_provider` — method — pub — Create an LLM provider based on config, wrapped with the
  - `env_dir` — property — pub — The build / environment directory (`<env_dir>`).
  - `fingerprints` — property — pub — The build's `Fingerprinter` over `<env_dir>/STAGES.json` (lazy).
  - `from_env` — classmethod — pub — Create context from an EnvironmentConfig.
  - `input_cells` — method — pub — The in-scope `(MNO, release)` cells present under `input/`, validated + sorted.
  - `profile_override` — method — pub — The single-profile override applied to every cell, if any:
  - `recipe` — property — pub — The recipe frozen in the build (``<env_dir>/RECIPE.json``), or
  - `stage_output` — method — pub — Get the output directory for a stage.
  - `stage_store` — property — pub — The host's `StageStore`, or None without `stages_root` (lazy).
  - `standalone` — classmethod — pub — Create context for standalone (no EnvironmentConfig) mode.
- `PipelineRunner` — class — pub — Orchestrates pipeline stage execution.
  - `__init__` — constructor — internal
  - `run` — method — pub — Run the specified stages in order.
- `STAGES_ROOT_ENV` — constant — pub
- `_env_path` — function — internal
- `logger` — constant — pub

`stage_graph.py`
- `CORPUS` — constant — pub
- `CORPUS_PREFIX` — constant — pub
- `CORPUS_STAGES` — constant — pub
- `GLOBAL_KEY` — constant — pub
- `SPECS` — constant — pub
- `STAGE_GRAPH` — constant — pub
- `STAGE_ORDER` — constant — pub
- `StageSpec` — dataclass — pub — One stage's node in the graph.
  - `per_cell` — property — pub
- `VARIANT` — constant — pub
- `VARIANT_STAGES` — constant — pub
- `spec_of` — function — pub — The spec of `stage`; `KeyError` for an unknown name.
- `tier_of` — function — pub — ``"corpus"`` or ``"variant"``, by the tier rule.
- `validate_graph` — function — pub — Structural checks; an empty list means the graph is well formed.

`stage_store.py`
- `DEFAULT_WAIT_SECONDS` — constant — pub
- `POLL_SECONDS` — constant — pub
- `StageStore` — class — pub — One host's stage store rooted at `root`.
  - `__init__` — constructor — internal
  - `_foreign_marker` — method — internal — A live marker of another build: present, not ours, younger than
  - `cell_dir` — method — pub
  - `commit` — method — pub — Put the complete output at `src` into the store under `fp`.
  - `has` — method — pub — True when a complete output for `fp` is in the store (the rename
  - `link_view` — method — pub — Make `view` (a build's cell directory) a clone of the stored
  - `mark_building` — method — pub — Announce that `build` computes `fp`. Best effort: a marker that
  - `marker_path` — method — pub
  - `path` — method — pub
  - `provenance` — method — pub
  - `provenance_path` — method — pub
  - `release` — method — pub — Remove the marker if `build` owns it.
  - `wait_for` — method — pub — Wait while another build computes `fp`. Returns True when the
- `WAIT_SECONDS_ENV` — constant — pub
- `_LINK_FALLBACK` — constant — internal
- `_WRITE_BITS` — constant — internal
- `_files` — function — internal
- `_link_or_copy` — function — internal
- `_now_iso` — function — internal
- `_remove_tree` — function — internal
- `clone_tree` — function — pub — Recreate `src` at `dst` (which must not exist): directories made,
- `make_readonly` — function — pub — Clear the write bits of every file under `tree` (directories keep
- `privatize` — function — pub — Give every file under `tree` that shares its inode (or is read-only)

`stages.py`
- `STAGE_FUNCS` — constant — pub
- `StageResult` — dataclass — pub — Result from running a single pipeline stage.
  - `ok` — property — pub
- `_MONTHS` — constant — internal
- `_RELEASE_RE` — constant — internal
- `_cached_unit_keys` — function — internal — Ledger keys of units the extraction loop will serve from cache:
- `_cell_extract_hints` — function — internal — Per-cell PDF extractor hints from each in-scope cell's bound profile
- `_fail` — function — internal
- `_load_taxonomy_ledger` — function — internal — Load extraction_state.json's per-doc ledger, honored only when its
- `_load_user_eval_questions` — function — internal — Load user-supplied evaluation questions from Excel files.
- `_release_sort_key` — function — internal — Chronological sort key for MMMYYYY release names.
- `_select_newest_trees` — function — internal — Newest instance of each (MNO, plan_id) across releases.
- `_split_csv` — function — internal — Split comma-separated string into list, stripping whitespace.
- `logger` — constant — pub
- `run_eval` — function — pub — Run evaluation.
- `run_extract` — function — pub — Extract documents into normalized IR.
- `run_graph` — function — pub — Build the knowledge graph.
- `run_parse` — function — pub — Parse each cell's IRs into requirement trees (D-DRAFT-6/7).
- `run_profile` — function — pub — Resolve + materialize each cell's parse profile (D-DRAFT-7).
- `run_resolve` — function — pub — Resolve cross-references across parsed trees.
- `run_standards` — function — pub — Ingest referenced 3GPP standards.
- `run_taxonomy` — function — pub — Extract the feature taxonomy of each in-scope cell from its parsed trees.
- `run_vectorstore` — function — pub — Build one vector store PER CELL (D-DRAFT-11).
<!-- END:STRUCTURE -->

**Depends on**
[env](../env/MODULE.md), [extraction](../extraction/MODULE.md), [profiler](../profiler/MODULE.md), [parser](../parser/MODULE.md), [resolver](../resolver/MODULE.md), [taxonomy](../taxonomy/MODULE.md), [standards](../standards/MODULE.md), [graph](../graph/MODULE.md), [vectorstore](../vectorstore/MODULE.md), [eval](../eval/MODULE.md), [query](../query/MODULE.md) (lazy, in stages.py — the eval and query stages), [models](../models/MODULE.md), [llm](../llm/MODULE.md) (via `PipelineContext.create_llm_provider`). (`corrections` is not imported: the corrections files are read as data under `<env_dir>/corrections/`; drift-check 2026-11-08.)

**Depended on by**
[web](../web/MODULE.md) (submits jobs via the pipeline runner), [llm](../llm/MODULE.md) (`llm_debug` builds a `PipelineContext`; llm and pipeline import each other — pipeline's imports of llm are lazy, inside functions), [taxonomy](../taxonomy/MODULE.md) (`tax_debug`), [profile_miner](../profile_miner/MODULE.md) (`profile_miner_cli`), [stores](../stores/MODULE.md) (`overlay_import` / `trees_build` / `history_build` raise `PipelineError` with `OVL-` / `TRS-` / `HIS-` codes), [query](../query/MODULE.md) (`release_diff` raises `PipelineError`).

**Deferred**
- Declare `models` and `query` in Depends on (deferred: imports are thin — `DocumentIR` for type, `query.pipeline`/`query.synthesizer` for the eval stage — and pipeline's Depends on list is already long — revisit: during a Depends-on audit or if the imports deepen)
