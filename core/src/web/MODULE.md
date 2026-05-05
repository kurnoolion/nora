# web

**Purpose**
FastAPI + Bootstrap 5 + HTMX Web UI for non-CLI team members (D-008). Provides pipeline submission with SSE-streamed logs, a persistent job queue, a shared-folder browser with Windows↔Linux path translation, a query console, a corrections editor, and a metrics dashboard (D-009). Runs behind an authenticating reverse proxy (`root_path` support; no in-app auth per D-016), works fully offline (vendored Bootstrap / Icons / HTMX), and never blocks a request on metric writes. Serves FR-16 (in-browser correction editing), FR-19 (eight surfaces: pipeline / SSE / job queue / folder browse / query / env CRUD / corrections / metrics), FR-20 (no npm/JS build), FR-28 (env_dir via Web UI form), FR-29 (state/ for runtime DBs per D-022); covers NFR-3 (vendored static assets), NFR-10 (fire-and-forget metrics middleware), NFR-11 (5-category SQLite metrics), NFR-12 (`/proc` + `nvidia-smi` sampling, no `psutil`).

**Public surface**
- App (app.py):
  - `app: FastAPI` — the ASGI application; wires middleware, static mounts, routers, templates
- Config (config.py):
  - `WebConfig` — host, port, root_path, path_mappings, ollama_url, default_model, env_dir, plus DB-path overrides `jobs_db` / `metrics_db` / `feedback_db`; `from_dict()`, `env_dir_path()`, `state_path()`, `jobs_db_path()`, `metrics_db_path()`, `feedback_db_path()` (per D-022; override-aware)
  - `PathMapping` — `(windows, linux, label)` entry
  - `EnvJsonConfig` — schema for the optional `config/env.json` layer (env-related fields: `env_dir`, `jobs_db`, `metrics_db`, `feedback_db`); `load(path=None)` with malformed/missing tolerance
  - `load_config(path=None) -> WebConfig` — resolves env_dir (web.json > $ENV_DIR > env.json) and per-DB overrides (CLI / env var > env.json > computed default) in one call
  - `DEFAULT_CONFIG_PATH`, `DEFAULT_ENV_JSON_PATH` — module-level constants pointing at `config/web.json` and `config/env.json`
- Jobs (jobs.py):
  - `Job` dataclass — id, job_type (`pipeline | query | eval`), status, pipeline/query fields, progress, log_lines, result, error
  - `JobQueue(db_path)` — aiosqlite-backed queue; `init_db()`, submit / update / list / cancel / load / append-log
- Metrics (metrics.py):
  - `MetricRecord` — timestamp, category (`request | llm | pipeline | resource | eval`), name, value, unit, tags
  - `MetricsStore(db_path)` — aiosqlite store with indexes on category / name / timestamp; `init_db()`, `record()`, query helpers
- Feedback (feedback_db.py):
  - `FeedbackStore(db_path)` — aiosqlite store for the Test page's free-form Q&A + thumbs-up/down + comment log; `initialize()`, `record_qa()` (returns row id), `record_feedback(row_id, vote, free_form_feedback)`, `get_row()`, `list_recent()`. Surface for offline review of LLM hallucinations (D-043 driver).
- `MetricsMiddleware` (middleware.py) — captures every request's timing and error count; fire-and-forget
- `PathMapper(mappings)` (path_mapper.py) — `to_linux()`, `to_windows()`; translates Windows UNC paths to Linux mount points
- `ResourceSampler` (resource_sampler.py) — background task sampling CPU / memory / disk / GPU via `/proc` and `nvidia-smi` (no `psutil` dependency)
- Routers (routes/): dashboard, environments, pipeline, jobs, query, corrections, files, metrics_route, parse_review (Parse Review UI), req_browser (Requirement Browser), resolve_review (Resolve Review UI), playground (Test page) — each mounted via `app.include_router`
- Static + Templates: vendored under `static/` and `templates/` — no CDN at runtime

**Invariants**
- `MetricsMiddleware` is **fire-and-forget** — it never blocks or crashes a response. Metric failures are swallowed at `logger.debug`.
- Zero npm / JS build step. Server-side jinja2 + HTMX partials only; Bootstrap 5 + Bootstrap Icons + HTMX are **vendored** under `static/`. Runtime never fetches from a CDN.
- **Reverse-proxy compatible**: `root_path` is injected into every template context via `_template_response()`. Links built with `url_for` or prefixed by `{{ root_path }}` work behind a sub-path proxy mount.
- SQLite uses WAL journal mode (both jobs and metrics DBs) — supports concurrent reads while a background job writes.
- Jobs and metrics DBs are separate files (`<env_dir>/state/nora.db`, `<env_dir>/state/nora_metrics.db` per D-022) — metrics can be truncated for retention without touching job history.
- `PathMapper` is case-insensitive for Windows paths (UNC paths are not case-sensitive); it returns `None` when no mapping matches — callers surface that as a user error, not a 500.
- Resource sampler runs on a 30s interval, reads CPU from `/proc/stat`, memory from `/proc/meminfo`, GPU via `nvidia-smi` subprocess — deliberately dependency-free because the host may be locked down.
- No proprietary document content in metric tags, job log lines sent to SSE, or error-message templates. Verbose logs persist to disk; chat-facing surfaces stay clean (D-012).

**Key choices**
- FastAPI over Streamlit / Gradio because the UI needs fine-grained routing (corrections, files, jobs) and reverse-proxy deployment — SESSION_SUMMARY §19.
- HTMX over a SPA framework — dramatically less JS, server renders HTML fragments, state lives in SQLite. Matches the "no npm build" invariant.
- `asyncio.create_task()` for background jobs + SSE for log streaming — one process, no broker, deploys as a single service.
- `ResourceSampler` reads `/proc` directly rather than importing `psutil` — one less pip install on restricted hosts and works inside containers without privileges.
- Separate metrics DB so the metrics retention / truncation policy can be aggressive without touching the job history.
- Ollama URL and default model live in `WebConfig` rather than env vars — the UI exposes them in settings; `PipelineContext` reads the same config when it creates a provider.

**Non-goals**
- No multi-user auth / RBAC in v1. Production deployment runs behind an authenticating reverse proxy (D-016); when in-app authn is added, it's a distinct cross-cutting change, not a router plugin.
- Not a deployment platform. Production deployment (systemd / container / proxy config) is the user's responsibility; app only exposes the right ASGI entrypoint.
- No WebSocket real-time — SSE is sufficient for unidirectional log streaming; WS adds reconnect complexity we don't need.
- No state beyond SQLite + filesystem. Caches are HTTP-level (browser) or derived artifacts in `<env_dir>/out/`; there is no Redis, no memcached, no in-process dict that outlives a request.

<!-- BEGIN:STRUCTURE -->
_Alphabetical, regenerated by regen-map._


`app.py`
- `_duration_filter` — function — internal — Human-readable duration for a Job.
- `_template_response` — function — internal — Render a template with root_path injected into context.
- `dashboard` — function — pub
- `health_check` — function — pub
- `lifespan` — function — pub
- `STATIC_DIR` — constant — pub
- `TEMPLATES_DIR` — constant — pub
- `WEB_DIR` — constant — pub

`config.py`
- `_ENV_VAR_FEEDBACK_DB` — constant — internal
- `_ENV_VAR_JOBS_DB` — constant — internal
- `_ENV_VAR_METRICS_DB` — constant — internal
- `_resolve_db_path` — function — internal — Pick the highest-priority override for a DB path.
- `DEFAULT_CONFIG_PATH` — constant — pub
- `DEFAULT_ENV_JSON_PATH` — constant — pub
- `EnvJsonConfig` — dataclass — pub — Per-environment config loaded from `config/env.
  - `load` — classmethod — pub
- `load_config` — function — pub — Load config from JSON file, falling back to defaults.
- `PathMapping` — dataclass — pub — Maps a Windows network path to a Linux mount point.
- `PROJECT_ROOT` — constant — pub
- `WebConfig` — dataclass — pub — Web application configuration.
  - `env_dir_path` — method — pub
  - `feedback_db_path` — method — pub — SQLite path for the Test page's question/answer/vote/feedback log.
  - `from_dict` — classmethod — pub
  - `jobs_db_path` — method — pub
  - `metrics_db_path` — method — pub
  - `state_path` — method — pub

`feedback_db.py`
- `_SCHEMA` — constant — internal
- `FeedbackStore` — class — pub — Async SQLite store for Test-page question/answer/feedback logs.
  - `__init__` — constructor — pub
  - `get_row` — method — pub — Read a single row by id (for testing / inspection).
  - `initialize` — method — pub — Create the schema if missing.
  - `list_recent` — method — pub — Read the N most recent rows, optionally filtered by section.
  - `record_feedback` — method — pub — Update an existing Q&A row with the user's vote and/or
  - `record_qa` — method — pub — Insert a new row at question-submission time.

`jobs.py`
- `_IDX_JOBS_CREATED` — constant — internal
- `_IDX_JOBS_STATUS` — constant — internal
- `_IDX_LOGS_JOB` — constant — internal
- `_JOBS_SCHEMA` — constant — internal
- `_LOGS_SCHEMA` — constant — internal
- `_now_iso` — function — internal
- `_row_to_job` — function — internal
- `Job` — dataclass — pub
- `JobQueue` — class — pub
  - `__init__` — constructor — pub
  - `append_log` — method — pub
  - `cancel` — method — pub
  - `cleanup_old` — method — pub
  - `get` — method — pub
  - `get_logs` — method — pub
  - `get_logs_with_numbers` — method — pub
  - `get_meta` — method — pub — Get job metadata without loading log lines.
  - `init_db` — method — pub
  - `list_jobs` — method — pub
  - `submit` — method — pub
  - `update_status` — method — pub

`metrics.py`
- `_IDX_CAT_NAME_TS` — constant — internal
- `_IDX_CATEGORY` — constant — internal
- `_IDX_NAME` — constant — internal
- `_IDX_TIMESTAMP` — constant — internal
- `_METRICS_SCHEMA` — constant — internal
- `_now_iso` — function — internal
- `MetricRecord` — dataclass — pub
- `MetricsStore` — class — pub
  - `__init__` — constructor — pub
  - `_agg_for` — method — internal
  - `_latest_value` — method — internal
  - `_pipeline_stage_summary` — method — internal
  - `cleanup_old` — method — pub
  - `compact_report` — method — pub — Compact pasteable summary in RPT style.
  - `init_db` — method — pub
  - `query` — method — pub
  - `record` — method — pub
  - `record_batch` — method — pub
  - `summary` — method — pub — Aggregates: count, avg, min, max, p95 per metric name.

`middleware.py`
- `_record_request_metric` — function — internal
- `MetricsMiddleware` — class — pub
  - `dispatch` — method — pub

`path_mapper.py`
- `_is_subpath` — function — internal — Return True if *path* is strictly under *parent*.
- `_looks_like_windows` — function — internal — Heuristic: starts with \\ or a drive letter like C:\.
- `_normalize_win` — function — internal — Normalize a Windows path: forward slashes to backslashes, strip trailing.
- `PathMapper` — class — pub — Translates paths between Windows UNC and Linux mount conventions.
  - `__init__` — constructor — pub
  - `is_within_roots` — method — pub — Security check: ensure the resolved path is within a configured root.
  - `list_roots` — method — pub — Return available roots with both path representations and labels.
  - `resolve` — method — pub — Smart resolve: detect Windows paths and convert; otherwise treat as Linux.
  - `to_linux` — method — pub — Convert a Windows UNC path to a Linux path.
  - `to_windows` — method — pub — Convert a Linux path to a Windows UNC path for display.

`resource_sampler.py`
- `_DEFAULT_INTERVAL` — constant — internal
- `_read_cpu_percent` — function — internal — Read CPU utilization from /proc/stat using delta between calls.
- `_read_disk_usage` — function — internal — Read disk usage for a path.
- `_read_gpu_info` — function — internal — Read GPU utilization via nvidia-smi.
- `_read_memory_gb` — function — internal — Read RAM from /proc/meminfo.
- `_sample_once` — function — internal
- `_sampler_loop` — function — internal
- `start_resource_sampler` — function — pub — Start the background sampler and return its task handle.

`routes/corrections.py`
- `_list_envs_with_status` — function — internal
- `_load_env` — function — internal
- `_safe_name` — function — internal
- `corrections_index` — function — pub
- `ENVIRONMENTS_DIR` — constant — pub
- `profile_discard` — function — pub
- `profile_editor` — function — pub
- `profile_save` — function — pub
- `profile_start` — function — pub
- `PROJECT_ROOT` — constant — pub
- `report_page` — function — pub
- `report_text` — function — pub
- `router` — constant — pub
- `taxonomy_discard` — function — pub
- `taxonomy_editor` — function — pub
- `taxonomy_save` — function — pub
- `taxonomy_start` — function — pub

`routes/dashboard.py`
- `dashboard_jobs_partial` — function — pub
- `dashboard_stats` — function — pub
- `dashboard_status_partial` — function — pub
- `router` — constant — pub

`routes/environments.py`
- `_list_environments` — function — internal
- `_stages_for_template` — function — internal
- `create_environment` — function — pub
- `delete_environment` — function — pub
- `ENVIRONMENTS_DIR` — constant — pub
- `environments_list` — function — pub
- `environments_new` — function — pub
- `PROJECT_ROOT` — constant — pub
- `router` — constant — pub

`routes/files.py`
- `_build_breadcrumbs` — function — internal
- `_find_root_label` — function — internal
- `_human_size` — function — internal
- `browse` — function — pub
- `file_listing_partial` — function — pub
- `files_page` — function — pub
- `router` — constant — pub

`routes/jobs.py`
- `cancel_job` — function — pub
- `job_detail` — function — pub
- `job_log_stream` — function — pub
- `jobs_list` — function — pub
- `jobs_table_partial` — function — pub
- `router` — constant — pub
- `TERMINAL_STATUSES` — constant — pub

`routes/metrics_route.py`
- `metrics_compact` — function — pub
- `metrics_page` — function — pub
- `metrics_resource_partial` — function — pub — HTMX partial: refreshes the resource gauges.
- `metrics_summary` — function — pub
- `router` — constant — pub

`routes/parse_review.py`
- `_build_annotated_blocks` — function — internal — Load DocumentIR + ParseLog and return (blocks, log, error_message).
- `_list_docs` — function — internal — Return doc IDs that have at least a parse log OR an IR file.
- `_load_log` — function — internal
- `_load_or_default_review` — function — internal
- `_parse_log_dir` — function — internal
- `parse_review_index` — function — pub
- `parse_review_report` — function — pub
- `parse_review_save` — function — pub
- `parse_review_view` — function — pub
- `router` — constant — pub

`routes/pipeline.py`
- `_list_environments` — function — internal — Scan environments/*.json and return summary dicts.
- `_record_stage_metrics` — function — internal — Record pipeline stage metrics to MetricsStore (fire-and-forget safe).
- `_stages_for_template` — function — internal — Build stage list for dropdown rendering.
- `ENVIRONMENTS_DIR` — constant — pub
- `pipeline_page` — function — pub
- `PROJECT_ROOT` — constant — pub
- `router` — constant — pub
- `run_pipeline_background` — function — pub — Execute pipeline stages in a background task.
- `submit_pipeline` — function — pub

`routes/playground.py`
- `_run_query_for_test` — function — internal — Adapt the existing /query pipeline runner into a dict shape.
- `playground_ask` — function — pub — Submit a question, run the query pipeline, log the Q&A row.
- `playground_feedback` — function — pub — Update an existing Q&A row with the user's vote / comment.
- `playground_page` — function — pub
- `router` — constant — pub

`routes/query.py`
- `_build_llm_from_env_or_default` — function — internal — Construct the LLM provider for /query and /test.
- `_build_pipeline` — function — internal — Construct a QueryPipeline + LLM.
- `_find_env_config_for_web` — function — internal — Locate the env JSON whose env_dir matches the Web UI's env_dir_path.
- `_get_or_build_pipeline` — function — internal — Return (pipeline, llm) cached on app.state.
- `_graph_path` — function — internal — Resolve <env_dir>/out/graph/knowledge_graph.json.
- `_pipeline_build_lock` — constant — internal
- `_PipelineBuildError` — class — internal — Raised by _build_pipeline when prerequisites aren't met.
- `_record_llm_metrics` — function — internal — Record LLM call metrics to MetricsStore (fire-and-forget safe).
- `_run_query_sync` — function — internal — Run the query pipeline synchronously (called via asyncio.to_thread).
- `_vectorstore_dir` — function — internal — Resolve <env_dir>/out/vectorstore/.
- `PROJECT_ROOT` — constant — pub
- `query_page` — function — pub
- `query_result` — function — pub
- `router` — constant — pub
- `run_query_background` — function — pub — Execute query in a background task.
- `submit_query` — function — pub

`routes/req_browser.py`
- `_build_tree_hierarchy` — function — internal — Convert flat requirement list into nested tree (child_nodes populated).
- `_list_docs` — function — internal
- `_load_req` — function — internal
- `_load_tree_flat` — function — internal
- `_load_xrefs` — function — internal
- `_parse_dir` — function — internal
- `_parse_str_list` — function — internal
- `_refs_for_req` — function — internal — Return refs sourced from req_id, grouped by type.
- `_resolve_dir` — function — internal
- `req_browser_compare` — function — pub
- `req_browser_detail` — function — pub
- `req_browser_index` — function — pub
- `req_browser_tree` — function — pub
- `router` — constant — pub

`routes/resolve_review.py`
- `_build_ref_rows` — function — internal — Build enriched ref rows for each of the three ref types.
- `_build_req_index` — function — internal — Return req_id -> {text, section, title} from the parsed tree.
- `_list_docs` — function — internal
- `_load_or_default_review` — function — internal
- `_parse_dir` — function — internal
- `_resolve_dir` — function — internal
- `_review_dir` — function — internal
- `_TEXT_PREVIEW` — constant — internal
- `resolve_review_index` — function — pub
- `resolve_review_report` — function — pub
- `resolve_review_save` — function — pub
- `resolve_review_view` — function — pub
- `router` — constant — pub
<!-- END:STRUCTURE -->

**Depends on**
[env](../env/MODULE.md), [models](../models/MODULE.md), [parser](../parser/MODULE.md), [pipeline](../pipeline/MODULE.md), [query](../query/MODULE.md), [resolver](../resolver/MODULE.md), [corrections](../corrections/MODULE.md).

**Depended on by**
None — top of the stack.

**Deferred**
- `ResourceSampler` class wrapper (deferred: current `start_resource_sampler()` function is functionally sufficient; class form would be a cosmetic refactor — revisit: if sampler state/lifecycle grows beyond the current single-task handle)
- Declare `llm`, `profiler`, `taxonomy`, `vectorstore` in Depends on (deferred: routes import schemas/configs across many peers; the right fix is likely to route through `pipeline`/`query` rather than expand Depends on — revisit: when refactoring routes to reduce peer coupling)
