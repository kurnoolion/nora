# env

**Purpose**
Per-environment scoped workspace configuration. An environment names a workspace (one team member × one `env_dir` × a stage range × MNO/release scope × objectives) so multiple contributors can run partial pipelines in parallel without stepping on each other's outputs. Serves FR-28 (env_dir CLI/config/UI parameterization), FR-29 (single-root partition layout); implements D-022 (per-env directory layout).

**Public surface**
- `EnvironmentConfig` (config.py) — the dataclass: `name`, `description`, `created_by`, `member`, `env_dir`, `requirements_dir` (documents-root override, D-DRAFT-6 docker-distro), `stage_start/end`, `mnos`, `releases`, `doc_types`, `objectives`, `model_provider/name/timeout`, `embedding_provider/model`, `standards_source`, `skip_taxonomy`, `skip_graph`; exposes `save_json()`, `load_json()`, `validate()`, `active_stages`, `env_dir_path`, `path(key)`, `input_path(mno, release)`, `input_root` (the effective documents root: `requirements_dir` when set, else `<env_dir>/input`), `out_path(stage)`, `state_path()`, `corrections_path()`, `correction_file(artifact)`, `reports_path()`, `eval_path()`, `init_directories()`
- `ProfileBindings` (profile_bindings.py, D-DRAFT-7) — per-cell profile resolution from `<env_dir>/profiles.json`. `ProfileBinding(mno, release, profile)` rows; `load_profile_bindings(env_dir, override=None) -> ProfileBindings`; `resolve(mno, release) -> Path` with precedence `--profile override > exact (mno,release) > (mno,"*") > default > fail-loud (PIP-E003)`; `covers(mno, release)` / `uncovered(cells)` for coverage validation. MNO match case-insensitive; release exact with `"*"` wildcard. Lives in `env` (not `pipeline`) to avoid the `pipeline → env` import cycle
- `LLMConfigFile` (config.py) — schema for `config/llm.json`: `llm_provider`, `llm_model`, `llm_timeout`, `llm_base_url`, `llm_api_key`, `embedding_provider`, `embedding_model`, `ollama_url`, `ollama_timeout_s`, `skip_taxonomy`, `skip_graph`. Empty/zero values fall through. `load(path=None)` with malformed/missing tolerance.
- Registry constants: `PIPELINE_STAGES`, `PIPELINE_LANES` (D-DRAFT-5 docker-distro — named stage ranges: `ingestion` = extract..standards, `nora` = taxonomy..eval; consumed by `run_cli --lane`), `STAGE_NAMES`, `STAGE_NUM`, `NUM_STAGE`, `STAGE_DESC`, `ENV_DIR_DIRS`, `DEFAULT_LLM_CONFIG_PATH`
- Resolvers (3-tier — CLI > env var > config/llm.json > env-config back-compat > default):
  - `resolve_llm_provider(cli, env_cfg)` — `--llm-provider` / `NORA_LLM_PROVIDER` / `llm_provider`
  - `resolve_embedding_provider(cli, env_cfg)` — `--embedding-provider` / `NORA_EMBEDDING_PROVIDER` / `embedding_provider`
  - `resolve_embedding_model(cli, env_cfg)` — `--embedding-model` / `NORA_EMBEDDING_MODEL` / `embedding_model`
  - `resolve_standards_source(cli, env_cfg)` — `--standards-source` / `NORA_STANDARDS_SOURCE` / env config
  - `resolve_skip_taxonomy(cli, env_cfg)` — `--skip-taxonomy` or `--rag-only` / `NORA_SKIP_TAXONOMY` or `NORA_RAG_ONLY` / `skip_taxonomy`
  - `resolve_skip_graph(cli, env_cfg)` — `--skip-graph` or `--rag-only` / `NORA_SKIP_GRAPH` or `NORA_RAG_ONLY` / `skip_graph`
- `resolve_requirements_dir(cli, env_cfg)` — documents-root override (D-DRAFT-6 docker-distro): `--requirements-dir` / `NORA_REQUIREMENTS_DIR` (`REQUIREMENTS_DIR_ENV_VAR`) / env-config `requirements_dir` / default `<env_dir>/input`
- `resolve_stage(value)` — accepts either stage name or 1-based number, returns canonical name
- `env_cli.main` — CLI: `stages | create | list | show | init | delete`

- `resolve_providers()` / `resolve_provider(provider_id)` (config.py) — the optional named provider roster from `config/llm.json` (`LLMProviderEntry`: id, name, base_url, model, api_key_env, supports_reasoning_control, default_mode). `resolve_providers()` returns `[]` when no roster is configured — the normal case, and the signal to callers to use the single-provider chain unchanged. `resolve_provider()` falls back to the first entry for an empty or unknown id, so a stale bookmark degrades instead of failing a request. No env/CLI tier: a roster is a set of named endpoints, which is file-shaped, not a flag

**Invariants**
- `supports_reasoning_control` says the endpoint **honours the knob**, not that the model reasons — a thinking model with no exposed control is `False`, because nothing we send changes how much it thinks. It is **declared, never detected**. No OpenAI-compatible endpoint advertises the capability, and probing only catches outright rejection — a server can accept `reasoning_effort` and silently ignore it, which is indistinguishable from honouring it. Declaring it is what lets the UI avoid offering a control that would do nothing.
- A malformed roster entry is dropped with a warning, never fatal — a half-written `providers` list must not take the app down, and the warning is what makes the omission visible.

- `PIPELINE_STAGES` is the **single source of truth** for stage names and ordering across the project; any other module listing stages must import from here.
- `env_dir` layout is fixed (D-022): `input/<MNO>/<release>/`, `out/<stage>/`, `state/`, `corrections/`, `reports/`, `eval/` (see `ENV_DIR_DIRS`). Other modules find artifacts by this convention, not by ad-hoc paths. One sanctioned exception (D-DRAFT-6 docker-distro): the **documents root** may be repointed outside the env_dir via `requirements_dir` (`input_root` is the authority; containers mount the shared corpus read-only at a fixed path) — every OUTPUT path (`out/`, `state/`, `reports/`, …) stays under `env_dir` unconditionally.
- `correction_file(artifact)` returns `None` when missing — callers must handle absence; this is how the "optional override" semantics of corrections is enforced.
- `validate()` returns errors as a list (never raises) so CLI and Web UI can surface all problems at once.
- Environment configs live at `environments/<name>.json` (gitignored except `.gitkeep`) — they are per-user, not committed.

**Key choices**
- Stage registry is a list of tuples (not an enum) so stages can be referenced by name, by 1-based number, or by description without three parallel definitions.
- `EnvironmentConfig` stores `env_dir` as a string (not `Path`) to keep JSON round-tripping trivial; `env_dir_path` property exposes it as a `Path` (rename from `document_root` / `doc_root` per D-022).
- **LLM + embedding config has a single canonical home**: `config/llm.json`. Per-field 3-tier resolution (CLI > env var > `config/llm.json`) with the `EnvironmentConfig` LLM/embedding fields kept as a back-compat fallback below the file. The env config keeps these fields for now to avoid breaking existing `environments/<name>.json` files; new environments should leave them at defaults and put global LLM settings in `config/llm.json`.
- **Pipeline mode toggles** (`skip_taxonomy`, `skip_graph`) follow the same 3-tier resolution. `--rag-only` and `NORA_RAG_ONLY` are convenience knobs that imply both. Skipping graph at run-time means downstream stages (eval) and the web query path build a stub MNO/Release/Plan-only graph from vectorstore metadata and run with `_bypass_graph=True`.

**Non-goals**
- Not a pipeline runner — execution lives in [pipeline](../pipeline/MODULE.md); this module only defines the scope and paths.
- No secrets or credentials — environment configs are human-editable JSON, safe to share for debugging.
- No runtime state (job IDs, metrics, logs) — those belong in [web](../web/MODULE.md)'s SQLite stores.

<!-- BEGIN:STRUCTURE -->
_Alphabetical, regenerated by regen-map._


`config.py`
- `BROAD_QUERY_TOP_K_ENV_VAR` — constant — pub
- `DEFAULT_BROAD_QUERY_TOP_K` — constant — pub
- `DEFAULT_EMBEDDING_MODEL` — constant — pub
- `DEFAULT_EMBEDDING_PROVIDER` — constant — pub
- `DEFAULT_ENABLE_GROUPING` — constant — pub
- `DEFAULT_GAP_THRESHOLD` — constant — pub
- `DEFAULT_LLM_CONFIG_PATH` — constant — pub
- `DEFAULT_LLM_MODEL` — constant — pub
- `DEFAULT_LLM_PROVIDER` — constant — pub
- `DEFAULT_LLM_TIMEOUT` — constant — pub
- `DEFAULT_NARROW_QUERY_TOP_K` — constant — pub
- `DEFAULT_RERANKER_BATCH_SIZE` — constant — pub
- `DEFAULT_RERANKER_MODEL` — constant — pub
- `DEFAULT_RERANKER_OLLAMA_URL` — constant — pub
- `DEFAULT_RERANKER_PROVIDER` — constant — pub
- `DEFAULT_RETRIEVAL_CONFIG_PATH` — constant — pub
- `DEFAULT_STANDARDS_SOURCE` — constant — pub
- `EMBEDDING_API_KEY_ENV_VAR` — constant — pub
- `EMBEDDING_BASE_URL_ENV_VAR` — constant — pub
- `EMBEDDING_MODEL_ENV_VAR` — constant — pub
- `EMBEDDING_PROVIDERS` — constant — pub
- `EMBEDDING_PROVIDER_ENV_VAR` — constant — pub
- `ENV_DIR_DIRS` — constant — pub
- `EnvironmentConfig` — dataclass — pub — Configuration for a pipeline environment.
  - `active_stages` — property — pub — Stage names that will run, in order.
  - `correction_file` — method — pub — Get path to a correction file if it exists, else None.
  - `corrections_path` — method — pub — Get the corrections directory.
  - `env_dir_path` — property — pub
  - `eval_path` — method — pub — Get the eval directory (user-supplied Q&A pairs).
  - `init_directories` — method — pub — Create the standard directory structure. Returns created dirs.
  - `input_path` — method — pub — Get input directory for a specific MNO and release (D-023).
  - `input_root` — property — pub — Root of the source documents: `requirements_dir` when set (shared
  - `load_json` — classmethod — pub
  - `out_path` — method — pub — Get output directory for a specific pipeline stage.
  - `path` — method — pub — Get a standard subdirectory under env_dir (generic accessor).
  - `reports_path` — method — pub — Get the reports directory.
  - `save_json` — method — pub
  - `state_path` — method — pub — Get the state directory (runtime SQLite DBs).
  - `validate` — method — pub — Return list of validation errors (empty = valid).
- `GAP_THRESHOLD_ENV_VAR` — constant — pub
- `GROUPING_ENABLED_ENV_VAR` — constant — pub
- `LLMConfigFile` — dataclass — pub — Schema for `config/llm.json`. Empty/zero values mean "fall through".
- `_parse_providers` — function — internal — Parse the optional `providers` list.
- `LLMProviderEntry` — dataclass — pub — One named endpoint the Ask page can send a question to.
  - `api_key` — property — pub
  - `load` — classmethod — pub
- `LLM_API_KEY_ENV_VAR` — constant — pub
- `LLM_BASE_URL_ENV_VAR` — constant — pub
- `LLM_MODEL_ENV_VAR` — constant — pub
- `LLM_PROVIDERS` — constant — pub
- `LLM_PROVIDER_ENV_VAR` — constant — pub
- `LLM_TIMEOUT_ENV_VAR` — constant — pub
- `NARROW_QUERY_TOP_K_ENV_VAR` — constant — pub
- `NUM_STAGE` — constant — pub
- `PIPELINE_LANES` — constant — pub
- `PIPELINE_STAGES` — constant — pub
- `RAG_ONLY_ENV_VAR` — constant — pub
- `REQUIREMENTS_DIR_ENV_VAR` — constant — pub
- `RERANKER_API_KEY_ENV_VAR` — constant — pub
- `RERANKER_BASE_URL_ENV_VAR` — constant — pub
- `RERANKER_BATCH_SIZE_ENV_VAR` — constant — pub
- `RERANKER_BATCH_SIZE_ENV_VAR_DEPRECATED` — constant — pub
- `RERANKER_ENABLED_ENV_VAR` — constant — pub
- `RERANKER_MODEL_ENV_VAR` — constant — pub
- `RERANKER_OLLAMA_URL_ENV_VAR` — constant — pub
- `RERANKER_PROVIDER_ENV_VAR` — constant — pub
- `RetrievalConfig` — dataclass — pub — Schema for `config/retrieval.json`.
  - `load` — classmethod — pub
- `SKIP_GRAPH_ENV_VAR` — constant — pub
- `SKIP_RESOLVE_ENV_VAR` — constant — pub
- `SKIP_STANDARDS_ENV_VAR` — constant — pub
- `SKIP_TAXONOMY_ENV_VAR` — constant — pub
- `STAGE_DESC` — constant — pub
- `STAGE_NAMES` — constant — pub
- `STAGE_NUM` — constant — pub
- `STANDARDS_SOURCES` — constant — pub
- `STANDARDS_SOURCE_ENV_VAR` — constant — pub
- `_BROAD_QUERY_TYPES` — constant — internal
- `_LLM_CONFIG_CACHE` — constant — internal
- `_PROJECT_ROOT` — constant — internal
- `_RETRIEVAL_CONFIG_CACHE` — constant — internal
- `_llm_config` — function — internal
- `_reset_llm_config_cache` — function — internal — Test hook — drop the cached config so the next read picks up
- `_reset_retrieval_config_cache` — function — internal — Test hook — drop the cached config so the next read picks up
- `_resolve_int_env_or_default` — function — internal — Read int from env var; fall back to default on absent / invalid.
- `_retrieval_config` — function — internal
- `_truthy` — function — internal — Treat env-var strings as bool. `1 / true / yes / on` → True.
- `is_broad_query_type` — function — pub — True when the query_type belongs to the broad bucket. Unknown
- `logger` — constant — pub
- `resolve_bm25_weight` — function — pub — Resolve the BM25 weight for the RRF fusion at query time.
- `resolve_embedding_api_key` — function — pub — Resolve the effective embedding API key (Bearer token for
- `resolve_embedding_base_url` — function — pub — Resolve the effective embedding base URL (for HTTP-API embedders).
- `resolve_embedding_model` — function — pub — Resolve the effective embedding model name.
- `resolve_embedding_provider` — function — pub — Resolve the effective embedding provider.
- `resolve_gap_threshold` — function — pub — Resolve the gap threshold for grouping auto-commit vs disambiguation.
- `resolve_grouping_enabled` — function — pub — Resolve whether Stage 4.7 grouping is enabled.
- `resolve_llm_api_key` — function — pub — Resolve the effective LLM API key (OpenAI-compatible providers).
- `resolve_llm_base_url` — function — pub — Resolve the effective LLM base URL (OpenAI-compatible providers).
- `resolve_llm_model` — function — pub — Resolve the effective LLM model name.
- `resolve_llm_provider` — function — pub — Resolve the effective LLM provider.
- `resolve_llm_timeout` — function — pub — Resolve the effective LLM request timeout (seconds).
- `resolve_requirements_dir` — function — pub — Resolve the source-documents root override.
- `resolve_provider` — function — pub — Look up one roster entry by id.
- `resolve_providers` — function — pub — The named provider roster from `config/llm.json`, or `[]`.
- `resolve_reranker_api_key` — function — pub — Resolve the reranker endpoint API key (bearer token) for
- `resolve_reranker_base_url` — function — pub — Resolve the reranker endpoint base URL for OpenAI-compatible
- `resolve_reranker_batch_size` — function — pub — Resolve the reranker batch size.
- `resolve_reranker_enabled` — function — pub — Resolve whether to attach the cross-encoder reranker at query time.
- `resolve_reranker_model` — function — pub — Resolve the cross-encoder model id / path.
- `resolve_reranker_ollama_url` — function — pub — Resolve the Ollama server URL for provider=ollama.
- `resolve_reranker_provider` — function — pub — Resolve the reranker backend.
- `resolve_skip_graph` — function — pub — Resolve whether to skip the knowledge-graph stage.
- `resolve_skip_resolve` — function — pub — Resolve whether to skip the cross-reference resolve stage.
- `resolve_skip_standards` — function — pub — Resolve whether to skip the standards (3GPP spec download) stage.
- `resolve_skip_taxonomy` — function — pub — Resolve whether to skip the taxonomy stage.
- `resolve_stage` — function — pub — Convert a stage number or name to a canonical stage name.
- `resolve_standards_source` — function — pub — Resolve the effective standards source.
- `resolve_top_k` — function — pub — Resolve the per-query top-k for retrieval.

`env_cli.py`
- `ENVS_DIR` — constant — pub
- `_env_path` — function — internal
- `_load_env` — function — internal
- `cmd_create` — function — pub — Create a new environment.
- `cmd_delete` — function — pub — Delete an environment config.
- `cmd_init` — function — pub — Initialize the directory structure for an environment.
- `cmd_list` — function — pub — List all environments.
- `cmd_show` — function — pub — Show environment details.
- `cmd_stages` — function — pub — List all pipeline stages.
- `main` — function — pub

`profile_bindings.py`
- `PROFILE_BINDINGS_FILENAME` — constant — pub
- `ProfileBinding` — dataclass — pub
- `ProfileBindings` — class — pub — Resolves `(mno, release)` → profile path with the D-DRAFT-7 precedence.
  - `__init__` — constructor — internal
  - `_match` — method — internal
  - `covers` — method — pub
  - `override` — property — pub
  - `resolve` — method — pub — The profile path for a cell. Raises ``ValueError`` (PIP-E003) on miss.
  - `uncovered` — method — pub — `(mno, release)` pairs with no binding. Accepts tuples or objects
- `load_profile_bindings` — function — pub — Load ``<env_dir>/profiles.json``.
<!-- END:STRUCTURE -->

**Depends on**
None (tier-0 leaf; stdlib only).

**Depended on by**
[pipeline](../pipeline/MODULE.md), [web](../web/MODULE.md).
