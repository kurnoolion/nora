# Requirements

Last updated: 2026-05-01. Behavioral specs only — project identity and scope live in `PROJECT.md`.

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
- **FR-2** — The DocumentProfiler derives a structure profile (heading detection, requirement-ID pattern, document zones, cross-reference patterns, body-text signature) from representative documents without invoking an LLM.
- **FR-3** — A profile-driven generic structural parser produces a RequirementTree from any MNO's documents using only the profile and detected patterns; no per-MNO code paths.
- **FR-4** — Cross-reference extraction captures internal, cross-plan, and 3GPP standards references with section number and release.
- **FR-5** — Standards ingestion fetches referenced 3GPP specs from the 3GPP archive, parses each spec to a section tree, and extracts the referenced sections plus their parent, definitions, and adjacent siblings (Option C Hybrid).
- **FR-6** — The feature taxonomy is derived bottom-up by per-document LLM extraction, then consolidated across documents into a unified taxonomy; the workflow includes human review through the Web UI.
- **FR-7** — The unified knowledge graph aggregates Requirements, Features, Standard_Sections, and Plan / Release / MNO organization with typed edges across organizational, within-doc, cross-doc, standards, and feature categories. The schema also defines Test_Case node and edge types; Test_Case nodes are populated post-v1 (see FR-26).
- **FR-8** — The unified vector store chunks requirements with metadata filters keyed on `mno`, `release`, `doc_type`, and `plan_id`.
- **FR-31** — Priority-marker extraction in headings. The structural parser detects an optional priority marker embedded in heading text (e.g., `mandatory`, `optional`, `conditional`) via a profile-defined regex; when matched, the marker is stored as a `priority` attribute on the `Requirement` node and stripped from the displayed title. Profile carries `heading_detection.priority_marker_pattern`; corpora without the convention leave it empty. (Verizon OA does not use this convention; some other carriers do.)
- **FR-32** — Form-factor applicability with hierarchical inheritance. Each `Requirement` carries an `applicability` attribute (free-form `list[str]`, e.g. `["smartphone", "tablet"]`) listing the form factors the requirement applies to. The parser populates this by (a) extracting an explicit value from the requirement's own text or attached table when present, via a profile-defined regex / keyword set, and (b) inheriting from the parent requirement when absent, recursing up the hierarchy until a value is found. When the document declares a top-level applicability section, its value serves as the root default. When no applicability information exists anywhere in the corpus, the attribute is left empty and downstream stages do not filter on it. Controlled-vocabulary form-factor labels are deferred until a second carrier needs the consistency.
- **FR-33** — Strikeout content omission. The extractors for all supported formats (PDF, DOCX, XLSX) surface strikeout formatting on `FontInfo.strikethrough`; the parser drops content blocks where `font_info.strikethrough` is true, representing requirements / sections deleted in the original document. Profile carries an `ignore_strikeout: bool = True` switch (override available through the corrections workflow per FR-15).
- **FR-34** — Table of Contents omission. The profiler detects TOC entries by their leader-dot-and-page-number signature (default regex `<title>\.{3,}\s*\d+\s*$`); the parser skips matching blocks. Profile carries `toc_detection_pattern` (regex, default as above) and `toc_page_threshold` (float in `[0, 1]`, default 0.8 — a page is treated as a TOC page when at least this fraction of its blocks match the pattern). Both fields are profile-tuneable.
- **FR-35** — Definitions / acronyms expansion for retrieval. The profiler detects each document's definitions / acronyms / glossary section by heading-text regex (default `(?i)acronym|definition|glossary`) and extracts entry pairs (`term → expansion`) into a per-document `definitions_map`. The chunk builder expands the first occurrence of each known term inline in every chunk before embedding (e.g., `ETWS` → `ETWS (Earthquake and Tsunami Warning System)`) to improve vector recall on acronym-shaped queries. Expansion is per-document, not corpus-wide, to preserve locality when a term means different things in different MNO documents.

### Query

- **FR-9** — Query analysis classifies each query into one of 8 types (single-doc / cross-doc / cross-MNO comparison / release diff / standards comparison / traceability / feature-level / general) and extracts MNO + release scope from the natural-language input.
- **FR-10** — MNO / release resolution maps user-supplied names and dates to canonical graph scope: explicit scope is preserved as-is, "latest" resolves to the newest release in scope, and missing scope expands to all available.
- **FR-11** — Graph scoping identifies candidate requirement nodes via graph traversal before any vector retrieval is attempted.
- **FR-12** — Targeted vector RAG ranks only within the graph-scoped candidate set with metadata filters; un-scoped pure-RAG retrieval is reserved for the A/B baseline mode.
- **FR-13** — Context assembly enriches retrieved chunks with hierarchical context (parent requirements, referenced Standard_Sections, cross-doc dependencies) before LLM synthesis.
- **FR-14** — LLM synthesis emits an answer with grounded citations to specific requirement IDs and 3GPP spec sections; when the LLM omits citations, a context-based fallback supplies them from the assembled context.

### Corrections and overrides

- **FR-15** — On re-run, the pipeline reads human-edited overrides from `<doc_root>/corrections/*.json` and prefers them over auto-generated outputs.
- **FR-16** — The Web UI provides in-browser editing of document profiles and feature taxonomies; saved edits land in the same files the pipeline reads as overrides under FR-15.

### Remote collaboration

- **FR-17** — Every pipeline-stage failure raises a `PipelineError` carrying a stable, registry-defined error code (e.g., `EXT-E001`); verbose logs persist to disk for self-service debugging in environments where the AI partner cannot see runtime artifacts.
- **FR-18** — The system emits compact reports in four formats — RPT (per-stage pipeline summary), MET (metrics), FIX (corrections diff), QC (quality-check templates) — paste-ready for chat-mediated collaboration.

### Web UI

- **FR-19** — The Web UI exposes pipeline-job submission, real-time SSE log streaming, an SQLite-backed job queue, shared-folder browsing with Windows↔Linux path mapping, the query interface, environment CRUD, the corrections editor, and the metrics dashboard, all under a configurable reverse-proxy `root_path`.
- **FR-20** — The Web UI runs without an npm or JS build toolchain (FastAPI + Jinja2 + HTMX + vendored Bootstrap 5) and serves all static assets locally.

### Evaluation

- **FR-21** — The evaluation framework runs a user-supplied Q&A set, scores each question across five metrics (completeness, accuracy, citation quality, standards integration, hallucination-free), and produces A/B comparisons between graph-scoped and pure-RAG retrieval.

### Runtime environment

- **FR-28** — The pipeline accepts a per-environment working directory (`env_dir`) via CLI argument, environment config file, or Web UI input; the system does not assume any hardcoded paths for runtime artifacts.
- **FR-29** — All runtime artifacts are written under `<env_dir>`, partitioned by purpose: `out/` for pipeline outputs (extracted IR, parsed trees, cross-reference manifests, taxonomies, downloaded standards, knowledge graph snapshots, vector store data); `state/` for runtime SQLite databases (job queue, metrics); `corrections/` for human overrides; `reports/` for compact RPT / MET / FIX / QC outputs; `eval/` for user-supplied Q&A. No artifact is written outside `<env_dir>`.
- **FR-30** — Source documents are read from `<env_dir>/input/<MNO>/<release>/`; the pipeline does not read source documents from the repository root or from any path outside `<env_dir>`.

## Non-functional

### Deployment and install

- **NFR-1** — Production runs fully on-premise; no external cloud AI service is invoked on proprietary MNO content.
- **NFR-2** — Offline / air-gapped install of the Ollama runtime, model weights (gemma3:12b, gemma4:e4b), and the HuggingFace sentence-embedding model works without internet access.
- **NFR-3** — All Web UI static assets (Bootstrap, Bootstrap Icons, HTMX) are vendored locally; runtime never fetches from a CDN.

### Resource constraints

- **NFR-4** — The PoC pipeline runs to completion on a personal PC with 16 GB RAM and CPU-only inference; the work-laptop target additionally supports a 16 GB NVIDIA GPU.
- **NFR-5** — The model picker auto-selects an Ollama model that fits detected hardware (RAM, GPU presence); Gemma 4 E4B is the PoC default for the 16 GB CPU-only target.

### LLM and store abstraction

- **NFR-6** — All LLM calls flow through the `LLMProvider` Protocol (structural typing); providers are swappable by passing a different instance with no other code changes.
- **NFR-7** — Embedding model, vector-DB backend, distance metric, and chunk contextualization toggles are configurable via a JSON-serializable `VectorStoreConfig`; `EmbeddingProvider` and `VectorStoreProvider` follow the same swap-by-instance Protocol pattern.

### Remote collaboration

- **NFR-8** — No compact report, error message, log, or test fixture contains proprietary MNO document content; only field names, regex patterns, IDs, keyword tokens, and counts may appear.
- **NFR-9** — Any new artifact type that crosses the AI-collaboration boundary ships with its compact-report schema, QC template, and error-code prefix in the same change that introduces it.

### Observability

- **NFR-10** — The metrics middleware is fire-and-forget and never blocks an HTTP response.
- **NFR-11** — KPIs are captured across five categories (REQ / LLM / PIP / RES / MET) and persisted to SQLite at `web/nora_metrics.db`.
- **NFR-12** — Resource sampling reads from `/proc` and `nvidia-smi` directly; `psutil` is not a runtime dependency.

### Data integrity

- **NFR-13** — The pipeline is re-runnable and idempotent per stage; partial re-runs auto-pick-up corrections from `<doc_root>/corrections/*.json`.
- **NFR-14** — Standards resolution is release-aware: each Standard_Section node carries its 3GPP release, and different MNO releases may reference different 3GPP releases of the same spec.

### Accuracy

- **NFR-15** — The query pipeline achieves at least **90% on the weighted overall score** defined in `src/eval/metrics.py` (completeness 0.30 + accuracy 0.25 + citation quality 0.20 + standards integration 0.15 + hallucination-free 0.10) for the v1 VZW corpus on the user-curated A/B eval Q&A set.
- **NFR-16** — Acceptance against NFR-15 is measured on the user-curated Q&A set only; synthetic Q&A may augment development iteration but does not count toward NFR-15.

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
