# web

**Purpose**
FastAPI + Bootstrap 5 + HTMX Web UI for non-CLI team members (D-008). Provides pipeline submission with SSE-streamed logs, a persistent job queue, a shared-folder browser with Windows↔Linux path translation, a query console, a corrections editor, and a metrics dashboard (D-009). Runs behind an authenticating reverse proxy (`root_path` support; no in-app auth per D-016), works fully offline (vendored Bootstrap / Icons / HTMX), and never blocks a request on metric writes. Serves FR-16 (in-browser correction editing), FR-19 (eight surfaces: pipeline / SSE / job queue / folder browse / query / env CRUD / corrections / metrics), FR-20 (no npm/JS build), FR-36 (enrichment-review editor: overlay records + merge-log fold + Apply fan-out), FR-28 (env_dir via Web UI form), FR-29 (state/ for runtime DBs per D-022); covers NFR-3 (vendored static assets), NFR-10 (fire-and-forget metrics middleware), NFR-11 (5-category SQLite metrics), NFR-12 (`/proc` + `nvidia-smi` sampling, no `psutil`). Also hosts the **Eval Studio** (strand golden-eval; serves FR-39, supports FR-38): the expert one-stop-shop for authoring golden eval samples (Stage-1 query + ground-truth picker) and curating golden responses (Stage-2 chat) — schema and runners owned by [eval](../eval/MODULE.md).

**Public surface**
- App (app.py):
  - `app: FastAPI` — the ASGI application; wires middleware, static mounts, routers, templates
- Config (config.py):
  - `WebConfig` — host, port, root_path (reverse-proxy prefix; `$NORA_WEB_ROOT_PATH` > web.json, env-first because web.json is baked into the image while the prefix is per-deployment; normalized to leading-slash/no-trailing-slash), path_mappings, ollama_url, default_model, env_dir, plus DB-path overrides `jobs_db` / `metrics_db` / `feedback_db` and `corrections_root` (enrichment-corrections volume, strand sira-enrichment-review; resolution web.json > $NORA_CORRECTIONS_ROOT > env.json; "" = surface disabled); `from_dict()`, `env_dir_path()`, `state_path()`, `jobs_db_path()`, `metrics_db_path()`, `feedback_db_path()` (per D-022; override-aware)
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
- Enrichment-corrections overlay (enrich_overlay_store.py + routes/enrich_review.py, strand sira-enrichment-review):
  - `EnrichOverlayStore(corrections_root)` — sole WRITER of `<root>/sira-enrich/` (sira-query mounts ro): word-record edits (`edit(mno, req_id, op, words|pairs, label, reason, by, origin_release)` with ops remove/unremove/add/unadd/suppress/unsuppress/reaffirm/discard; one active record per (word, direction)), label ops (`disabled_labels`, `set_label_disabled`, `delete_label`, `label_counts`, `label_req_counts` — drawer badge reqs:records), merge log (`accepted_labels`, `set_label_merged` — `accepted-labels.json`, labels-are-branches), reason categories (seeded, extensible), pending signals (`overlay_digest(mno, label)` — canonical view digest, formula locked to sira-query's `_overlay_digest`; `overlay_mtime` as legacy fallback). flock'd read-modify-write, atomic tmp+rename, last-writer-wins per record.
  - `POST /api/enrich-review/edit`, `GET/POST /api/enrich-review/labels[.../toggle|merge|delete]`, `GET/POST /api/enrich-review/reasons` — JSON edit API; missing corrections_root -> 503 (fail-loud, not silent no-op). Merge/un-merge and delete are admin-gated in team mode (`team_mode.is_admin`); edits are not (they land invisibly on the editor's label branch). Record semantics validated against the service-side fold (`sandbox/sira_query/enrich_overlay.py`) by a cross-side test.
  - `GET /enrichment-review` (page) + HTMX partials: `/api/enrich-review/cells|plans` (proxied sira-query reads over NORA_SIRA_QUERY_URLS — comma-separated, both stacks; the PRIMARY = first URL is the read/banner authority), `/table` (server-rendered rows: service data ⊕ CURRENT overlay projected onto the `label` view — edits visible instantly regardless of service state; index-words column shows the base BM25 layer; enrichments header carries the enrich model name the service reports), `/row-edit` (one chip interaction → store edit → row partial + OOB pending banner), `/apply` (POST /cells/<cell>/reload on EVERY configured sira-query — with a label only that branch variant rebuilds; the default view is untouched), `/pending`, `/pending-cells` + `/apply-all` (per-MNO staleness sweep behind the labels drawer's Apply-all button — reloads every stale cell of the current view). Exactly ONE `#pending-banner` exists, in the sticky stamp bar; the table response updates it OOB. Chip grammar per the strand design: plain ×→ghost ↺, green adds, suppress-collapse, muted read-only chips for merged (main) records, newer-add-countermands-older-remove rendered as an active add, held banner with Re-affirm/Discard (bulk via __all_held__). Team-mode gate admits `/enrichment-review` + `/api/enrich-review` (domain experts are the gated team).
  - Exports (enrich_report.py + `GET /api/enrich-review/export?mode=report|scorecard&label=&mno=&download=`): pure text builders over flattened word records — `build_report` (label × category pivot, top removed/added words with req/plan counts, suppressions, origin drift, verbatim notes by category; D-012 posture — never requirement body text) and `build_scorecard` (per remove-record: does the CURRENT LLM output — latest loaded release per MNO — still produce the word? gone = fixed; per-label fixed/unfixed + unfixed word list). Report mode degrades to a note when sira-query is down; scorecard mode requires it. Deterministic ordering (successive exports diff cleanly). The export is the input to a prompt-revision pass; after re-enrichment the scorecard measures fix efficacy, then `delete_label` retires the campaign's records.
- Ingested-corpus inventory (routes/playground.py, D-DRAFT-14 docker-distro):
  - `GET /api/test/ingested` — HTMX partial for the Test page's corpus table: one row per served `(MNO, release)` cell with distinct plans, distinct requirements, ingestion date, Latest badge (MMMYYYY order — mirrors query-side latest resolution), Lane badges. Merges the nora vectorstore scan with the sira-query service's `GET /cells` (SIRA-only cells appear from service data; both-lane cells with diverging counts render BOTH numbers — lane staleness indicator). Cached on a cell-dir mtime fingerprint + 5-min TTL.
- Feedback (feedback_db.py):
  - `FeedbackStore(db_path)` — aiosqlite store for the Test page's Q&A + feedback log; `initialize()`, `record_qa()` (returns row id), `record_feedback(row_id, vote, free_form_feedback)` (legacy thumbs vote), `record_user_feedback()` (team-eval: 0–9 score + categories + comment + user name, per lane), `get_row()`, `list_recent()`. Surface for offline review of LLM hallucinations (D-043 driver) and the team-eval round.
- Config store (config_db.py) [D-053]:
  - `ConfigStore(db_path)` — synchronous SQLite-backed user-config store; values JSON-encoded; threadsafe via internal lock. Public methods: `get(module, key)`, `get_module(module)`, `get_all()`, `set(module, key, value, updated_by)`, `delete(module, key)`, `apply_to_caches()` (overlay every stored value onto the cached `LLMConfigFile` / `RetrievalConfig` instances at lifespan startup), `reapply_one(module, key)` (cheaper single-field overlay used after each save).
- Config schema (config_schema.py) [D-053]:
  - `ConfigField` — per-knob metadata: `module`, `key`, `label`, `kind` (`bool` / `string` / `int` / `float` / `enum` / `password` / `dict_by_query_type`), `category` (`feature` / `value` / `tunable`), `choices` for enums, `value_kind` for per-type maps, `help` text.
  - `ConfigSection` — section grouping (LLM & Embedding, Retrieval & Grouping); `CONFIG_SECTIONS: list[ConfigSection]` is the page's authoritative schema.
  - `find_field(module, key) -> ConfigField | None`, `all_fields() -> list[ConfigField]` — accessors.
- Markdown rendering (markdown_render.py):
  - `render_markdown(text) -> Markup` — converts LLM answer markdown to Jinja-safe HTML (headers, bullets, **bold**, *italic*, fenced code, tables, `nl2br`). Registered as the `md` Jinja filter on `templates.env`. Strips dangerous tags defensively before parsing (script / style / iframe / object / embed / svg-with-onclick / math).
  - `render_markdown_bubbles(text, req_ids=None, root_path="") -> Markup` — `render_markdown` plus req-ID bubbles (strand req-id-bubbles): each id in `req_ids` that appears verbatim in the rendered prose becomes a badge that previews the requirement on hover and pins it on click, loading the body from `GET /api/req/{req_id}` via a `bubbleopen` event so every open path (hover / click / keyboard focus) fetches by one route. `root_path` prefixes that URL for reverse-proxy mounts. Registered as the `md_bubbles` Jinja filter. Substitution skips tag interiors and the contents of `<a>` / `<pre>`, so markup and fenced blocks cannot be corrupted; inline `<code>` IS bubbled (LLMs routinely backtick req IDs). Empty / missing `req_ids` degrades to plain `render_markdown` output.
- DOCX preview rendering (docx_html_render.py):
  - `render_docx_html(file_path) -> str` — emits an HTML fragment for the Bootstrap annotation harness. Walks docx body in `DOCXExtractor`'s order and applies the same skip rules (empty paragraphs, degenerate tables) so every emitted element's `data-block-idx` matches the IR's `ContentBlock.position.index`. Tables also emit `data-row-idx` per body row for row-range annotations.
- Annotation schema (bootstrap_schema.py):
  - `validate_annotation_file(payload) -> dict` — server-side validator for `<env_dir>/annotations/<plan>_annotations.json` per `cline-playbooks/annotation-schema.md`; returns sanitized payload (extra fields stripped) or raises `AnnotationValidationError` with a per-field error list.
  - `KINDS` — 14 kinds: 8 structural (`section_heading`, `req_id`, `toc`, `strikethrough`, `version_history`, `definitions`, `applicability`, `priority`), 5 reference (`reference_intra_doc`, `reference_cross_doc`, `reference_spec`, `reference_list`, `reference_list_entry`), and 1 user-override (`remove`, [D-061]).
  - `SPEC_REFERENCE_STYLES` (`direct` / `indirect`) — required field for `reference_spec` kind.
  - `REFERENCE_LIST_NUMBERING_STYLES`, `REFERENCE_LIST_LAYOUTS`, `STRIKETHROUGH_SUBKINDS`, `STRIKETHROUGH_VISUALS`, `TOC_PATTERN_HINTS`, `DEFINITIONS_LAYOUTS`, `REQ_ID_PLACEMENTS`, `APPLICABILITY_POSITIONS`, `VERSION_HISTORY_SUBTYPES` — authoritative enum constants.
  - `TARGET_KEYS_BY_KIND` — per-kind allowed keys for the optional ground-truth `target` dict (intra_doc: section_number/req_id; cross_doc: +plan_id; reference_spec: spec/section/ref_number; reference_list_entry: spec/section). Unknown keys silently stripped on save.
  - `NOTES_MAX_CHARS`, `SCHEMA_VERSION` — caps.
  - `AnnotationValidationError` — raised on validation failure; carries `errors: list[str]`.
- Eval Studio (routes/golden_eval.py, strand golden-eval):
  - `GET /eval-studio` (page) + HTMX partials — sample board (list by status `draft → stage1-ready → golden-ready`, per-expert attribution), sample create/edit (query text + area tag), and status transitions (`stage1-ready` requires non-empty, fully-validated ground truth; `golden-ready` requires a saved golden_response).
  - **Ground-truth picker** — cascading dropdowns **MNO → Plan → Release**: MNO/Release inventories from the per-cell parse layout (`out/parse/<mno>/<release>/`, same discovery as req_browser), Plan dropdown shows the union of plans across that MNO's releases and the Release dropdown filters to releases containing the chosen plan (latest preselected). Selected cell+plan renders the requirement tree (shared tree loader, below) with checkboxes + filter-as-you-type; checked rows append to the sample's ground-truth list, which persists across dropdown changes (multi-plan / multi-MNO samples = change dropdowns, keep ticking). Every picker-sourced entry is fully qualified `(mno, release, plan, req_id)` — the cascade supplies qualifiers, the expert never types them. Each added entry shows the requirement title/text inline for confirmation.
  - Direct-paste entry — accepts BULK ids (split on comma/space/newline, strand eval-studio-ux-2 [D-207]); each id is validated against the store at save time; an id resolving in exactly one cell is auto-qualified, an id matching SEVERAL releases auto-picks the LATEST release (`req_tree.latest_match`) with a muted per-pick notice — no ambiguity error (the expert wants the current revision; D-207 records the contract change); unresolvable ids block `stage1-ready`.
  - Round-2 UX (strand eval-studio-ux-2, teammate-authored): sortable columns + bounded scrollable lists (attribute-driven client-side sorter, per-table state surviving HTMX re-renders), board author + MNO filters, sample MNO tag (D-206), copy buttons, golden-tab stay-on-save + `golden_meta.edited` provenance badge, in-place question edit, sticky new-sample form values, Enter-to-submit, in-context tab-preserving promote buttons.
  - Retrieval-assisted seeding — run the sample's query against a stack, tick relevant results in. Carries a bias caution (ground truth seeded from retrieval can't capture what retrieval misses); the QC template requires ≥1 independently-sourced entry per sample.
  - Stage-1 preview — single-sample run (`POST /sira-query` on a configured stack) rendering recall + hit/miss per ground-truth entry, so an expert sanity-checks a sample without waiting for a batch run.
  - Stage-2 curation chat — free-form chat with the on-prem LLM over the ground-truth chunk texts (fetched by `req:<id>` from the store); the chat is a scratchpad, only the final text + meta persist as `golden_response` via eval's `golden.py` (single schema, no parallel write path).
  - Team-mode gate admits `/eval-studio` (domain experts are the gated team); draft-sample delete is expert-allowed (UI-confirmed), deleting promoted (non-draft) samples admin-gated.
- Shared requirement-tree loader (req_tree.py, strand golden-eval): `load_tree_flat` / `build_tree_hierarchy` / `load_tree` / `find_tree` / cell-and-doc discovery (`list_cells` / `list_docs`), picker cascade data (`plans_for_mno` — plan → releases union, `reqs_for_plan`), and `find_req` (corpus-wide id lookup driving direct-entry validation + auto-qualification) plus `latest_match` (newest-release pick over `find_req` results, ties→first for determinism — the latest-on-conflict contract, D-207) — lifted from `routes/req_browser.py`'s module-private helpers; used by both req_browser and the Eval Studio picker. Per-cell layout first, legacy flat fallback.
- `MetricsMiddleware` (middleware.py) — captures every request's timing and error count; fire-and-forget
- `PathMapper(mappings)` (path_mapper.py) — `to_linux()`, `to_windows()`; translates Windows UNC paths to Linux mount points
- `ResourceSampler` (resource_sampler.py) — background task sampling CPU / memory / disk / GPU via `/proc` and `nvidia-smi` (no `psutil` dependency)
- Routers (routes/): dashboard, environments, pipeline, jobs, query, corrections, files, metrics_route, parse_review (Parse page — two tabs: **Bootstrap** annotation harness with `GET /parse-review/bootstrap/docs`, `GET /parse-review/bootstrap/<doc_id>/view`, `GET|POST /parse-review/bootstrap/<doc_id>/annotations` writing `<env_dir>/annotations/<plan>_annotations.json` atomically; **Review** post-parse 3-pane), req_browser (Requirement Browser), resolve_review (Resolve Review UI), playground (Test/Ask page — `POST /api/test/ask`, `POST /api/test/synthesize-group` for D-049 disambiguation user-pick path, `POST /api/test/feedback`, `GET /api/req/{req_id}` (strand req-id-bubbles — read-only requirement fragment behind the answer bubbles; resolves through the shared `req_tree.find_req` + `latest_match`, 404 when the id is not in the parse layer; team-gate allowlisted so shared answers stay readable for gated experts); strands ask-page-ux/ask-history [D-208, D-209]: `GET /ask/s/{row_id}` read-only shared-answer snapshot of a stored ask (normal user view ONLY — engineering internals are not persisted at ask time, D-209; retroactively shareable, no schema change) + body-only `GET /api/ask/s/{row_id}` (the history detail pane reuses the shared markup via `_shared_body.html` so the two surfaces cannot drift) + `GET /ask/history` (browser-localStorage question history — server-side history REJECTED, D-208: optional free-text `user_name` makes it spoofable/merged-anonymous; stale entries 404 and offer removal). All three paths are team-gate allowlisted — shared answers are team-safe by D-209's normal-view-only constraint), config_route (Config page — `GET /config`, `POST /api/config/save`; D-053) — each mounted via `app.include_router`
- App state (set up in `lifespan`): `app.state.job_queue`, `app.state.metrics`, `app.state.feedback_store`, `app.state.path_mapper`, `app.state.config_store` (ConfigStore | None — None when `--config-db` is unset), `app.state.query_pipeline` (cached after first build; saving via `/api/config/save` sets it back to None so the next query rebuilds with the new resolved values).
- CLI launcher (`if __name__ == "__main__"` in app.py): `--env-dir`, `--host`, `--port`, `--jobs-db`, `--metrics-db`, `--feedback-db`, `--config-db` (each maps to a corresponding `NORA_*_DB` / `ENV_DIR` env var so the uvicorn-reload worker re-import sees the same resolution).
- Static + Templates: vendored under `static/` and `templates/` — no CDN at runtime

**Invariants**
- `MetricsMiddleware` is **fire-and-forget** — it never blocks or crashes a response. Metric failures are swallowed at `logger.debug`.
- Zero npm / JS build step. Server-side jinja2 + HTMX partials only; Bootstrap 5 + Bootstrap Icons + HTMX are **vendored** under `static/`. Runtime never fetches from a CDN.
- **Reverse-proxy compatible**: `root_path` is injected into every template context via `_template_response()`. Links built with `url_for` or prefixed by `{{ root_path }}` work behind a sub-path proxy mount. The proxy must pass the prefix through unchanged (Starlette root_path semantics: the ASGI path includes the prefix; routing strips it — a stripping proxy 404s the `/static` mount). Middleware path checks (team gate allowlist, metrics endpoint label) compare the root_path-stripped path, never the raw one.
- SQLite uses WAL journal mode (both jobs and metrics DBs) — supports concurrent reads while a background job writes.
- Jobs and metrics DBs are separate files (`<env_dir>/state/nora.db`, `<env_dir>/state/nora_metrics.db` per D-022) — metrics can be truncated for retention without touching job history.
- `PathMapper` is case-insensitive for Windows paths (UNC paths are not case-sensitive); it returns `None` when no mapping matches — callers surface that as a user error, not a 500.
- Resource sampler runs on a 30s interval, reads CPU from `/proc/stat`, memory from `/proc/meminfo`, GPU via `nvidia-smi` subprocess — deliberately dependency-free because the host may be locked down.
- No proprietary document content in metric tags, job log lines sent to SSE, or error-message templates. Verbose logs persist to disk; chat-facing surfaces stay clean (D-012).
- **Config-page DB layer in resolver chain** [D-053]: when `--config-db` / `$NORA_CONFIG_DB` is set, lifespan startup instantiates `ConfigStore` and calls `apply_to_caches()`, which overlays every stored value onto the cached `LLMConfigFile` / `RetrievalConfig` instances. The existing `resolve_*` functions in `core/src/env/config.py` then automatically pick up the new tier — no plumbing changes elsewhere. Effective resolver chain becomes `CLI > env var > ConfigStore (DB) > config/*.json > defaults`. `POST /api/config/save` writes to the DB, calls `reapply_one` to refresh the cache, and sets `app.state.query_pipeline = None` so the next query rebuilds with the new resolved values.
- **Markdown renderer strips dangerous HTML before parsing**: `render_markdown` removes `<script>` / `<style>` / `<iframe>` / `<object>` / `<embed>` / `<svg>` / `<math>` tags (paired and self-closing) before invoking the markdown library. LLM answer text on the Test page goes through this filter; raw chunk text in the click-to-expand fragment view deliberately doesn't (the indexed body may contain literal markdown syntax that's part of the requirement, e.g. `**MUST**` in 3GPP-style specs).
- **Answer bubbles anchor on the row's own req_id set, never a req-ID pattern** (strand req-id-bubbles): the bubbled ids are `rag_chunks` + `llm_citations` on the live path and the persisted `retrieved_ids` + `cited_ids` columns on the stored path — matched as literal substrings. No regex, and no branch on `lane`: the set is derived once in the shared per-lane context both nora and sira pass through, so a bubble path that works on one lane cannot silently fail on the other (the lanes are exercised on different machines). An id the LLM invented is absent from the set and simply renders unbubbled. Bubble bodies load on first open only — `req_tree.find_req` scans parse trees, so eager preloading would cost N tree loads per answer. The badge does NOT carry `data-bs-toggle`: Bootstrap's own toggle would close a panel hover had already opened, so `app.js` owns show/hide and syncs `aria-expanded` by hand. **Any hand-injected HTML carrying `hx-*` must be passed to `htmx.process()`** — htmx only wires what it swapped itself, and the Ask page's SSE handler and History detail pane both assign with raw `innerHTML`; without it the bubbles render, expand, and never fetch.
- **Logging configured at module-import time**, not just inside the `if __name__ == "__main__":` launcher block. Required because `uvicorn.run(reload=True)` spawns a worker that re-imports the module but never executes the launcher block; without basicConfig at import, the worker's loggers default to WARNING and silently drop every `logger.info(...)` in the request path (verification lines like `Web LLM resolved`, `[Query knobs]`, `ConfigStore active` would never reach stderr).
- **Bootstrap-tab DOCX renderer is index-aligned with the extractor**: `docx_html_render.render_docx_html` walks the docx body in `DOCXExtractor.extract`'s order and applies the same skip rules (empty paragraphs and degenerate single-empty-column tables consume no index). This guarantees every `data-block-idx` in the rendered HTML corresponds to a real `ContentBlock.position.index` in the saved IR — a regression here would silently misalign every annotation.
- **Labels are branches; Apply is never a publish** (strand sira-enrichment-review): the default (main) view = unlabeled records + labels in the `accepted-labels.json` merge log; a label view = main + that label's records. Merge/un-merge only edit the merge log — records are never rewritten, so both are instant and reversible. Apply/apply-all are deliberately NOT admin-gated: a reload can only sync serving to what the overlay + merge log already contain, never expose an unmerged label to main.
- **Pending is a view-scoped content digest, formula-locked to sira-query**: `overlay_digest` hashes only the view's filtered records — the merge log itself stays out, so a merge/un-merge flags only the MNOs the label touches; fully-undone edits digest back to "in sync". The formula must byte-match sira-query's `_overlay_digest` (cross-side parity test); both images must ship formula changes together. Pending/banner state always renders from the PRIMARY configured service (`NORA_SIRA_QUERY_URLS[0]`) — the one every read path queries.
- **Eval Studio writes samples only through eval's `golden.py`** (strand golden-eval): the sample schema has one owner; web never serializes `<env_dir>/eval/golden/` JSON itself. Golden samples are proprietary content — no sample query, req_id, or golden-response text in metric tags, logs, or error messages (NFR-8; same posture as D-012).
- **Bootstrap reference detection is decoupled from resolution**: annotation kinds capture the *source-token shape* of references (5 kinds: `reference_intra_doc`, `reference_cross_doc`, `reference_spec` with `style=direct|indirect`, `reference_list`, `reference_list_entry`). The optional `target` dict is **ignored by Cline's rule derivation** — it carries resolver-eval ground truth only. Indirect spec citations (`[5]`) flow through a two-step path the parser already supports for `definitions`: section-level annotation marks the references list; per-entry pattern populates a `reference_list_map: dict[int, {spec, section?}]` on the parsed tree; the resolver looks up the bracketed number in that map at resolve time. This split keeps detection rules portable across MNOs and lets resolution evolve independently.

**Key choices**
- FastAPI over Streamlit / Gradio because the UI needs fine-grained routing (corrections, files, jobs) and reverse-proxy deployment — SESSION_SUMMARY §19.
- HTMX over a SPA framework — dramatically less JS, server renders HTML fragments, state lives in SQLite. Matches the "no npm build" invariant.
- `asyncio.create_task()` for background jobs + SSE for log streaming — one process, no broker, deploys as a single service.
- `ResourceSampler` reads `/proc` directly rather than importing `psutil` — one less pip install on restricted hosts and works inside containers without privileges.
- Separate metrics DB so the metrics retention / truncation policy can be aggressive without touching the job history.
- Ollama URL and default model live in `WebConfig` rather than env vars — the UI exposes them in settings; `PipelineContext` reads the same config when it creates a provider.
- **`/config` page + ConfigStore as the user-editing surface for the resolver chain** [D-053]: the page renders LLM and Retrieval knobs grouped by category (Features / Values / Tunable parameters) per `CONFIG_SECTIONS`. New `kind="dict_by_query_type"` schema field renders a per-`QueryType` table editor (used by `bm25_weight_by_type` today; pattern generalizes to the rest of Phase 4-migrate's per-type maps). Opt-in: when `--config-db` is unset, the page renders read-only with a notice and the resolver chain falls through to JSON files / defaults. See [`../query/RETRIEVAL.md`](../query/RETRIEVAL.md) §14 for the full configuration model.

**Non-goals**
- No multi-user auth / RBAC in v1. Production deployment runs behind an authenticating reverse proxy (D-016); when in-app authn is added, it's a distinct cross-cutting change, not a router plugin.
- Not a deployment platform. Production deployment (systemd / container / proxy config) is the user's responsibility; app only exposes the right ASGI entrypoint.
- No WebSocket real-time — SSE is sufficient for unidirectional log streaming; WS adds reconnect complexity we don't need.
- No state beyond SQLite + filesystem. Caches are HTTP-level (browser) or derived artifacts in `<env_dir>/out/`; there is no Redis, no memcached, no in-process dict that outlives a request.

<!-- BEGIN:STRUCTURE -->
_Alphabetical, regenerated by regen-map._


`app.py`
- `STATIC_DIR` — constant — pub
- `TEMPLATES_DIR` — constant — pub
- `WEB_DIR` — constant — pub
- `_duration_filter` — function — internal — Human-readable duration for a Job.
- `_start_time` — constant — internal
- `_template_response` — function — internal — Render a template with root_path injected into context.
- `admin_unlock` — function — pub — Admin full-access unlock for team mode: a correct ?token sets an HttpOnly
- `app` — constant — pub
- `config` — constant — pub
- `dashboard` — function — pub
- `health_check` — function — pub
- `lifespan` — function — pub
- `logger` — constant — pub
- `templates` — constant — pub

`config.py`
- `DEFAULT_CONFIG_PATH` — constant — pub
- `DEFAULT_ENV_JSON_PATH` — constant — pub
- `EnvJsonConfig` — dataclass — pub — Per-environment config loaded from `config/env.json`. All
  - `load` — classmethod — pub
- `PROJECT_ROOT` — constant — pub
- `PathMapping` — dataclass — pub — Maps a Windows network path to a Linux mount point.
- `WebConfig` — dataclass — pub — Web application configuration.
  - `env_dir_path` — method — pub
  - `feedback_db_path` — method — pub — SQLite path for the Test page's question/answer/vote/feedback log.
  - `from_dict` — classmethod — pub
  - `jobs_db_path` — method — pub
  - `metrics_db_path` — method — pub
  - `state_path` — method — pub
- `_ENV_VAR_FEEDBACK_DB` — constant — internal
- `_ENV_VAR_JOBS_DB` — constant — internal
- `_ENV_VAR_METRICS_DB` — constant — internal
- `_resolve_db_path` — function — internal — Pick the highest-priority override for a DB path. Returns ""
- `load_config` — function — pub — Load config from JSON file, falling back to defaults.
- `logger` — constant — pub

`config_db.py`
- `ConfigStore` — class — pub — SQLite-backed key-value config store, scoped by (module, key).
  - `__init__` — constructor — internal
  - `_connect` — method — internal
  - `_init_schema` — method — internal
  - `apply_to_caches` — method — pub — Overlay every stored value onto the in-memory config caches.
  - `delete` — method — pub
  - `get` — method — pub — Return decoded value or None if absent.
  - `get_all` — method — pub — Return everything, indexed by (module, key) tuples.
  - `get_module` — method — pub — Return all (key → value) pairs for one module.
  - `reapply_one` — method — pub — After a single write, re-overlay just that value onto the
  - `seed_missing` — method — pub — Write each (module, key, value) only when the key is absent.
  - `set` — method — pub — Upsert one (module, key) → value pair.
- `_JSON_DECODE_FALLBACK` — constant — internal
- `_SCHEMA_SQL` — constant — internal
- `_decode` — function — internal
- `_encode` — function — internal
- `logger` — constant — pub

`config_schema.py`
- `CONFIG_SECTIONS` — constant — pub
- `ConfigField` — dataclass — pub
- `ConfigSection` — dataclass — pub
- `_LLM_FIELDS` — constant — internal
- `_RETRIEVAL_FIELDS` — constant — internal
- `all_fields` — function — pub
- `find_field` — function — pub

`enrich_overlay_store.py`
- `DEFAULT_REASON_CATEGORIES` — constant — pub
- `EDIT_OPS` — constant — pub
- `EnrichOverlayStore` — class — pub — Filesystem store bound to a corrections root ("" = disabled).
  - `__init__` — constructor — internal
  - `_locked` — method — internal
  - `_mno_path` — method — internal
  - `_read_json` — staticmethod — internal
  - `_write_json` — staticmethod — internal
  - `accepted_labels` — method — pub — Labels merged into main (the default view). File = merge log.
  - `add_reason_category` — method — pub
  - `delete_label` — method — pub — Strip every record carrying `label` from every MNO file (the
  - `dir` — property — pub
  - `disabled_labels` — method — pub
  - `edit` — method — pub — Apply one edit op; returns the req's updated entry (None when the
  - `enabled` — property — pub
  - `get_entry` — method — pub
  - `get_overlay` — method — pub
  - `label_counts` — method — pub — Record counts per label across MNO files (drawer display).
  - `label_req_counts` — method — pub — Distinct requirements touched per label across MNO files
  - `list_mnos` — method — pub — MNOs with an overlay file (sorted).
  - `overlay_digest` — method — pub — Canonical content digest of the `label` VIEW of the MNO overlay
  - `overlay_mtime` — method — pub — 0.0 when absent — pending = mtime > cell loaded_at.
  - `reason_categories` — method — pub
  - `set_label_disabled` — method — pub
  - `set_label_merged` — method — pub — Merge a label into main (or un-merge it) — the admin's

`enrich_report.py`
- `SAMPLE_REQ_IDS` — constant — pub
- `_in_scope` — function — internal
- `_scope_str` — function — internal
- `_word_pivot` — function — internal — Per-word lines: word, #reqs, #plans, categories, sample req_ids.
- `build_report` — function — pub — The label × category pivot report. `plans` maps (mno, req_id) -> plan
- `build_scorecard` — function — pub — Prompt-fix scorecard: per remove-record, does the CURRENT LLM output
- `flatten_records` — function — pub — {mno: overlay} -> flat record rows

`feedback_db.py`
- `CATEGORIES` — constant — pub
- `FeedbackStore` — class — pub — Async SQLite store for Test-page question/answer/feedback logs.
  - `__init__` — constructor — internal
  - `get_row` — method — pub — Read a single row by id (for testing / inspection).
  - `initialize` — method — pub — Create the schema if missing and bring older DBs forward with
  - `list_recent` — method — pub — Read the N most recent rows, optionally filtered by section.
  - `record_feedback` — method — pub — Update an existing Q&A row with the user's vote and/or
  - `record_qa` — method — pub — Insert a new row at question-submission time. Returns the
  - `record_user_feedback` — method — pub — Merged-tab feedback path: update an existing row with the user's
- `_LANE_INDEX` — constant — internal
- `_MERGED_TAB_COLUMNS` — constant — internal
- `_SCHEMA` — constant — internal
- `_ensure_columns` — function — internal — Add any of `columns` not already present on `table`. SQLite has no
- `logger` — constant — pub

`jobs.py`
- `Job` — dataclass — pub
- `JobQueue` — class — pub
  - `__init__` — constructor — internal
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
- `_IDX_JOBS_CREATED` — constant — internal
- `_IDX_JOBS_STATUS` — constant — internal
- `_IDX_LOGS_JOB` — constant — internal
- `_JOBS_SCHEMA` — constant — internal
- `_LOGS_SCHEMA` — constant — internal
- `_now_iso` — function — internal
- `_row_to_job` — function — internal

`markdown_render.py`
- `_DANGEROUS_TAG_OPEN_RE` — constant — internal
- `_DANGEROUS_TAG_RE` — constant — internal
- `_MD_EXTENSIONS` — constant — internal
- `render_markdown` — function — pub — Convert markdown source to HTML, return Jinja-safe Markup.

`metrics.py`
- `MetricRecord` — dataclass — pub
- `MetricsStore` — class — pub
  - `__init__` — constructor — internal
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
- `_IDX_CATEGORY` — constant — internal
- `_IDX_CAT_NAME_TS` — constant — internal
- `_IDX_NAME` — constant — internal
- `_IDX_TIMESTAMP` — constant — internal
- `_METRICS_SCHEMA` — constant — internal
- `_now_iso` — function — internal
- `logger` — constant — pub

`middleware.py`
- `MetricsMiddleware` — class — pub
  - `dispatch` — method — pub
- `TeamModeMiddleware` — class — pub — When NORA_WEB_TEAM_MODE is on, redirect gated team members (no admin
  - `dispatch` — method — pub
- `_record_request_metric` — function — internal
- `logger` — constant — pub

`path_mapper.py`
- `PathMapper` — class — pub — Translates paths between Windows UNC and Linux mount conventions.
  - `__init__` — constructor — internal
  - `is_within_roots` — method — pub — Security check: ensure the resolved path is within a configured root.
  - `list_roots` — method — pub — Return available roots with both path representations and labels.
  - `resolve` — method — pub — Smart resolve: detect Windows paths and convert; otherwise treat as Linux.
  - `to_linux` — method — pub — Convert a Windows UNC path to a Linux path.
  - `to_windows` — method — pub — Convert a Linux path to a Windows UNC path for display.
- `_is_subpath` — function — internal — Return True if *path* is strictly under *parent*.
- `_looks_like_windows` — function — internal — Heuristic: starts with \\ or a drive letter like C:\.
- `_normalize_win` — function — internal — Normalize a Windows path: forward slashes to backslashes, strip trailing.

`req_tree.py`
- `_release_sort_key` — function — internal
- `_req_plan` — function — internal — A requirement's effective plan: per-req plan_id (multi-plan docs)
- `build_tree_hierarchy` — function — pub — Convert flat requirement list into nested tree (child_nodes
- `find_req` — function — pub — Locate a req_id across the corpus — direct-entry validation and
- `find_tree` — function — pub — Resolve a doc's tree file. Cell-qualified path first, flat legacy
- `list_cells` — function — pub — Discover ``(mno, release)`` cells that have at least one parsed
- `list_docs` — function — pub — Doc ids with a parsed tree — scoped to one cell when qualifiers
- `load_tree` — function — pub — Full tree dict (incl. plan_id / plan_name / requirements).
- `load_tree_flat` — function — pub — The tree's flat requirements list (req_browser's historical shape).
- `logger` — constant — pub
- `parse_dir` — function — pub
- `plans_for_mno` — function — pub — ``{plan_id: [release, ...]}`` — the union of plans across the
- `reqs_for_plan` — function — pub — Every requirement in one cell belonging to ``plan``, across all of

`resource_sampler.py`
- `_DEFAULT_INTERVAL` — constant — internal
- `_prev_cpu_idle` — constant — internal
- `_prev_cpu_total` — constant — internal
- `_read_cpu_percent` — function — internal — Read CPU utilization from /proc/stat using delta between calls.
- `_read_disk_usage` — function — internal — Read disk usage for a path. Returns (used_gb, total_gb).
- `_read_gpu_info` — function — internal — Read GPU utilization via nvidia-smi. Returns None if unavailable.
- `_read_memory_gb` — function — internal — Read RAM from /proc/meminfo. Returns (used_gb, total_gb).
- `_sample_once` — function — internal
- `_sampler_loop` — function — internal
- `logger` — constant — pub
- `start_resource_sampler` — function — pub — Start the background sampler and return its task handle.

`routes/config_route.py`
- `_coerce` — function — internal — Convert a form string to the field's typed value. Empty string
- `_current_dict_by_query_type` — function — internal — Build the {query_type: value} dict for a dict_by_query_type
- `_current_value` — function — internal — Read the live effective value for a field via the resolver chain.
- `config_page` — function — pub
- `config_save` — function — pub — Persist edits, invalidate caches, clear cached pipeline.
- `logger` — constant — pub
- `router` — constant — pub

`routes/corrections.py`
- `ENVIRONMENTS_DIR` — constant — pub
- `PROJECT_ROOT` — constant — pub
- `_list_envs_with_status` — function — internal
- `_load_env` — function — internal
- `_safe_name` — function — internal
- `corrections_index` — function — pub
- `logger` — constant — pub
- `profile_discard` — function — pub
- `profile_editor` — function — pub
- `profile_save` — function — pub
- `profile_start` — function — pub
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
- `logger` — constant — pub
- `router` — constant — pub

`routes/enrich_review.py`
- `ApplyAllRequest` — class — pub
- `EditRequest` — class — pub
- `LabelDelete` — class — pub
- `LabelMerge` — class — pub
- `ReasonAdd` — class — pub
- `_LABELED_OPS` — constant — internal
- `_SIRA_URLS` — constant — internal
- `_TABLE_PAGE` — constant — internal
- `_cell_mno_release` — function — internal
- `_pending_ctx` — function — internal — Pending = the label VIEW's overlay content differs from what its
- `_row_view` — function — internal — Merge a service row with the CURRENT overlay entry into template
- `_service_get` — function — internal — GET from the primary sira-query; service errors surface as 502.
- `_stale_cells` — function — internal — Cells whose `label`-view serving digest lags the live overlay.
- `_store` — function — internal
- `api` — constant — pub
- `apply` — function — pub — The expert's Apply: reload the cell on EVERY configured sira-query
- `apply_all` — function — pub — Apply for every stale cell of the current view, on EVERY
- `cells` — function — pub — Proxied cell list for the MNO/Release cascade.
- `edit` — function — pub
- `export` — function — pub — The label x category pivot report / prompt-fix scorecard
- `label_delete` — function — pub — Bulk cleanup once a prompt fix lands (D-DRAFT-5). Admin only —
- `label_merge` — function — pub — Merge a label into main (or un-merge) — admin only. This edits the
- `labels` — function — pub
- `logger` — constant — pub
- `page` — function — pub
- `pending` — function — pub
- `pending_cells` — function — pub — Feeds the labels drawer's Apply-all button (shown only when
- `plans` — function — pub
- `reason_add` — function — pub
- `reasons` — function — pub
- `router` — constant — pub
- `row_edit` — function — pub — One chip/box interaction → store edit → re-rendered row partial.
- `table` — function — pub — Server-rendered rows: service data ⊕ current overlay, projected

`routes/environments.py`
- `ENVIRONMENTS_DIR` — constant — pub
- `PROJECT_ROOT` — constant — pub
- `_list_environments` — function — internal
- `_stages_for_template` — function — internal
- `create_environment` — function — pub
- `delete_environment` — function — pub
- `environments_list` — function — pub
- `environments_new` — function — pub
- `logger` — constant — pub
- `router` — constant — pub

`routes/files.py`
- `_build_breadcrumbs` — function — internal
- `_find_root_label` — function — internal
- `_human_size` — function — internal
- `browse` — function — pub
- `file_listing_partial` — function — pub
- `files_page` — function — pub
- `logger` — constant — pub
- `router` — constant — pub

`routes/golden_eval.py`
- `_CHAT_CONTEXT_MAX_CHARS` — constant — internal
- `_CHAT_SYSTEM` — constant — internal
- `_editor_ctx` — function — internal
- `_env_dir` — function — internal
- `_gt_context` — function — internal — Assemble the ground-truth requirement texts for the curation chat
- `_load_or_error` — function — internal
- `_stack_url` — function — internal — Primary sira-query stack URL — first of NORA_SIRA_QUERY_URLS,
- `_template` — function — internal
- `curation_chat` — function — pub
- `eval_studio_page` — function — pub
- `gt_add` — function — pub
- `gt_remove` — function — pub
- `logger` — constant — pub
- `picker_plans` — function — pub
- `picker_releases` — function — pub
- `picker_reqs` — function — pub
- `router` — constant — pub
- `sample_board` — function — pub
- `sample_create` — function — pub
- `sample_delete` — function — pub
- `sample_editor` — function — pub
- `sample_meta` — function — pub
- `sample_status` — function — pub
- `save_golden` — function — pub
- `stage1_preview` — function — pub

`routes/jobs.py`
- `TERMINAL_STATUSES` — constant — pub
- `cancel_job` — function — pub
- `job_detail` — function — pub
- `job_log_stream` — function — pub
- `jobs_list` — function — pub
- `jobs_table_partial` — function — pub
- `logger` — constant — pub
- `router` — constant — pub

`routes/metrics_route.py`
- `logger` — constant — pub
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
- `logger` — constant — pub
- `parse_review_index` — function — pub — Summary landing — corpus-level rollup of profile-driven detection
- `parse_review_report` — function — pub
- `parse_review_save` — function — pub
- `parse_review_view` — function — pub — Per-doc Review page — 3-pane annotated view. Reached by clicking
- `router` — constant — pub

`routes/pipeline.py`
- `ENVIRONMENTS_DIR` — constant — pub
- `PROJECT_ROOT` — constant — pub
- `_list_environments` — function — internal — Scan environments/*.json and return summary dicts.
- `_record_stage_metrics` — function — internal — Record pipeline stage metrics to MetricsStore (fire-and-forget safe).
- `_stages_for_template` — function — internal — Build stage list for dropdown rendering.
- `logger` — constant — pub
- `pipeline_page` — function — pub
- `router` — constant — pub
- `run_pipeline_background` — function — pub — Execute pipeline stages in a background task.
- `submit_pipeline` — function — pub

`routes/playground.py`
- `_CHARS_PER_TOKEN` — constant — internal
- `_INGESTED_CACHE` — constant — internal
- `_PIN_MAX` — constant — internal
- `_PIN_MIN_SCORE` — constant — internal
- `_PIN_MODE` — constant — internal
- `_PIN_REL_THRESHOLD` — constant — internal
- `_SECTION_IDS` — constant — internal
- `_SELECT_SYNTH_ENABLED` — constant — internal
- `_SELECT_SYNTH_MAX_OUTPUT_TOKENS` — constant — internal
- `_SELECT_SYNTH_SYSTEM_PROMPT` — constant — internal
- `_SELECT_SYNTH_TEXT_CHARS` — constant — internal
- `_SELECT_SYNTH_TOP_K` — constant — internal
- `_SIRA_HEALTHZ_SNAPSHOT_KEYS` — constant — internal
- `_SIRA_QUERY_TIMEOUT` — constant — internal
- `_SIRA_QUERY_URL` — constant — internal
- `_SYNTH_MODE` — constant — internal
- `_SYNTH_TOKEN_BUDGET` — constant — internal
- `_balanced_pin` — function — internal — Round-robin across (mno, release) cells, capped at `limit`.
- `_build_merged_response_html` — function — internal — Post-process lane outputs and render the merged container to a
- `_build_sections` — function — internal — Build the per-request section registry with a corpus-aware blurb.
- `_build_select_synth_context` — function — internal — Group packed chunks by (mno, release) with explicit headers + full text,
- `_call_sira_query` — function — internal — POST the question to the SIRA per-query probe service and
- `_corpus_label` — function — internal — Best-effort short label for the corpus the web UI is bound to.
- `_count_cell` — function — internal — (distinct plans, distinct requirements) from chunk metadata.
- `_filter_sira_notes` — function — internal — In select-synth mode, rerank-off is the intended design (the lane drops
- `_flatten_cited_ids` — function — internal — Extract req_ids from a synthesizer citation list (the *explicit*
- `_ingested_cache_key` — function — internal
- `_ingested_rows` — function — internal
- `_pack_select_synth` — function — internal — Round-robin across (mno, release) cells, packing WHOLE chunks until the
- `_pick_sira_snapshot` — function — internal — Subset a /healthz response to the keys that affect retrieval
- `_render_template_to_string` — function — internal — Render a Jinja template to a string (not a Response). Used to
- `_run_nora_lane_for_merged` — function — internal — Run NORA's hybrid pipeline for the merged tab. Returns a
- `_run_query_for_test` — function — internal — Adapt the existing /query pipeline runner into a dict shape
- `_run_select_synth_lane` — function — internal — select-synth lane: SIRA BM25 candidates (no rerank, full text) → one LLM call
- `_run_sira_lane_for_merged` — function — internal — Run SIRA's BM25→rerank pipeline + NORA's synthesizer pinned to
- `_select_pinned_chunks` — function — internal — Apply the score-based filter to SIRA's ranked results.
- `_select_synth_extract_citations` — function — internal — Corpus-agnostic citation extraction: which packed req_ids appear verbatim
- `_select_synth_int` — function — internal — Read NORA_SIRA_SELECT_SYNTH_<name>, falling back to the legacy
- `_select_synth_synthesize` — function — internal — One LLM call over all packed chunks: the model selects relevant ones and
- `_snapshot_nora_lane_config` — function — internal — Compose NORA's lane_config snapshot from what the query pipeline
- `_snapshot_sira_lane_config` — function — internal — Fetch the per-query SIRA service's /healthz and apply
- `ingested_inventory` — function — pub — The ingested-corpus table partial (HTMX, loaded on page load).
- `logger` — constant — pub
- `playground_ask` — function — pub — Submit a question, run the query pipeline, log the Q&A row,
- `playground_ask_stream` — function — pub — SSE streaming variant of /api/test/ask for the merged tab.
- `playground_feedback` — function — pub — Update an existing Q&A row with the user's feedback. Dispatches on
- `playground_page` — function — pub
- `playground_synthesize_group` — function — pub — Step 3c — user picked a group from a disambiguation response.
- `router` — constant — pub

`routes/query.py`
- `PROJECT_ROOT` — constant — pub
- `_DEFAULT_MAX_DISTANCE_THRESHOLD` — constant — internal
- `_MAX_DISTANCE_THRESHOLD_ENV_VAR` — constant — internal
- `_PipelineBuildError` — class — internal — Raised by `_build_pipeline` when prerequisites aren't met
- `_build_llm_from_env_or_default` — function — internal — Construct the LLM provider for /query and /test.
- `_build_pipeline` — function — internal — Construct a QueryPipeline + LLM. Heavy: loads graph (~10MB),
- `_config_store_get` — function — internal — Best-effort read from app.state.config_store. Returns None if
- `_find_env_config_for_web` — function — internal — Locate the env JSON whose `env_dir` matches the Web UI's
- `_get_or_build_pipeline` — function — internal — Return (pipeline, llm) cached on `app.state`. First call pays
- `_graph_path` — function — internal — Resolve `<env_dir>/out/graph/knowledge_graph.json`. The Web UI
- `_pipeline_build_lock` — constant — internal
- `_record_llm_metrics` — function — internal — Record LLM call metrics to MetricsStore (fire-and-forget safe).
- `_resolve_max_distance_threshold` — function — internal — Return the threshold to pass to QueryPipeline. None disables it.
- `_resolve_reranker` — function — internal — Resolve and instantiate the reranker from the 3-tier config chain.
- `_resolve_top_k_cap` — function — internal — Resolve the user-configured Top-K cap from the ConfigStore.
- `_run_query_sync` — function — internal — Run the query pipeline synchronously (called via asyncio.to_thread).
- `_vectorstore_dir` — function — internal — Resolve `<env_dir>/out/vectorstore/`.
- `logger` — constant — pub
- `query_page` — function — pub
- `query_result` — function — pub
- `router` — constant — pub
- `run_query_background` — function — pub — Execute query in a background task.
- `submit_query` — function — pub

`routes/req_browser.py`
- `_list_docs` — function — internal
- `_load_req` — function — internal
- `_load_xrefs` — function — internal
- `_parse_str_list` — function — internal
- `_refs_for_req` — function — internal — Return refs sourced from req_id, grouped by type.
- `_resolve_dir` — function — internal
- `logger` — constant — pub
- `req_browser_compare` — function — pub
- `req_browser_detail` — function — pub
- `req_browser_index` — function — pub
- `req_browser_tree` — function — pub
- `router` — constant — pub

`routes/resolve_review.py`
- `_TEXT_PREVIEW` — constant — internal
- `_build_ref_rows` — function — internal — Build enriched ref rows for each of the three ref types.
- `_build_req_index` — function — internal — Return req_id -> {text, section, title} from the parsed tree.
- `_list_docs` — function — internal
- `_load_or_default_review` — function — internal
- `_parse_dir` — function — internal
- `_resolve_dir` — function — internal
- `_review_dir` — function — internal
- `logger` — constant — pub
- `resolve_review_index` — function — pub
- `resolve_review_report` — function — pub
- `resolve_review_save` — function — pub
- `resolve_review_view` — function — pub
- `router` — constant — pub

`team_mode.py`
- `ADMIN_COOKIE` — constant — pub
- `ADMIN_TOKEN` — constant — pub
- `ENV_ADMIN_TOKEN` — constant — pub
- `ENV_TEAM_MODE` — constant — pub
- `TEAM_MODE` — constant — pub
- `_TEAM_ALLOWED` — constant — internal
- `is_admin` — function — pub — True when the request may see the full app: gate off (everyone), or a
- `path_allowed_for_team` — function — pub — Whitelist check for the gated surface. `/` is NOT allowed — gated members
- `team_restricted` — function — pub — True when this request is a gated team member (no admin cookie).
<!-- END:STRUCTURE -->

**Depends on**
[env](../env/MODULE.md), [models](../models/MODULE.md), [parser](../parser/MODULE.md), [pipeline](../pipeline/MODULE.md), [query](../query/MODULE.md), [resolver](../resolver/MODULE.md), [corrections](../corrections/MODULE.md), [eval](../eval/MODULE.md) (eval-invocation routes; Eval Studio consumes `golden.py` schema/loaders — edge declared at strand golden-eval design time, closing the asymmetry with eval's Depended-on-by).
Runtime service edge (not an import): **sira-query service** over HTTP (`NORA_SIRA_QUERY_URL`; the enrichment-review surface reads `NORA_SIRA_QUERY_URLS` — comma-separated, first = primary/read authority) — the Test page's SIRA retrieval lane, the corpus inventory's `GET /cells` (D-DRAFT-14 docker-distro), and the enrichment-review proxied reads + `POST /cells/<cell>/reload` (Apply). Degrades gracefully when unreachable (review page: report export degrades, scorecard requires it).

**Depended on by**
None — top of the stack.

**Deferred**
- `ResourceSampler` class wrapper (deferred: current `start_resource_sampler()` function is functionally sufficient; class form would be a cosmetic refactor — revisit: if sampler state/lifecycle grows beyond the current single-task handle)
- Declare `llm`, `profiler`, `taxonomy`, `vectorstore` in Depends on (deferred: routes import schemas/configs across many peers; the right fix is likely to route through `pipeline`/`query` rather than expand Depends on — revisit: when refactoring routes to reduce peer coupling)
