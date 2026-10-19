# Requirements

Last updated: 2026-10-18. Behavioral specs only — project identity and scope live in `PROJECT.md`.

<!--
How to use this file:

- Each requirement has a stable ID. IDs are never reused and never renumbered.
  - New functional requirement → next `FR-N`.
  - New non-functional requirement → next `NFR-N`.
- One sentence per requirement. Active voice. Testable where possible.
- Removed requirements are struck through in place:
    ~~**FR-3** — <original text>~~ (removed YYYY-MM-DD: <reason>)
- Items agreed to postpone go under `## Deferred` — they are not drift.
- `drift-check` reads this file. Keep it current; it is the authority for what the
  system is supposed to do, which design and implementation are checked against.
-->

## Functional

### Ingestion

- **FR-1** — The system extracts a normalized DocumentIR from PDF, DOCX, and XLSX inputs; the IR captures text blocks, tables, images, and font / positional metadata where available.
- **FR-2** — The DocumentProfiler drafts a structure profile (heading detection, requirement-ID pattern, document zones, cross-reference patterns, body-text signature) from representative documents without invoking an LLM; the pipeline takes hand-authored profiles bound per (MNO, release) cell through `<env_dir>/profiles.json`, and an uncovered cell fails loud (D-097).
- **FR-3** — A profile-driven generic structural parser produces a RequirementTree from any MNO's documents using only the profile and detected patterns; no per-MNO code paths.
- **FR-4** — Cross-reference extraction captures internal, cross-plan, and 3GPP standards references with section number and release.
- **FR-6** — The feature taxonomy is derived bottom-up by per-plan LLM extraction (a document may carry one plan or many; multi-plan documents are split into per-plan units), then consolidated into a unified taxonomy; the workflow includes human review through the Web UI.
- **FR-31** — Priority-marker extraction in headings. The structural parser detects an optional priority marker embedded in heading text (e.g., `mandatory`, `optional`, `conditional`) via a profile-defined regex; when matched, the marker is stored as a `priority` attribute on the `Requirement` node and stripped from the displayed title. Profile carries `heading_detection.priority_marker_pattern`; corpora without the convention leave it empty. (Verizon OA does not use this convention; some other carriers do.)
- **FR-32** — Form-factor applicability with hierarchical inheritance. Each `Requirement` carries an `applicability` attribute (free-form `list[str]`, e.g. `["smartphone", "tablet"]`) listing the form factors the requirement applies to. The parser populates this by (a) extracting an explicit value from the requirement's own text or attached table when present, via a profile-defined regex / keyword set, and (b) inheriting from the parent requirement when absent, recursing up the hierarchy until a value is found. When the document declares a top-level applicability section, its value serves as the root default. When no applicability information exists anywhere in the corpus, the attribute is left empty and downstream stages do not filter on it. Controlled-vocabulary form-factor labels are deferred until a second carrier needs the consistency.
- **FR-33** — Strikeout content omission. The extractors for all supported formats (PDF, DOCX, XLSX) surface strikeout formatting on `FontInfo.strikethrough`; the parser drops content blocks where `font_info.strikethrough` is true, representing requirements / sections deleted in the original document. Profile carries an `ignore_strikeout: bool = True` switch (override available through the corrections workflow per FR-15).
- **FR-34** — Table of Contents omission. The profiler detects TOC entries by paragraph style when the corpus has TOC styles (`toc_detection`, D-063, which takes priority), else by their leader-dot-and-page-number signature (default regex `<title>\.{3,}\s*\d+\s*$`); the parser skips matching blocks. Profile carries `toc_detection_pattern` (regex, default as above) and `toc_page_threshold` (float in `[0, 1]`, default 0.7 — a page is treated as a TOC page when at least this fraction of its blocks match the pattern). Both fields are profile-tuneable.
- **FR-35** — Definitions / acronyms expansion for retrieval. The profiler detects each document's definitions / acronyms / glossary section by heading-text regex (default `(?i)acronym|definition|glossary`) and extracts entry pairs (`term → expansion`) into a per-document `definitions_map`. The chunk builder expands the first occurrence of each known term inline in every chunk before embedding (e.g., `ETWS` → `ETWS (Earthquake and Tsunami Warning System)`) to improve vector recall on acronym-shaped queries. Expansion is per-document, not corpus-wide, to preserve locality when a term means different things in different MNO documents.

### Query

- **FR-9** — Query analysis classifies each query into one of ten types (single-doc / cross-doc / cross-MNO comparison / release diff / standards comparison / traceability / feature-level / fact / summarize / general — D-051) and extracts MNO + release scope from the natural-language input.
- **FR-10** — MNO / release resolution maps user-supplied names and dates to canonical scope: explicit scope is preserved as-is, "latest" resolves to the newest release in scope, and when no release is named each MNO in scope resolves independently to its own latest release (D-106).
- **FR-14** — LLM synthesis emits an answer with grounded citations to specific requirement IDs (and to 3GPP spec sections when the standards stage is enabled); when the LLM omits citations, a context-based fallback supplies them from the assembled context.
- **FR-40** — The serving retrieval path is SIRA select-synth per (MNO, release) cell over the NORA-ingested corpus (extract → profile → parse → resolve → taxonomy), using NORA's feature taxonomy; the Ask page answers from it with requirement-ID citations (D-254).

### Corrections and overrides

- **FR-15** — On re-run, the pipeline reads human-edited overrides from `<env_dir>/corrections/*.json` and prefers them over generated outputs (taxonomy and other derived artifacts; profiles are hand-authored inputs, FR-2).
- **FR-16** — The Web UI provides in-browser editing of document profiles and feature taxonomies; saved edits land in the same files the pipeline reads as overrides under FR-15.

### Remote collaboration

- **FR-17** — Every pipeline-stage failure raises a `PipelineError` carrying a stable, registry-defined error code (e.g., `EXT-E001`); verbose logs persist to disk for self-service debugging in environments where the AI partner cannot see runtime artifacts.
- **FR-18** — The system emits compact reports in four formats — RPT (per-stage pipeline summary), MET (metrics), FIX (corrections diff), QC (quality-check templates) — paste-ready for chat-mediated collaboration.

### Web UI

- **FR-19** — The Web UI exposes pipeline-job submission, real-time SSE log streaming, an SQLite-backed job queue, shared-folder browsing with Windows↔Linux path mapping, the Ask page (with per-user question history, shareable answer links, requirement-ID links that open the requirement in place, and a per-question timeline — D-208..D-230), environment CRUD, the corrections editor, and the metrics dashboard, all under a configurable reverse-proxy `root_path`.
- **FR-20** — The Web UI runs without an npm or JS build toolchain (FastAPI + Jinja2 + HTMX + vendored Bootstrap 5) and serves all static assets locally.
- **FR-36** — The Web UI provides an enrichment-review editor: per-cell review of SIRA doc-enrichment phrases with expert add/remove records folded by merge-log semantics, and an Apply action that reloads every configured sira-query service so corrections take effect across all serving stacks.
- **FR-38** — The system maintains an expert-curated golden eval set (target 50 → 200 samples; per-sample JSON under `<env_dir>/eval/golden/`, never in the repo) scored in two stages against any configured serving stack: Stage 1 — retrieval recall (% of a sample's ground-truth req_ids, optionally cell-qualified, present in the stack's retrieved results); Stage 2 — LLM-judged similarity between a response regenerated from Stage-1 retrieved chunks via the production synthesis path and the expert's golden response. One run per stack URL is the release A/B; judge scores are comparable only within one judge-prompt version.
- **FR-39** — The Web UI provides an Eval Studio: an expert one-stop-shop for authoring golden samples (query + area tag + ground-truth picker with MNO → Plan → Release cascade over the ingested corpus, direct-entry, and retrieval-assisted seeding) and curating golden responses via free-form LLM chat, with per-sample Stage-1 preview. The batch eval run itself executes outside the UI as a build step (standalone CLI or final pipeline stage).
- **FR-41** — The Web UI has a team mode (D-159): when enabled, a request without the admin cookie reaches only an allowlisted set of surfaces, and every Ask from such a request runs the SIRA lane only; an admin unlocks full access with a token-backed cookie. Every new route is checked against the allowlist.
- **FR-42** — The Ask page offers a configured roster of named LLM endpoints and a Fast/Think choice (D-216); when a provider returns a permanent refusal, the question is retried once on the roster's fallback endpoint and the answer discloses the reroute (D-220).
- **FR-43** — Builds reach serving by promotion to a named label: promotion swaps a stack to the new label in one step, the previous label stays available for rollback, and a later build never changes a promoted label's data (D-132, D-190).

### Evaluation


### Runtime environment

- **FR-28** — The pipeline accepts a per-environment working directory (`env_dir`) via CLI argument, environment config file, or Web UI input; the system does not assume any hardcoded paths for runtime artifacts, and every storage location outside `env_dir` (FR-29, FR-30) is configured, not assumed.
- **FR-29** — Pipeline artifacts are written under `<env_dir>`, partitioned by purpose: `out/` for pipeline outputs (extracted IR, parsed trees, cross-reference manifests, taxonomies, and the outputs of any enabled later stage); `state/` for runtime SQLite databases (job queue, metrics); `corrections/` for human overrides; `reports/` for compact RPT / MET / FIX / QC outputs; `eval/` for user-supplied Q&A. Serving deployments keep mutable state out of the promoted, read-only `env_dir` snapshot, in configured locations: web state (job, metrics and config DBs, and the roster file) in a per-stack web-state directory (D-247), enrichment corrections in a corrections volume (D-160), and the pooled feedback DB (D-120).
- **FR-30** — Source documents are read from `<env_dir>/input/<MNO>/<release>/` by default; a deployment may point the documents root at a shared requirements store with the same `<MNO>/<release>/` layout through `requirements_dir` (`--requirements-dir` / `NORA_REQUIREMENTS_DIR`, D-132). The pipeline never reads source documents from the repository root.

## Non-functional

### Deployment and install

- **NFR-1** — Production runs fully on-premise; no off-premise LLM or external AI service is invoked on proprietary MNO content.
- **NFR-2** — Offline / air-gapped install works without internet access for every configured model and embedder (local runtimes and their weights, or the on-premise endpoints a deployment names); no component downloads a model at run time.
- **NFR-3** — All Web UI static assets (Bootstrap, Bootstrap Icons, HTMX) are vendored locally; runtime never fetches from a CDN.

### Resource constraints

- **NFR-4** — The PoC pipeline runs to completion on a personal PC with 16 GB RAM and CPU-only inference; the work-laptop target additionally supports a 16 GB NVIDIA GPU (PoC targets; serving deployments use configured LLM endpoints, FR-42).
- **NFR-5** — For local Ollama use, the model picker auto-selects a model that fits detected hardware (RAM, GPU presence); Gemma 4 E4B is the PoC default for the 16 GB CPU-only target (PoC only; serving deployments pick from the configured roster, FR-42).

### LLM and store abstraction

- **NFR-6** — All LLM calls in NORA core flow through the `LLMProvider` Protocol (structural typing); providers are swappable by passing a different instance with no other code changes. The SIRA sandbox patch layer sits outside this scope (D-111 boundary) until SIRA integrates as a pipeline retrieval signal (see deferred FR-37).
- **NFR-7** — Embedding model, vector-DB backend, distance metric, and chunk contextualization toggles are configurable via a JSON-serializable `VectorStoreConfig`; `EmbeddingProvider` and `VectorStoreProvider` follow the same swap-by-instance Protocol pattern.

### Remote collaboration

- **NFR-8** — No compact report, error message, log, or test fixture contains proprietary MNO document content; only field names, regex patterns, IDs, keyword tokens, and counts may appear.
- **NFR-9** — Any new artifact type that crosses the AI-collaboration boundary ships with its compact-report schema, QC template, and error-code prefix in the same change that introduces it.

### Observability

- **NFR-10** — The metrics middleware is fire-and-forget and never blocks an HTTP response.
- **NFR-11** — KPIs are captured across five categories (REQ / LLM / PIP / RES / MET) and persisted to an SQLite metrics DB in the configured state directory (`<env_dir>/state/` by default; the per-stack web-state directory in serving deployments, FR-29).
- **NFR-12** — Resource sampling reads from `/proc` and `nvidia-smi` directly; `psutil` is not a runtime dependency.

### Data integrity

- **NFR-13** — The pipeline is re-runnable and idempotent per stage; partial re-runs auto-pick-up corrections from `<env_dir>/corrections/*.json`.

### Accuracy

- **NFR-15** — Acceptance is measured by the golden eval of FR-38: Stage-1 retrieval recall and the Stage-2 judge score, each against targets the project owner sets per release.
- **NFR-16** — Acceptance against NFR-15 uses the expert-curated golden set only; synthetic Q&A may augment development iteration but does not count toward NFR-15.

## Deferred

<!--
Requirements explicitly postponed. Not drift. Drift-check surfaces these as notes.
Entry format: **FR-N** — <requirement> (deferred: <why> — revisit: <trigger or date>)
-->

- **FR-22** — Single-requirement compliance check against Excel compliance sheets (deferred: post-PoC compliance agent — revisit: when v1 PoC accuracy passes NFR-15).
- **FR-23** — Cross-document compliance consistency check (deferred: post-PoC compliance agent v1 — revisit: after FR-22 lands).
- **FR-24** — Auto-fill from module / chipset documentation (deferred: post-PoC compliance agent v2 — revisit: after FR-22 and FR-23 land).
- **FR-25** — Delta compliance sheet generation between releases (deferred: post-PoC compliance agent v2 — revisit: when a multi-release corpus exists).
- **FR-26** — Test-case parsing (separate parser for test case documents) populates Test_Case nodes in the unified knowledge graph and Test_Case-related edges per FR-7's schema (deferred: post-v1 — revisit: when a test-case corpus is identified for ingestion).
- **FR-27** — Extraction of DOC and XLS legacy formats (DOC converted to DOCX via LibreOffice headless per the format-aware extraction design) (deferred: not required for v1 corpus — revisit: when a corpus containing DOC or XLS files needs ingestion).
- **FR-37** — SIRA select-synth becomes a weighted retrieval signal inside the full NORA query pipeline (graph-scoped RAG); today SIRA is the serving path on its own (FR-40) (deferred: future work — the full NORA pipeline (knowledge graph and graph-scoped RAG); built, but the stages beyond taxonomy are disabled in current builds (D-254) — revisit: when the knowledge-graph work resumes).
- **FR-5** — Standards ingestion fetches referenced 3GPP specs from a pluggable source (public mirror by default, or the 3GPP archive — D-025), parses each spec to a section tree, and extracts the referenced sections plus their parent, definitions, and adjacent siblings (Option C Hybrid) (deferred: future work — the full NORA pipeline (knowledge graph and graph-scoped RAG); built, but the stages beyond taxonomy are disabled in current builds (D-254) — revisit: when the knowledge-graph work resumes).
- **FR-7** — The unified knowledge graph aggregates Requirements, Features, Standard_Sections, and Plan / Release / MNO organization with typed edges across organizational, within-doc, cross-doc, standards, and feature categories; Test_Case node and edge types follow with FR-26 (deferred: future work — the full NORA pipeline (knowledge graph and graph-scoped RAG); built, but the stages beyond taxonomy are disabled in current builds (D-254) — revisit: when the knowledge-graph work resumes).
- **FR-8** — The vector store keeps one store per (MNO, release) cell (D-096), chunks requirements with metadata filters keyed on `mno`, `release`, `doc_type`, and `plan_id`, and merges the cells at query time (D-107) (deferred: future work — the full NORA pipeline (knowledge graph and graph-scoped RAG); built, but the stages beyond taxonomy are disabled in current builds (D-254) — revisit: when the knowledge-graph work resumes).
- **FR-11** — Graph scoping identifies candidate requirement nodes via graph traversal before any vector retrieval is attempted (deferred: future work — the full NORA pipeline (knowledge graph and graph-scoped RAG); built, but the stages beyond taxonomy are disabled in current builds (D-254) — revisit: when the knowledge-graph work resumes).
- **FR-12** — Targeted vector RAG ranks only within the graph-scoped candidate set with metadata filters; un-scoped pure-RAG retrieval is reserved for the A/B baseline mode (deferred: future work — the full NORA pipeline (knowledge graph and graph-scoped RAG); built, but the stages beyond taxonomy are disabled in current builds (D-254) — revisit: when the knowledge-graph work resumes).
- **FR-13** — Context assembly enriches retrieved chunks with hierarchical context (parent requirements, referenced Standard_Sections, cross-doc dependencies) before LLM synthesis (deferred: future work — the full NORA pipeline (knowledge graph and graph-scoped RAG); built, but the stages beyond taxonomy are disabled in current builds (D-254) — revisit: when the knowledge-graph work resumes).
- **FR-21** — The evaluation framework runs a user-supplied Q&A set, scores each question across five metrics (completeness, accuracy, citation quality, standards integration, hallucination-free), and produces A/B comparisons between graph-scoped and pure-RAG retrieval (deferred: future work — the full NORA pipeline (knowledge graph and graph-scoped RAG); built, but the stages beyond taxonomy are disabled in current builds (D-254) — revisit: when the knowledge-graph work resumes).
- **NFR-14** — Standards resolution is release-aware: each Standard_Section node carries its 3GPP release, and different MNO releases may reference different 3GPP releases of the same spec (deferred: future work — the full NORA pipeline (knowledge graph and graph-scoped RAG); built, but the stages beyond taxonomy are disabled in current builds (D-254) — revisit: when the knowledge-graph work resumes).
