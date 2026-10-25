# store_service

**Purpose**
The `nora-store` process: the one service in a serving stack that holds corpus data. It loads the Cells store (flat SIRA cell directories in dev, cell DBs, or — served stacks — the publish registry's active release set), composes the Overlay onto every cell at load, answers retrieval (`POST /query`: scope resolution, query expansion, per-cell BM25, rerank, fusion), serves the review surface (`/cells*`), hosts the four store routers under `/stores/<store>/v1` (D-DRAFT-28, corp-db-layer) and is the Overlay's single writer. Moved from the top level (`nora_store`) to `core/src` on 2026-10-25 so the structure rule holds: it imports only core. Design and flows: `docs/architecture.md` §7, §22, §23.

**Public surface**
- ASGI app `core.src.store_service.service:app` (the image CMD). Routes: `POST /query` (`{query, top_k?, label?, text_chars?}` → ranked rows with `(mno, release)` provenance, `resolved_cells`, `unresolved`, timings); `GET /cells?label=`; `GET /cells/{cell}/plans?label=`; `GET /cells/{cell}/enrichments?plan&req_id&label`; `POST /cells/{cell}/reload?label=` (409 for a schema-1 cell DB); `POST /registry/reload` (`{release_set, changed, prompts_loaded, missing_prompts}`); `GET /healthz` (identity, health, stores, knobs, prompts — key list in `docs/architecture.md` §7.3); the hosted routers `/stores/cells/v1` (`stores_api.py`: `cells`, `/{mno}/{rel}/meta|rows|phrases|effective`) and `/stores/{trees,history,overlay}/v1` (`core.src.stores.api`, mounted by `stores_api.mount(app)`).
- Env (read at import): `NORA_STORE_REGISTRY` / `NORA_STORE_RELEASE_SET` (registry mode; implies cell-db), `NORA_STORE_ROOT`, `NORA_STORE_INDEX` (`flat` | `cell-db`), `NORA_STORE_ENGINE` (`bm25x` | `fts5`), `NORA_OVERLAY_URL` | `NORA_OVERLAY_DB` | `NORA_STORE_CORRECTIONS_ROOT` (Overlay backend, in that order), `NORA_TREES_ROOT`, `NORA_HISTORY_ROOT`, `NORA_ENV_DIR`, `NORA_LLM_SHIM_URL` / `NORA_LLM_MODEL` / `NORA_LLM_SHIM_API_KEY` (expansion), `NORA_SIRA_RERANK_*`, `NORA_SIRA_QUERY_FALLBACK_LLM_*` + `NORA_LLM_REFUSAL_MARKERS`, `NORA_SIRA_{TOP_K,RERANK_TOP_N,MAX_DF_RATIO,EXPANSION_WEIGHT,QUERY_ENRICH_ENABLED,FUSION_BALANCED,SCALE_TOPK_BY_CELLS,FANOUT_*}`, `NORA_SIRA_PROMPTS_FROM` / `NORA_SIRA_{QUERY_ENRICH,RERANK,DOC_ENRICH}_RUN` / `NORA_SIRA_USE_LATEST_RUNS` / `NORA_SIRA_CLONE_ROOT` (prompt sources), `NORA_QUERY_ANALYZER` + `NORA_DECISION_*`, `SIRA_PROMPT_SCHEME`, `NORA_CODE_VERSION`. Full table: `docs/architecture.md` §19.2.
- `cell_db_store.py`: `load_cell_db(path) -> CellDb` (schema 1 or 2; read-only immutable open; index blobs extracted to a temp dir), `fts5_search`, `CellDbError`.
- `enrich_overlay.py` (stdlib): `apply_overlay_to_req(llm_words, entry, verdict_fn) -> (effective, held, suppressed)` — the fold: effective = `suppress_all` ? adds : (llm − removes) + adds; remove wins unless the add is strictly newer; `make_verdict_fn(...)` — fingerprint equal → ok, same origin release → ok, vanilla-token Jaccard ≥ 0.85 → ok, else held; `load_overlay`; re-exports `allowed_labels`, `filter_overlay` from `core.src.stores.overlay_ops`.
- `scope.py`: `normalize_release(text) -> MMMYYYY | "latest" | None`, `resolve_cells(intent, available) -> (resolved: set[CellKey], unresolved: list)` — named MNOs or all; no release → each MNO's latest; unparseable or uningested requests land in `unresolved`, never dropped.
- `fusion.py`: `Candidate` (comp_id carries mno, release, doc_id), `merge_candidates(cells, retrieve_fn, top_n)` (per cell, no cross-cell dedup), `rank_candidates(pool, rerank_scores, balanced, n_cells)` (global by score; round-robin when rerank is off, degenerate, or balanced with several cells).
- CLI `python -m core.src.store_service.sira_debug {env, corpus, phrases, query} [--filter] [--rerank] [--top-n]` — offline inspector over the same loaders.

**Invariants**
- One registry resolution per load / reload (`_registry_resolve`, cached): Cells, Trees, History and `/healthz` report the same set until the next reload; a flip in progress is never visible as a mixed set.
- The Overlay is composed in memory; the on-disk index and the cell files are never written (`disable_auto_save` after every BM25 load — D-DRAFT-7; cell DBs opened `mode=ro&immutable=1`).
- When this process is the Overlay authority (local backend opened read-write) every write to `/stores/overlay/v1` runs in one backend transaction; otherwise writes answer 409 and the Overlay is read from `NORA_OVERLAY_URL`.
- Reload swaps a cell under a per-cell lock; in-flight queries finish on the old state. A labeled reload rebuilds only that label's variant; a default-view reload drops every variant of the cell.
- `sira_prompt_scheme` on `/healthz`: `SIRA_PROMPT_SCHEME` env, else the served release set's `prompt_scheme`, else the label manifest.
- `ok` on `/healthz` is false when a configured prompt is missing or a load failed; identity keys are computed at load, never per request.
- Imports only `core` (`stores`, `llm.refusal`, `query.analyzer`). Never `sandbox`: the cell-name helpers live in `core.src.stores.cells`, the refusal twin in `core.src.llm.refusal`.
- No requirement text in logs or error payloads; `/cells/{cell}/enrichments` is the one route that returns requirement text, to the review page.

**Key choices**
- Composition at load, no cache (D-DRAFT-23); schema-2 cell files hold the vanilla index and raw phrases only, so a file is a function of the build and the Overlay changes without a rebuild.
- Scope extraction uses the same `QueryType` analyzer as the web (`make_query_analyzer_from_knob`); retrieval does not vary by query type, scope and synthesis do.
- Expansion once per query, DF-filtered per cell against that cell's `max_df`; rerank over the merged pool with each cell's corpus text; balanced fusion keeps every resolved cell represented in a multi-MNO answer.
- The service derives the SIRA clone root and the repo root from its own file (`parents[3]`); `test_store_service_package_paths` pins that after two package moves.

**Non-goals**
- No answer synthesis: nora-web calls the language model for the answer (select-synth) or runs the NORA synthesizer over pinned rows.
- No writes to the registry (publish CLI only) or to the cell, trees and history files.
- No cells over HTTP as a store protocol: the index lives in this process; separating Cells needs an index-blob endpoint.
- No web runtime DBs (jobs, metrics, feedback, config) — deferred to a later strand.

**Depends on**
[stores](../stores/MODULE.md) (`overlay`, `overlay_ops`, `api`, `registry`, `trees` / `history` factories, `cells`), [llm](../llm/MODULE.md) (`refusal`: markers, permanent-refusal detection, fallback reasoning fields), [query](../query/MODULE.md) (`analyzer.make_query_analyzer_from_knob`, `MockQueryAnalyzer`), [extraction](../extraction/MODULE.md) (release ordering through `stores.cells`). Runtime only: the `bm25x` wheel and the SIRA clone's query and rerank prompts carried by the store image; FastAPI, httpx, pydantic.

**Depended on by**
[web](../web/MODULE.md) over HTTP only (`NORA_STORE_URL`, the store clients); the converter `sandbox/cell_db_convert.py` (`enrich_overlay` fold for its identity gate, `cell_db_store` reader); `core/src/stores/publish.py` (`verify_serving` calls `/registry/reload` and `/healthz`); the golden runner (`/query`, `/healthz`); `docker/store.Dockerfile` (`nora-store`, `nora-store-test`).

**Deferred**
- Per-cell query and rerank prompt selection (one pair is loaded today).
- Plan-aware doc/section fan-out on the multi-cell path (legacy single-dataset path only).
- An LLM-backed scope analyzer wired to the service's own HTTP client (the `llm` arm reports as `rules` here).
- `cell_db_store.py`, the fold in `enrich_overlay.py` and the `/stores/cells/v1` router move to `stores` when the `CellStore` protocol lands (TODO in `stores/MODULE.md` → Deferred).

<!-- BEGIN:STRUCTURE -->
_Alphabetical, regenerated by regen-map._


`cell_db_store.py`
- `CellDbData` — dataclass — pub — Everything the service needs from one cell DB file.
- `CellDbError` — class — pub — Cell DB cannot be served (bad schema, missing table, undecodable blob).
- `SUPPORTED_SCHEMAS` — constant — pub
- `_fts_query` — function — internal — Quoted-token OR query; internal quotes doubled per FTS5 syntax.
- `_fts_scores` — function — internal — One MATCH pass -> {doc: score}, with bm25()'s negative-is-better
- `fts5_search` — function — pub — The fts5 engine's retrieval: two MATCH passes combined as
- `load_cell_db` — function — pub — Read one cell DB: meta (schema-checked), corpus rows in doc order,
- `open_ro` — function — pub — Read-only, immutable open — write-once is enforced, not assumed.

`enrich_overlay.py`
- `JACCARD_THRESHOLD_DEFAULT` — constant — pub
- `OverlayResult` — dataclass — pub — The fold's output for one requirement.
- `VerdictFn` — constant — pub
- `_record_state` — function — internal — 'applied' | 'held' | 'excluded' for one record. `allowed` is the
- `apply_overlay_to_req` — function — pub — Fold one requirement's records into its effective enrichment set.
- `jaccard` — function — pub — Token-set Jaccard; both empty -> 1.0 (nothing changed about nothing).
- `load_accepted_labels` — function — pub — Labels merged into "main" — the default view everyone gets. The
- `load_overlay` — function — pub — Load `<root>/sira-enrich/<MNO>.json`; missing/malformed -> {} (loud log
- `make_verdict_fn` — function — pub — Build the cross-release guard from a token-set source.
- `sira_enrich_dir` — function — pub

`fusion.py`
- `Candidate` — dataclass — pub — A retrieved chunk tagged with its origin cell (provenance).
  - `comp_id` — property — pub
  - `mno` — property — pub
  - `release` — property — pub
- `CompId` — constant — pub
- `Hit` — constant — pub
- `RerankFn` — constant — pub
- `RetrieveFn` — constant — pub
- `_rerank_sorted` — function — internal — A cell's candidates in descending rerank-score order (None last).
- `_round_robin` — function — internal — Interleave per-cell lists position-by-position (cell 0 rank 0,
- `fuse` — function — pub — Synchronous retrieve -> merge -> rerank -> rank convenience wrapper.
- `merge_candidates` — function — pub — Retrieve up to `per_cell_top_n` from each cell and tag with
- `rank_candidates` — function — pub — Rank the merged pool and return top_k.

`scope.py`
- `_IntentLike` — class — internal — The slice of QueryIntent that scope resolution reads.
- `_MONTHS` — constant — internal
- `normalize_release` — function — pub — Map a human release phrase to a cell label, or None if unparseable.
- `resolve_cells` — function — pub — Resolve a QueryIntent to target cells + the requested-but-missing list.

`service.py`
- `CellState` — dataclass — pub — Per-(MNO, release) cell: its own BM25 index + corpus + DF stats.
  - `vanilla_tokens` — method — pub — VANILLA index token set for a req (the fingerprint basis —
- `_CELL_STATS_CACHE` — constant — internal
- `_CORR_ROOT` — constant — internal
- `_DATASET` — constant — internal
- `_DB_ROOT` — constant — internal
- `_DEFAULT_TOP_K` — constant — internal
- `_DOC_ENRICH_RUN` — constant — internal
- `_ENGINE` — constant — internal
- `_ENRICH_MODEL_NAME` — constant — internal
- `_ENV_DIR` — constant — internal
- `_EXPANSION_WEIGHT` — constant — internal
- `_FALLBACK_LLM_API_KEY` — constant — internal
- `_FALLBACK_LLM_MODEL` — constant — internal
- `_FALLBACK_LLM_URL` — constant — internal
- `_FALLBACK_REASONING_FIELDS` — constant — internal
- `_FALLBACK_STATS` — constant — internal
- `_FANOUT_ENABLED` — constant — internal
- `_FANOUT_PER_HIT` — constant — internal
- `_FINGERPRINTS_NAME` — constant — internal
- `_FUSION_BALANCED` — constant — internal
- `_HISTORY_ROOT` — constant — internal
- `_INDEX_MODE` — constant — internal
- `_MAX_DF_RATIO` — constant — internal
- `_OVERLAY_DB` — constant — internal
- `_OVERLAY_URL` — constant — internal
- `_PLAN_LINE_RE` — constant — internal
- `_QUERY_ENRICH_ENABLED` — constant — internal
- `_QUERY_ENRICH_RUN` — constant — internal
- `_QUERY_ENRICH_TEMPERATURE` — constant — internal
- `_QUERY_PROMPT_PATH` — constant — internal
- `_REFUSAL_MARKERS` — constant — internal
- `_REGISTRY` — constant — internal
- `_RELEASE_SET` — constant — internal
- `_RERANK_BACKEND` — constant — internal
- `_RERANK_BATCH_MAX_TOKENS` — constant — internal
- `_RERANK_BATCH_SIZE_RAW` — constant — internal
- `_RERANK_BULK_DEFAULT_BATCH` — constant — internal
- `_RERANK_ENABLED` — constant — internal
- `_RERANK_LLM_API_KEY` — constant — internal
- `_RERANK_LLM_MODEL` — constant — internal
- `_RERANK_LLM_URL` — constant — internal
- `_RERANK_MAX_TOKENS` — constant — internal
- `_RERANK_PROMPT_PATH` — constant — internal
- `_RERANK_RUN` — constant — internal
- `_RERANK_TOP_N` — constant — internal
- `_SCALE_TOPK_BY_CELLS` — constant — internal
- `_SHIM_API_KEY` — constant — internal
- `_SHIM_MODEL` — constant — internal
- `_SHIM_URL` — constant — internal
- `_SIRA_CLONE_ROOT` — constant — internal
- `_STORES` — constant — internal
- `_STORES_PATHS` — constant — internal
- `_SiraQueryRequest` — class — internal
- `_TREES_ROOT` — constant — internal
- `_USE_LATEST` — constant — internal
- `_apply_overlay_and_enrich` — function — internal — Fold the corrections overlay into the cell's enrichments and apply
- `_bm25` — constant — internal
- `_build_retrieve_fn` — function — internal — retrieve_fn(cell, k) -> [(doc_id, bm25)] for `merge_candidates`.
- `_candidate_doc_texts` — function — internal — [(cand, doc_text)] for candidates whose doc is present in its cell's
- `_cell_is_composed` — function — internal
- `_cells` — constant — internal
- `_cells_load_error` — constant — internal
- `_code_version` — function — internal — Serving-code version: image-baked env first, git for source runs.
- `_compute_identity` — function — internal — Fill the identity globals from the loaded serving state. Called
- `_corpus_by_id` — constant — internal
- `_data_fingerprint` — constant — internal
- `_data_fingerprint_cells` — constant — internal
- `_doc_enrich_applied_docs` — constant — internal
- `_doc_enrich_source` — constant — internal
- `_doc_id_to_idx` — constant — internal
- `_doc_ids` — constant — internal
- `_enrich_model_of_run` — function — internal — Model name recorded in a doc-enrich run's config.json (the full
- `_extract_referenced_req_ids` — function — internal — Pull req_ids out of a doc/section row's text body.
- `_fanout_reranked` — function — internal — Expand doc/section pointer rows into their constituent req-level
- `_format_batch_rerank_prompt` — function — internal — Build a batch-scoring prompt for `docs = [(local_id, text), ...]`.
- `_fresh_cell` — function — internal — A newly loaded, not-yet-composed state for `ck` from its source: the
- `_get_variant` — function — internal — Resolve the serving state for a label view. "" -> the default
- `_history_store` — function — internal
- `_is_pointer_row` — function — internal — True for doc-level / section-level rows whose text body is a
- `_label_cells` — constant — internal
- `_llm_call` — function — internal — One OpenAI-shaped chat-completion call. Routes to the shim by
- `_load_cell_prompts` — function — internal — Multi-cell prompt load. Flat cells: the first cell's runs/ (prompt
- `_load_cells` — function — internal — Populate `_cells` from `<db_root>` — flat cell dirs by default, or
- `_load_error` — constant — internal
- `_load_one_cell` — function — internal — Load one cell's BM25 index + corpus + doc-enrichment into a
- `_load_one_cell_db` — function — internal — Load one cell from its SQLite cell DB.
- `_load_prompts` — function — internal — Load the service-level query-enrich + rerank prompt templates.
- `_load_state` — function — internal — Load BM25 index + corpus + prompts. Called once on first use.
- `_max_df_absolute` — constant — internal
- `_missing_prompts` — function — internal — Prompts the configuration needs but the service did not load: the
- `_multi_cell_query` — function — internal — Multi-MNO query path: analyze -> resolve scope -> retrieve per cell
- `_overlay_digest` — function — internal — Canonical content digest of the APPLIED view: the overlay filtered
- `_overlay_store` — function — internal — The Overlay store for this process. NORA_OVERLAY_URL → the peer
- `_parse_batch_scores` — function — internal — Extract `{id: score}` pairs from a batch rerank LLM response.
- `_parse_cell_or_404` — function — internal
- `_parse_phrases` — function — internal — Mirror sira.llm.parse_phrases — pull `keywords` list from a
- `_parse_rerank_response` — function — internal — Extract `{index: score}` from a cross-encoder /rerank response.
- `_parse_score` — function — internal — Pull integer `score` from a JSON object in the LLM response.
- `_pending_plans` — function — internal — Plans whose reqs have overlay edits not yet applied to the `label`
- `_plan_matches` — function — internal — `doc:`/`section:` composite rows stamp `plan_id / plan_name` (a
- `_plan_of` — function — internal
- `_query_prompt_source` — constant — internal
- `_query_prompt_template` — constant — internal
- `_read_fingerprints` — function — internal — `{_id, fingerprint}` per line; absent sidecar (pre-S1 builds) -> {}.
- `_read_serve_manifest` — function — internal — Best-effort MANIFEST.json lookup: at the db_root, then one level
- `_read_view_overlay` — function — internal — (overlay filtered to the `label` view, accepted labels). No store
- `_registry_label` — constant — internal
- `_registry_lock` — constant — internal
- `_registry_paths` — function — internal — `{unit: file}` of one store in the served set (sorted by unit).
- `_registry_prompt_scheme` — constant — internal
- `_registry_resolve` — function — internal — `{store: {unit: StoreVersion}}` of the registry's served set. Read
- `_registry_units` — constant — internal
- `_registry_versions` — constant — internal
- `_reload_locks` — constant — internal
- `_reload_locks_guard` — constant — internal
- `_rerank_bulk` — function — internal — Score candidates via a dedicated cross-encoder /rerank endpoint (D-DRAFT-14),
- `_rerank_candidates` — function — internal — rerank_fn for `fuse`: score the merged candidate pool -> {comp_id: score}.
- `_rerank_one_batch` — function — internal — POST one sub-batch of `[(cand, doc_text), …]` to the /rerank endpoint and
- `_rerank_prompt_source` — constant — internal
- `_rerank_prompt_template` — constant — internal
- `_resolve_run_dir` — function — internal — Resolve a per-stage run directory.
- `_serve_manifest` — constant — internal
- `_sha256_files` — function — internal — Streamed sha256 over the concatenated bytes of *paths* (absent
- `_short_sha` — function — internal — 12-hex sha256 of a prompt's content; "" when not loaded.
- `_startup` — function — internal
- `_stores_health` — function — internal — Which backend each hosted store runs on, for /healthz.
- `_tree_store` — function — internal — Trees over NORA_TREES_ROOT (SQLite cell DBs), else the registry's
- `app` — constant — pub
- `cell_enrichments` — function — pub — Per-req enrichment data for the review UI: LLM output, the applied
- `cell_plans` — function — pub
- `cell_reload` — function — pub — Re-read the cell from disk (vanilla index + corpus + enrichments +
- `cells_inventory` — function — pub — Per-cell corpus inventory for UI surfaces (the web test page's
- `healthz` — function — pub
- `logger` — constant — pub
- `registry_reload` — function — pub — Re-read the publish registry's served set and swap in the units
- `sira_query` — function — pub — Run the full SIRA pipeline on a single query and return the

`sira_debug.py`
- `_DATASET` — constant — internal
- `_DB_ROOT` — constant — internal
- `_EXPANSION_WEIGHT` — constant — internal
- `_MAX_DF_RATIO` — constant — internal
- `_QUERY_PROMPT_PATH` — constant — internal
- `_RERANK_PROMPT_PATH` — constant — internal
- `_RERANK_TOP_N_DEFAULT` — constant — internal
- `_SERVICE_URL` — constant — internal
- `_SHIM_MODEL` — constant — internal
- `_SHIM_URL` — constant — internal
- `_SIRA_CLONE_ROOT` — constant — internal
- `_llm_call` — function — internal
- `_load_bm25` — function — internal
- `_load_corpus` — function — internal
- `_load_doc_phrases` — function — internal
- `_load_prompt` — function — internal
- `_parse_phrases` — function — internal
- `_parse_score` — function — internal
- `_paths` — function — internal
- `_run_query_trace` — function — internal
- `cmd_corpus` — function — pub
- `cmd_env` — function — pub
- `cmd_phrases` — function — pub
- `cmd_query` — function — pub
- `cmd_req` — function — pub
- `cmd_service` — function — pub
- `logger` — constant — pub
- `main` — function — pub

`stores_api.py`
- `cells_router` — function — pub — Over the service's loaded cells: `get_cells()` is the `_cells`
- `mount` — function — pub — Mount the four routers; the store getters are called per request
<!-- END:STRUCTURE -->
