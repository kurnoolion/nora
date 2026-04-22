# Requirements

Last updated: 2026-04-21. Behavioral specs only — project identity and scope live in `PROJECT.md`.

*Skeleton seeded by `project-init --retrofit` on 2026-04-21. Candidate FR / NFR entries from `docs/compact/design-inputs/` are listed as comments under each section — review and promote (or reject) each during requirements phase. None are authoritative yet.*

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

<!--
Candidate FR entries extracted from design inputs (TDD §5, §7, §8, §10; SESSION_SUMMARY "Capabilities"). Promote, edit, or reject each during requirements phase.

Ingestion:
- FR candidate — The system extracts normalized document IR from PDF, DOC, DOCX, XLS, and XLSX inputs, preserving font metadata, table structure, images, and embedded OLE objects (TDD §5.1).
- FR candidate — DocumentProfiler derives a document structure profile (headings, req-ID pattern, zones, cross-ref patterns, body-text signature) from representative documents without LLM involvement (TDD §5.2).
- FR candidate — The generic structural parser applies a profile to any MNO's documents and emits a RequirementTree without per-MNO code (TDD §5.3).
- FR candidate — Cross-reference extraction captures internal, cross-plan, and standards (3GPP TS) references with section and release (TDD §5.5).
- FR candidate — Standards ingestion fetches 3GPP specs by version from the FTP site, extracts referenced sections + parent + adjacent subsections + definitions (Option C Hybrid, TDD §5.6).
- FR candidate — Feature taxonomy is derived bottom-up from documents via LLM, then consolidated across documents; human review is a required step (TDD §5.7).
- FR candidate — Unified Knowledge Graph construction produces nodes for Requirements, Test Cases, Features, Standards, Plan/Release, with 15+ typed edges (TDD §5.8, §6).
- FR candidate — Unified Vector Store construction chunks requirements with metadata filters for mno, release, doc_type (TDD §5.9).

Query:
- FR candidate — Query analysis classifies query type (single-doc / cross-doc / cross-MNO / cross-release / standards-comparison / traceability) and extracts MNO + release scope (TDD §7.1).
- FR candidate — MNO and release resolution maps user-supplied names/dates to canonical graph scope (TDD §7.2).
- FR candidate — Graph scoping identifies candidate requirement nodes via graph traversal before any vector retrieval (TDD §7.3).
- FR candidate — Targeted vector RAG ranks within the scoped candidate set with metadata filters; never runs unscoped (TDD §7.4).
- FR candidate — Context assembly produces hierarchical context (parent requirements, referenced standards, cross-doc dependencies) for LLM synthesis (TDD §7.5).
- FR candidate — LLM synthesis produces an answer with grounded citations back to specific requirement IDs and document sections (TDD §7.6).

Corrections and overrides:
- FR candidate — The pipeline reads human-edited overrides from `<doc_root>/corrections/*.json` on re-run and prefers them over auto-generated output (SESSION_SUMMARY §18, src/corrections).
- FR candidate — The Web UI allows in-browser editing of document profiles and taxonomies with file-backed storage matching the corrections convention (TDD §10.7).

Remote collaboration:
- FR candidate — Every pipeline-stage failure emits a stable prefixed error code registered in a catalog; verbose logs are persisted to disk for self-service debugging (SESSION_SUMMARY §18, src/pipeline/error_codes.py).
- FR candidate — Every artifact type that crosses the AI-collaboration boundary has a paired compact report format (RPT, MET, FIX, QC) containing no proprietary document content (interview topic 4a).
- FR candidate — The system accepts human QC feedback on intermediate artifacts (profile, taxonomy, eval) and human FIX edits in compact text format (interview topic 4a).

Web UI:
- FR candidate — The Web UI allows pipeline job submission, real-time log streaming via SSE, job queue with SQLite persistence, shared folder browsing with Windows↔Linux path mapping, query interface, environment CRUD, and reverse-proxy deployment (TDD §10).
- FR candidate — The Web UI runs with zero npm/JS build (FastAPI + Bootstrap 5 + HTMX + jinja2) and vendored static assets for offline environments (TDD §10.2, v0.6 change log).

Evaluation:
- FR candidate — The evaluation framework runs a user-supplied Q&A set, computes per-question scores, and produces A/B comparisons between LLM providers and pipeline configurations (src/eval/, SESSION_SUMMARY §18).
-->

- **FR-1** — <behavior>

## Non-functional

<!--
Candidate NFR entries extracted from design inputs (TDD §3, §10.5; SESSION_SUMMARY constraints; interview topic 4).

Deployment and install:
- NFR candidate — The system runs fully on-premise in production; no external cloud AI service is invoked on proprietary data (TDD §3).
- NFR candidate — Offline / air-gapped install of Ollama models and HuggingFace embeddings works without internet access (SETUP_OFFLINE.md, src/vectorstore/hf_offline.py).
- NFR candidate — Web UI static assets (Bootstrap, Bootstrap Icons, HTMX) are vendored; no CDN fetch at runtime (TDD §10.6).

Resource constraints:
- NFR candidate — The PoC pipeline runs on a personal PC with 16 GB RAM and CPU-only inference (interview topic 4; model-picker target).
- NFR candidate — The model picker auto-selects an Ollama model that fits detected hardware (RAM ceiling, GPU presence); Gemma 4 E4B is the PoC default (SESSION_SUMMARY §17).

LLM abstraction:
- NFR candidate — All LLM calls go through the `LLMProvider` Protocol; providers are swappable by instance without code changes elsewhere (SESSION_SUMMARY §15, src/llm/base.py).
- NFR candidate — Embedding model, vector DB backend, distance metric, and chunk contextualization are configurable via `VectorStoreConfig` (SESSION_SUMMARY §16).

Remote collaboration:
- NFR candidate — No compact report, error message, log, or test fixture contains proprietary MNO document content (interview topic 4a).
- NFR candidate — Any new artifact type that crosses the AI-collaboration boundary must include its compact report schema, QC template, and error-code prefix in the same change that introduces it (interview topic 4a).

Observability:
- NFR candidate — Metrics middleware is fire-and-forget and never blocks HTTP responses (TDD §10.5).
- NFR candidate — KPIs are captured across five categories (REQ / LLM / PIP / RES / MET) and persisted to SQLite at `web/nora_metrics.db` (TDD §10.5, SESSION_SUMMARY §20).
- NFR candidate — Resource sampling reads from `/proc` and `nvidia-smi` without requiring `psutil` (TDD §10.5).

Data integrity:
- NFR candidate — The pipeline is re-runnable and idempotent per stage; partial reruns pick up corrections from `<doc_root>/corrections/*.json` (src/pipeline/runner.py).
- NFR candidate — Standards resolution is version-aware: different MNO releases may reference different 3GPP releases; each referenced section is stored with its release qualifier (TDD §5.6, §6.5).
-->

- **NFR-1** — <constraint + measurable criterion if applicable>

## Deferred

<!--
Requirements explicitly postponed. Not drift. Drift-check surfaces these as notes.

Entry format:
- **FR-N** — <requirement> (deferred: <why> — revisit: <trigger or date>)

Candidate deferred items from design inputs (TDD §8.3 compliance agent — explicitly post-PoC):
- FR candidate — Single-requirement compliance check against Excel compliance sheets (deferred: post-PoC — revisit: when PoC accuracy passes A/B eval threshold).
- FR candidate — Cross-document compliance consistency check (deferred: post-PoC — revisit: after compliance agent v1).
- FR candidate — Auto-fill from module/chipset documentation (deferred: post-PoC — revisit: compliance agent v2).
- FR candidate — Delta compliance sheet generation between releases (deferred: post-PoC — revisit: compliance agent v2).
-->

<!-- (none yet — promote candidates above during requirements phase) -->
