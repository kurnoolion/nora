# Decisions

*Entries D-001 through D-012 below were reconstructed on 2026-04-21 during `project-init --retrofit`. They anchor pre-COMPACT architectural choices already baked into the codebase. Rationale is drawn from `docs/compact/design-inputs/SESSION_SUMMARY.md` and the TDD. Consequences are partially captured; edit to fill gaps as team knowledge surfaces.*

<!--
Template for new entries:

## D-XXX: Short descriptive title
**Status**: Active
**Date**: YYYY-MM-DD
**Context**: What problem prompted this decision?
**Decision**: What was chosen?
**Why**: Reasoning; alternatives considered in passing.
**Consequences**: What does this force or rule out?
**Alternatives considered** *(optional, for non-trivial decisions)*:
  - Option X — rejected because ...
**Supersedes** / **Superseded by** *(optional)*:
  - [D-XXX](#d-xxx)
-->

---

## D-001: KG + RAG hybrid over pure vector RAG

**Status**: reconstructed (active)
**Date**: 2026-04-21
**Context**: Pure vector RAG was evaluated for MNO requirement Q&A and failed — no relationship awareness, undirected retrieval scope, destroyed hierarchical structure, poor telecom-terminology handling, missing standards context, no MNO/release awareness.
**Decision**: Combine a unified Knowledge Graph (routes the query — determines WHERE to look) with targeted vector RAG (ranks within scope — determines WHAT is most relevant) and the requirement hierarchy (provides structural CONTEXT for LLM synthesis).
**Why**: Graph traversal captures cross-document, cross-MNO, and cross-release relationships that pure vector RAG cannot follow. Vector retrieval remains valuable for semantic ranking within a pre-scoped candidate set.
**Consequences**: Every downstream module depends on this split — `src/graph/` owns routing, `src/vectorstore/` owns ranking, `src/query/` orchestrates. Retrieval must always be scoped; unscoped vector search is a hard-flag.
**Alternatives considered**: pure vector RAG (rejected — documented failure modes); pure graph traversal (rejected — loses semantic ranking).

---

## D-002: Single unified graph + vector store, not MxN partitioned

**Status**: reconstructed (active)
**Date**: 2026-04-21
**Context**: Multi-MNO × multi-release scale could be handled either by one partitioned store per (MNO, release) pair or by a single unified store with metadata filters.
**Decision**: Single unified graph and single unified vector store span all MNOs and releases. Logical partitioning is via metadata attributes (`mno`, `release`, `doc_type`). Standards nodes and Feature nodes are shared across partitions.
**Why**: Enables cross-MNO comparison queries, cross-release version diffs, and shared standards/feature references as natural graph traversals — without the complexity of merging results from separate stores. Cross-MNO comparison was a core capability; partitioned stores would have made it expensive.
**Consequences**: Every node and chunk must carry MNO / release / doc_type metadata. Filters are enforced at every retrieval path. Graph size grows with total corpus (acceptable for planned scale).
**Alternatives considered**: MxN partitioned (rejected — cross-MNO comparison becomes a merge operation with correctness risks).

---

## D-003: Profile-driven generic structural parser, no per-MNO code

**Status**: reconstructed (active)
**Date**: 2026-04-21
**Context**: Different MNOs have different document formats. The naive approach is a parser per MNO; this creates a maintenance burden that grows with every new MNO and every format change.
**Decision**: A standalone, LLM-free `DocumentProfiler` derives a document structure profile (headings, req-ID pattern, zones, cross-ref patterns, body-text signature) from representative documents and emits human-editable JSON. A `GenericStructuralParser` applies the profile to any MNO's documents and emits a `RequirementTree`. Adding a new MNO requires profiling a representative doc — no code changes.
**Why**: Eliminates per-MNO parser drift. Keeps LLM out of the structural path (determinism, speed). JSON profile is human-reviewable and correctable without code changes.
**Consequences**: Profile quality is critical — wrong profile produces wrong tree across all of that MNO's docs. Validation against held-out docs is a required step. `src/profiler/` and `src/parser/` must remain decoupled (profiler emits profile; parser consumes it).
**Alternatives considered**: per-MNO parser registry (rejected — superseded by this decision); LLM-driven parsing (rejected — cost, latency, determinism).

---

## D-004: Option C Hybrid Selective for standards ingestion

**Status**: reconstructed (active)
**Date**: 2026-04-21
**Context**: MNO requirements reference 3GPP / GSMA / OMA specs extensively. Three ingestion options: (A) full specs, (B) only cited sections, (C) cited sections plus surrounding context.
**Decision**: Option C — ingest referenced 3GPP sections plus parent section, adjacent subsections, and definitions. Aggregate references by `(spec, release)` across the corpus; download once per combination.
**Why**: Full specs are prohibitively large and mostly irrelevant. Section-only misses context that makes the requirement interpretable. Option C preserves interpretability while bounding ingest cost.
**Consequences**: `src/standards/` must resolve spec + release to FTP download URL, parse 3GPP DOCX section trees, and extract the referenced section plus its context window. Release-aware — different MNO releases may reference different 3GPP releases; separate `Standard_Section` nodes per release.
**Alternatives considered**: full-spec ingestion (rejected — size + noise); section-only ingestion (rejected — loses interpretability).

---

## D-005: Bottom-up LLM-derived feature taxonomy with mandatory human review

**Status**: reconstructed (active)
**Date**: 2026-04-21
**Context**: A feature taxonomy is needed to route cross-document queries ("device activation" spans SIM, UI, Network, Entitlement documents). Two approaches: pre-define the taxonomy from domain knowledge, or derive it bottom-up from documents.
**Decision**: LLM extracts candidate features per document; a consolidator merges and deduplicates across documents; a human review step is required before the taxonomy is consumed by the graph. Human edits land in `<doc_root>/corrections/taxonomy.json` and are preferred on re-run.
**Why**: Pre-defined taxonomies drift from real document content and require continuous manual tuning. Bottom-up derivation stays aligned with the corpus. LLM is used for extraction / consolidation only; human review prevents hallucinated features from reaching the graph.
**Consequences**: Pipeline runs require a human review checkpoint. Corrections workflow (D-011) is a hard dependency. Taxonomy quality is gated by review attention; unreviewed runs produce lower-quality answers.
**Alternatives considered**: pre-defined taxonomy (rejected — drift, manual tuning burden).

---

## D-006: LLM abstraction via `LLMProvider` Protocol (structural typing)

**Status**: reconstructed (active)
**Date**: 2026-04-21
**Context**: The system must support multiple LLMs — Claude for design-time work, Ollama for local PoC runtime, proprietary on-premise LLM for production — without caller code changes.
**Decision**: A `LLMProvider` Protocol in `src/llm/base.py` defines the LLM interface (`complete(prompt, system, temperature, max_tokens) -> str`). Any class with a matching `complete()` method satisfies the Protocol (structural typing, no inheritance required). Providers swap by instance.
**Why**: Protocol-based structural typing avoids inheritance lock-in while still enforcing the contract. Swap-by-instance keeps the choice of LLM at the edge (config / factory), not baked into caller code.
**Consequences**: No direct imports of `ollama`, `httpx`-to-Ollama, or any LLM SDK outside `src/llm/`. All LLM callers import the Protocol. Changing the Protocol signature is a hard-flag event — triggers architecture-phase review and a new DECISIONS entry.
**Alternatives considered**: ABC inheritance (rejected — lock-in, ceremony); direct provider-specific calls (rejected — production swap becomes a refactor).

---

## D-007: `EmbeddingProvider` and `VectorStoreProvider` Protocols (same pattern as LLMProvider)

**Status**: reconstructed (active)
**Date**: 2026-04-21
**Context**: Embedding models, vector databases, distance metrics, and chunk contextualization are all tunable dimensions. Hard-coding them makes experimentation expensive and swap-by-config impossible.
**Decision**: `EmbeddingProvider` Protocol (`src/vectorstore/embedding_base.py`) and `VectorStoreProvider` Protocol (`src/vectorstore/store_base.py`). Implementations: `SentenceTransformerEmbedder`, `ChromaDBStore`. Configuration via `VectorStoreConfig` (JSON-serializable) selects provider, model, metric, and chunking strategy.
**Why**: Enables A/B evaluation across embedding models and backends with no caller-side changes. Same pattern as D-006 keeps the codebase uniform.
**Consequences**: `chromadb` and `sentence-transformers` are imported only inside the vectorstore module. Experimentation is config-driven. Protocol change is hard-flag.
**Alternatives considered**: direct ChromaDB / sentence-transformers calls (rejected — parity with D-006, swap-by-config lost).

---

## D-008: Web UI via FastAPI + Bootstrap 5 + HTMX

**Status**: reconstructed (active)
**Date**: 2026-04-21
**Context**: Team members on Windows PCs need a non-terminal interface for pipeline submission, job monitoring, shared-doc browsing, queries, and corrections. Evaluated Streamlit, Gradio, Airflow, and the FastAPI + HTMX approach.
**Decision**: FastAPI (async server) + Bootstrap 5 + HTMX for partial-page updates + jinja2 for server-side rendering. Zero npm / JS build step. Static assets vendored for offline environments. Background jobs via `asyncio.create_task()`. Log streaming via SSE. Job queue persisted to SQLite via `aiosqlite`. Reverse-proxy compatible via `root_path`.
**Why**: Streamlit rejected — single-user session model incompatible with multi-user team use. Gradio rejected — ML-demo focus, wrong abstractions for pipeline ops. Airflow rejected — heavy operational dependency (scheduler, DB, web server) for a PoC. FastAPI + HTMX gives multi-user, async, partial updates, reverse-proxy friendly, zero JS build — all in a Python-native stack.
**Consequences**: Web UI code in `src/web/` is first-class — same design and test rigor as core pipeline modules. Static assets must be vendored; CDN fetches are a hard-flag. Multi-user auth / RBAC is deferred (Open question for production).
**Alternatives considered**: Streamlit (rejected — single-user); Gradio (rejected — wrong abstractions); Airflow (rejected — heavyweight).

---

## D-009: Metrics architecture — 5-category persistent SQLite, fire-and-forget middleware

**Status**: reconstructed (active)
**Date**: 2026-04-21
**Context**: Production and work-laptop runs execute on hardware the AI partner cannot access; accuracy, latency, and resource usage must be observable from compact reports. Metrics must not become a performance tax.
**Decision**: Five metric categories — REQ (endpoint timing), LLM (model performance via `OllamaProvider.last_call_stats`), PIP (stage timing), RES (CPU/RAM/GPU sampling via `/proc` + `nvidia-smi`), MET (custom). Persistent SQLite at `web/nora_metrics.db`. `MetricsMiddleware` is fire-and-forget — never blocks HTTP responses. `compact_report()` emits pasteable MET lines with no proprietary content.
**Why**: Categorization matches the debugging axes that matter (where is time / memory / accuracy spent). SQLite is zero-operational-overhead. Fire-and-forget ensures observability never degrades user experience. `/proc` + `nvidia-smi` avoids a `psutil` dependency and works in minimal environments.
**Consequences**: Every pipeline stage emits PIP metrics. Every LLM call emits LLM metrics. Every long stage emits RES samples. Metrics schema is an internal contract — changes are DECISIONS entries. No proprietary content in metric values.
**Alternatives considered**: Prometheus / Grafana (rejected — operational overhead for a PoC on restricted networks); psutil-based sampling (rejected — dependency not available in all deployment environments).

---

## D-010: Multi-format extraction via normalized `DocumentIR`

**Status**: reconstructed (active)
**Date**: 2026-04-21
**Context**: MNO documents ship in PDF, DOC, DOCX, XLS, and XLSX formats, with embedded OLE objects, images, and diagrams. Downstream modules (profiler, parser, graph) must work without per-format branching.
**Decision**: Format-aware extractors (`PDFExtractor`, `DOCXExtractor`, …) emit a common `DocumentIR` (ContentBlock, FontInfo, Position, BlockType). `DOC` is converted to DOCX via LibreOffice headless before extraction. Downstream modules consume only `DocumentIR` — format-agnostic.
**Why**: One IR schema isolates format-specific concerns to the extraction boundary. Downstream module contracts stay stable across format additions. pymupdf used for text + font metadata; pdfplumber for tables; table-region deduplication and font-group splitting handle mixed-font blocks.
**Consequences**: Adding a new format means a new extractor — downstream modules do not change. The `DocumentIR` schema is an internal contract; changes are DECISIONS entries. Font metadata must be preserved (profiler relies on font-size clustering for heading detection).
**Alternatives considered**: per-format downstream branches (rejected — O(formats × downstream modules) maintenance).

---

## D-011: Corrections override pattern — `<doc_root>/corrections/*.json` preferred on re-run

**Status**: reconstructed (active)
**Date**: 2026-04-21
**Context**: Auto-generated pipeline artifacts (profile, taxonomy) are imperfect — humans must correct them. Overrides must survive pipeline re-runs without being clobbered.
**Decision**: Auto-generated artifacts land under `<doc_root>/output/` (profile, taxonomy). Human-edited overrides land under `<doc_root>/corrections/` (same filenames). On every pipeline run, the pipeline copies `corrections/*.json` over the auto-generated output. Edits are durable across reruns without any merge tool.
**Why**: Simple file-based convention; no database, no merge logic, no lock management. Human authority is explicit — what's in `corrections/` wins. Pairs naturally with the `/switch-phase` review workflow.
**Consequences**: Every artifact type with a human-review need uses this convention — no parallel override channel. `src/corrections/` owns the diff / compactor / FixReport surfaces. Web UI (D-008) writes directly to `corrections/` JSON files.
**Alternatives considered**: in-place edit of auto-generated files (rejected — wiped on every re-run); database-backed overrides (rejected — operational overhead).

---

## D-012: Chat-mediated remote collaboration — stable error codes + compact reports

**Status**: reconstructed (active)
**Date**: 2026-04-21
**Context**: Production and work-laptop runs execute on proprietary docs and eval sets the AI partner cannot see; full artifacts cannot be pasted through chat. Debugging and correction workflows must work from short, no-proprietary-content text the user can type or paste.
**Decision**: (a) Every pipeline-stage failure emits a stable prefixed error code (`EXT-`, `PRF-`, `PRS-`, `RES-`, `TAX-`, `STD-`, `GRA-`, `VEC-`, `EVL-`, …) registered in `src/pipeline/error_codes.py`. Verbose logs persist to disk for self-service debugging. Collaboration surface is `code + user observation`. (b) Every artifact type that crosses the AI-collaboration boundary has a paired compact report format — RPT (pipeline), MET (metrics), FIX (corrections), QC (quality check). One record per line, no internal document content. (c) QC templates are fixed-field (numbers + Y/N, no prose); FIX is compact summaries of human overrides in `<doc_root>/corrections/`.
**Why**: The AI partner's blind spot is structural — no view of production artifacts. Compact formats + stable codes turn that blind spot into a tractable debugging surface. Pasting fits within chat limits. No-proprietary-content is a hard invariant — compact reports contain no MNO document text.
**Consequences**: Every new artifact type must ship with: (1) error-code prefix, (2) compact report schema, (3) QC template. `drift-check` and `close-session` hard-flag artifacts that lack these. This decision is the authority behind NFRs related to remote collaboration.
**Alternatives considered**: ad-hoc text dumps (rejected — proprietary-content leak risk + chat-limit overruns); verbose-log paste (rejected — too large, too sensitive).

---

## D-013: v1 PoC corpus narrowed to single-MNO (Verizon Feb 2026); multi-MNO ingestion is post-v1

**Status**: Active
**Date**: 2026-04-27
**Context**: Design intent (TDD §3, SESSION_SUMMARY) named multi-MNO scope (Verizon, AT&T, T-Mobile) "across multiple MNOs and quarterly releases" as the target. The v1 corpus, however, is 5 publicly-available VZW OA documents (Feb 2026 release) — a single MNO at a single release. Design intent and corpus diverged silently, blurring what v1 acceptance covers.
**Decision**: v1 ships against a single-MNO corpus only (Verizon Feb 2026). Cross-MNO and release-diff success criteria are explicitly post-v1. The graph and vector schemas (D-002) remain multi-MNO-ready: every node and chunk carries `mno` / `release` metadata, and ingesting AT&T / T-Mobile proprietary documents on-prem is the immediate post-v1 work item.
**Why**: PoC accuracy gate (NFR-15 ≥ 90% weighted overall) must be reachable before adding corpus complexity. Multi-MNO ingestion of proprietary documents requires on-prem proprietary-LLM integration that is itself out of v1 scope. Narrowing to one public MNO lets the team validate the architectural bet (KG + RAG > pure RAG) on a known dataset before adding the proprietary-data dimension.
**Consequences**: PROJECT.md In/Out scope must explicitly mark multi-MNO as post-v1. NFR-15 acceptance is measured on VZW only. Cross-MNO comparison and release-diff queries (FR-9 query types) are *capabilities the system supports* but not *outcomes verified in v1*. Adding the second MNO triggers re-evaluation of the KG memory ceiling (STATUS Flag).
**Alternatives considered**: multi-MNO from v1 (rejected — proprietary-doc handling and on-prem LLM not ready); v1 single-MNO multi-release (rejected — adds release-diff complexity without unlocking the architectural validation).

---

## D-014: Test_Case node and edge types kept in graph schema, populated post-v1

**Status**: Active
**Date**: 2026-04-27
**Context**: TDD design decision #11 makes Test_Case nodes a first-class graph citizen with their own parser, `tested_by` / `tests` edges, and `doc_type` metadata. Step 4 of the original PoC plan (test-case parsing) was deferred to focus on the requirements-document path. Schema vs. corpus mismatch left a question: strip Test_Case from the schema until the parser lands, or keep as schema-only?
**Decision**: The unified graph schema retains Test_Case node and edge types. v1 populates zero Test_Case nodes (no parser, no test-case corpus). FR-7 documents this; FR-26 (Deferred) parks the parser work.
**Why**: Schema stability across v1 and post-v1 avoids a future migration on persisted graph state. Test_Case is a *known* future requirement, not a hypothetical — keeping the schema slot reserved is cheaper than a later schema rev. Graph builder simply emits zero Test_Case nodes today.
**Consequences**: Graph builder code paths for Test_Case must compile but produce empty output. drift-check should not flag the schema slot as unused. Adding the test-case parser later requires only the parser module, taxonomy of test cases, and edge-emission logic — no graph schema change.
**Alternatives considered**: strip Test_Case from schema (rejected — future migration cost on persisted graph state); split into per-doc-type subgraphs (rejected — conflicts with D-002 unified-store decision).

---

## D-015: NFR-15 acceptance bar is weighted-overall ≥ 90%, not raw req-ID accuracy

**Status**: Active
**Date**: 2026-04-27
**Context**: User stated the v1 success criterion as "answer requirements queries for single MNO with 90%+ accuracy." The eval framework (FR-21, `src/eval/metrics.py`) computes five metrics per question (completeness, accuracy, citation quality, standards integration, hallucination-free) plus a weighted overall (0.30/0.25/0.20/0.15/0.10). "90% accuracy" admits two readings — the weighted overall, or the raw `accuracy` sub-metric (req-ID recall).
**Decision**: NFR-15 binds acceptance to the weighted-overall score ≥ 90% on the user-curated A/B eval Q&A set. Pure req-ID recall is part of the weighted score (25% weight) but not the standalone bar.
**Why**: The weighted overall is harder to game — a system can hit ≥ 90% req-ID recall by retrieving every plausible ID and still produce poor answers (low completeness, missing standards integration, no citations). The weighted score reflects the full eval surface and matches the design rationale that all five metrics encode acceptance-relevant dimensions.
**Consequences**: Eval reports must report both per-metric scores and the weighted overall. Changing any of the five weights is a hard-flag DECISIONS event (a different weighted-overall is a different acceptance bar). NFR-16 binds the dataset to user-curated Q&A only — synthetic Q&A cannot count toward acceptance.
**Alternatives considered**: raw req-ID accuracy ≥ 90% (rejected — gameable, doesn't reflect citation/standards quality); pass/fail per-metric AND combined (rejected — too many gates, hard to reason about).

---

## D-016: Production deployment runs behind authenticating reverse proxy; no in-app auth

**Status**: Active
**Date**: 2026-04-27
**Context**: PROJECT.md Out-of-scope already excluded "Multi-user authentication / RBAC on the Web UI". Production deployment still needs *some* authentication. Two options: reverse-proxy-handled (e.g., nginx + corporate SSO), or in-app auth.
**Decision**: Production deployment runs behind an authenticating reverse proxy. The system itself does not implement authentication. The Web UI's `root_path` config (D-008) already accommodates reverse-proxy deployment. Auth is a deployment responsibility, not a system feature.
**Why**: In-app auth would couple the system to a specific identity provider, contradict the on-prem-only constraint (NFR-1) by introducing IdP integration code paths that vary per deployment, and bloat the v1 surface. Corporate environments running this system already have authenticated reverse proxies (intranet SSO is standard). Reverse-proxy auth is the simplest interface — upstream identity is opaque to the system; the system trusts that traffic reaching it is authenticated.
**Consequences**: System never sees raw login traffic. No password storage, no session management, no IdP integration code. `root_path` config must be honored end-to-end (FR-19 explicit). Direct exposure of the FastAPI server without a reverse proxy is a deployment misconfiguration, not a v1 system bug.
**Alternatives considered**: in-app auth with FastAPI security primitives (rejected — couples to IdP, contradicts on-prem-simplicity); no auth at all (rejected — production cannot ship without auth).

---

## D-017: Domain-expert correction validation = architect FIX-report review (workflow rule, not code gate)

**Status**: Active
**Date**: 2026-04-27
**Context**: Telecom domain experts edit profile.json / taxonomy.json directly through the Web UI (D-011, FR-15, FR-16). Their edits flow into the next pipeline run automatically. Without a validation step, a bad correction silently corrupts downstream artifacts. The Contributors table flagged this as a missing validation channel.
**Decision**: Architect (or designated reviewer) reviews the compact FIX report for each correction-driven re-run before it executes. This is a workflow rule — not a code-enforced gate. The pipeline does not block on architect approval; FIX reports already exist (D-012) and strip proprietary content, making them paste-ready for chat-mediated review. The architect role in the Contributors table now explicitly owns this validation channel.
**Why**: Code-gated approval would require a workflow engine, multi-user role assignments, and approval state in SQLite — incompatible with v1's no-RBAC stance (Out of scope). FIX reports already encode exactly the diff a reviewer needs (added/removed features, regex changes, zone deltas) with no proprietary content. The pipeline is idempotent (NFR-13) — a bad correction can be reverted by editing the corrections file and re-running. Workflow rule + idempotent recovery is the lightest sufficient mechanism.
**Consequences**: Trusted-team assumption is explicit — small team, known reviewers. Architect must actually review FIX reports; sloppy review is the v1 failure mode. Bad-correction recovery path is "edit corrections file, re-run pipeline" — no rollback infrastructure needed. Contributors table shows the validation channel; missing it would be a soft flag at close-session.
**Alternatives considered**: code-gated approval workflow (rejected — RBAC scope creep, inconsistent with v1); no validation at all (rejected — was the original gap that prompted this decision).

---

## D-018: DOC and XLS legacy formats preserved in TDD design intent, parked as Deferred FR-27

**Status**: Active
**Date**: 2026-04-27
**Context**: TDD §5.1 and SESSION_SUMMARY decision #13 named multi-format extraction across PDF / DOC / DOCX / XLS / XLSX as a design pillar. v1 code implements PDF + DOCX. User committed to extending v1 with XLSX (FR-1). DOC and XLS were not explicitly committed for v1, but the design names them. Two paths to reconcile: trim TDD design intent down to PDF/DOCX/XLSX, or keep design intent intact and park DOC/XLS as Deferred FRs.
**Decision**: TDD §5.1 design intent is preserved as-is. DOC and XLS extraction land in `requirements.md` Deferred (FR-27) with revisit trigger "when a corpus containing DOC or XLS files needs ingestion". The format-aware extraction layer (D-010) is built to accommodate them — adding a new extractor is the single integration path, no downstream changes.
**Why**: Trimming TDD would erase a known-future capability that downstream modules already accommodate (per D-010, downstream modules consume only `DocumentIR`). Deferred FRs are the COMPACT mechanism for "known future, not v1" — cheap to maintain, drift-check treats them as `[DEFERRED]` not as drift. Preserving design intent also keeps the format-aware layer's purpose visible — without DOC/XLS in view, future readers might question why the abstraction exists at all.
**Consequences**: drift-check requirements layer will not flag DOC/XLS as missing. New extractors land as new modules; no design rev needed. If a corpus surfaces with DOC or XLS files before the post-v1 work, FR-27 is the parking spot to revive. R-vs-D consistency holds: design names 5 formats; requirements explicitly defers 2.
**Alternatives considered**: trim TDD §5.1 to PDF/DOCX/XLSX only (rejected — erases known-future capability that the abstraction was built for); silent omission from requirements (rejected — creates unowned drift between TDD and requirements.md).

---

## D-019: Three-tier code organization — `core/` + `customizations/` + `config/`

**Status**: Active
**Date**: 2026-04-27
**Context**: Up to D-018, all source code lives under `src/`. The AI / human collaboration boundary is implicit — human-curated profiles, AI-generated pipeline code, deployment-specific config, and proprietary-LLM provider scaffolds intermix with no structural cue to readers about what is safe to edit, what is generated, or what is settings.
**Decision**: Three top-level directories. `core/` holds AI-generated source (`core/src/` = current `src/`; `core/tests/` = current `tests/`). `customizations/` holds AI-scaffolded code that humans complete or fully own. `config/` holds per-module settings.
**Why**: Makes the AI / human collaboration boundary explicit in the file system. Diff reviews, `drift-check`, and `regen-map` can apply different rules per zone. Externalizing the boundary forces design-time clarity about which surfaces are AI-owned vs human-owned.
**Consequences**: Every existing MODULE.md path changes. CLI invocations change (`python -m core.src.pipeline.run_cli`). Imports change. `structure-conventions.md` rewritten. Drift-check authority paths update. Triggers a development-phase reorg session to execute the file moves.
**Alternatives considered**: keep flat `src/` (rejected — boundary stays implicit, drives confusion); two-tier `core/` / `customizations/` only (rejected — config is a distinct concern); package-internal split within `src/` (rejected — same readability problem as flat).

---

## D-020: Bi-directional `core ↔ customizations` dependency; no AI / human authorship distinction in git

**Status**: Active
**Date**: 2026-04-27
**Context**: D-019 raises the dependency-direction question between `core/` and `customizations/`. Common patterns are unidirectional (extensions plug into a core API). This project's reality has dependency flows in both directions.
**Decision**: `core/` and `customizations/` may import each other freely. Git commits do not mark AI vs human authorship — the directory of the change implies it. Manual edits to `core/` are *exceptional* (allowed for emergencies, not forbidden); the rule is "normally, core is regenerated by AI."
**Why**: `core`'s `LLMProvider` Protocol is consumed by `customizations`' proprietary-LLM provider, but `customizations`' profile data is consumed by `core`'s parser. Forcing unidirectional flow breaks one or the other. No authorship marking keeps tooling simple — the boundary is structural, not authorial.
**Consequences**: Drift-check must accept cycles between `core/` and `customizations/` as legal. `regen-map`'s `Depends on` extraction must recognize cross-boundary edges in either direction. No CI rule for "AI-only commits to core" — review process governs that.
**Alternatives considered**: unidirectional `customizations` → `core` only (rejected — breaks profile / config injection); explicit AI / human authorship metadata in commits (rejected — tooling burden, ambiguous on emergency edits); per-directory immutability flags (rejected — over-engineered for team scale).

---

## D-021: One config file per module under top-level `config/`; runtime DBs and per-env data are not config

**Status**: Active
**Date**: 2026-04-27
**Context**: Settings are currently scattered — `web/config.json` for the Web UI, defaults inlined in Python (LLM model picker, vector store config). No central place to find or override.
**Decision**: `config/<module>.json` per module that needs configuration. Loaded by the corresponding module via a documented entry-point. Runtime SQLite DBs (job queue, metrics) are *state*, not config — they live under `<env_dir>/state/`. Per-env user data (corrections, eval Q&A) lives under the env, not in `config/`.
**Why**: Centralized config is discoverable and swappable per-deployment. Per-module separation prevents one big change-magnet file. Excluding state and per-env data keeps `config/`'s purpose tight: deploy / install-time settings, edited rarely, committable to git.
**Consequences**: Modules read their own config file (no cross-module config reads). New module needing config = new file under `config/`. `config/README.md` documents each file's purpose. `web/config.json` migrates to `config/web.json` with import-path updates in the Web UI route modules.
**Alternatives considered**: single `config.json` (rejected — change-magnet, merge conflicts, hard to scope reads); config in each module's directory under `core/src/<module>/config.json` (rejected — buries config inside AI-generated zone, violates regenerable-core principle); environment-variables-only (rejected — JSON is more reviewable at the scale of settings here).

---

## D-022: Per-environment runtime directory `<env_dir>` as single root for input, output, state, corrections, reports, eval

**Status**: Active
**Date**: 2026-04-27
**Context**: Runtime data is scattered today — source PDFs at the repository root, generated artifacts under `data/`, runtime DBs under `web/`, corrections under `<doc_root>/corrections/`. No single root, hard to clean up between runs, hard to ship environment state via filesystem.
**Decision**: A single per-environment directory `<env_dir>` contains everything generated at runtime, partitioned by purpose: `input/<MNO>/<release>/*.pdf|docx|xlsx`, `out/{extracted,parsed,resolved,taxonomy,standards,graph,vectorstore}/`, `state/{nora.db, nora_metrics.db}`, `corrections/{profile.json, taxonomy.json}`, `reports/`, `eval/`. The env config in `environments/<name>.json` resolves a name to an `env_dir`; the path is passed via CLI `--env-dir`, environment-config field, or Web UI form.
**Why**: Single-root-per-env makes pipeline invocations self-contained (a single path argument is enough), supports per-env cleanup (`rm -rf <env_dir>` is safe), and lets env state be zipped and shipped. The repo stays free of generated artifacts.
**Consequences**: All file-writing modules update path resolution to take `env_dir` as a parameter rather than computing from globals or hard-coded paths. The `document_root` field in env config is renamed `env_dir` for clarity. Existing top-level `data/` is deprecated. Repo-root PDFs (LTE*.pdf) move to `<env_dir>/input/VZW/Feb2026/`. Web UI's runtime DBs move from `web/` to `<env_dir>/state/`. FR-28..FR-30 in `requirements.md` encode the behavioral contract.
**Alternatives considered**: per-artifact-type directories at repo root (rejected — what we have today; doesn't isolate envs, can't ship state); per-MNO root (rejected — confuses env identity with corpus identity); flat env dir (rejected — partitioning by purpose is the discoverability win).

---

## D-023: Source documents organized as `<env_dir>/input/<MNO>/<release>/`

**Status**: Active
**Date**: 2026-04-27
**Context**: Source PDFs live at the repository root today (LTESMS.pdf, LTEAT.pdf, ...) with no metadata partitioning. The pipeline currently infers MNO from filename or env config. Multi-MNO future work (post-v1) requires an explicit organizational scheme.
**Decision**: Source documents under `<env_dir>/input/<MNO>/<release>/`. MNO is the upper-case operator code (VZW, ATT, TMO). Release is the canonical release tag (e.g., `Feb2026`, `Oct2025`). The pipeline reads MNO and release from path, not filename.
**Why**: Path-encoded metadata is unambiguous and survives file renames. Aligns with multi-MNO post-v1 work — adding a new MNO is creating a new sub-directory, not a code change. Pairs naturally with FR-7's per-(MNO, release) graph metadata.
**Consequences**: Extraction layer's `infer_metadata_from_path` becomes authoritative — filename-based fallbacks become deprecated. Multi-MNO ingestion later is just adding directories.
**Alternatives considered**: flat `<env_dir>/input/*.pdf` with metadata in a sidecar JSON (rejected — sidecar drift, plus filename-based MNO inference is fragile); `<env_dir>/<MNO>/<release>/input/` (rejected — `input` is the conceptual category; MNO / release partitions under it).

---

## D-024: `customizations/` initial seeding — `profiles/` + proprietary-LLM provider boilerplate; directory grows as new human-touch surfaces are identified

**Status**: Active
**Date**: 2026-04-27
**Context**: D-019 establishes `customizations/` as the human-completion zone but leaves what initially lives there open. Two surfaces are currently human-curated or contain human-completion-required scaffolds: document profiles (`profiles/vzw_oa_profile.json` is hand-curated against held-out documents) and the proprietary-LLM provider boilerplate (AI-generated scaffold awaiting deployment-specific fill-in).
**Decision**: Initial `customizations/` contents: `customizations/profiles/<profile>.json` (was repo-root `profiles/`); `customizations/llm/<provider>.py` (proprietary-LLM provider boilerplate). Both with co-located tests under `customizations/profiles/tests/` and `customizations/llm/tests/`. Other modules stay in `core/`. As future work identifies surfaces that need human touchup (per-MNO extraction overrides, custom prompt templates, ...), they move to `customizations/` with their own DECISIONS entry recording the move.
**Why**: Profiles are explicitly human-curated against real documents — they are not regeneration candidates. The proprietary-LLM provider is sensitive code that varies per-deployment and must be human-reviewed end-to-end. Anchoring the convention with two concrete examples gives it shape rather than starting empty.
**Consequences**: Two file-tree moves in the development-phase reorg session. `profile_cli` loads profiles from `customizations/profiles/` rather than `profiles/`. LLM provider registration looks at both `core/src/llm/` (Ollama, mock, model picker) and `customizations/llm/` (proprietary). Test paths in pytest config update. CONTRIBUTING.md updates to point new contributors at `customizations/` for human-touch surfaces.
**Alternatives considered**: start `customizations/` empty (rejected — no anchoring example, weak convention); also include `corrections/` (rejected — corrections are *data* per-env, not code; they live under `<env_dir>/corrections/` per D-022 and FR-29); also include the extraction layer (rejected — extraction is generic and profile-driven; per-MNO overrides are forward work, will land in `customizations/` when needed).

---

## D-025: HuggingFace as default 3GPP standards source; DOCX over MD; 3GPP FTP retained as fallback

**Status**: Active
**Date**: 2026-04-28
**Context**: 3GPP FTP source took 1384s for 54 specs in the work-laptop run (~26s avg per download) — dominant time cost in the pipeline. Corporate proxies on the work laptop required a `NO_PROXY=localhost` workaround for Ollama; multi-host FTP archive (`ftp.3gpp.org` plus per-spec content endpoints) is harder to whitelist than a single domain. Some legacy specs ship as `.doc` requiring headless LibreOffice for DOC→DOCX — extra dep on locked-down hosts. User flagged the public `GSMA/3GPP` HuggingFace dataset as a candidate alternate source.
**Decision**: Add HuggingFace as a second spec source alongside the existing 3GPP FTP path, via a pluggable `SpecDownloader.source` parameter (`"huggingface"` | `"3gpp"`). HuggingFace becomes the **default**. Use the `original/Rel-{N}/{NN}_series/` DOCX side of the dataset, not the `marked/` Markdown side. Source choice precedence: CLI `--standards-source` > `NORA_STANDARDS_SOURCE` env var > `EnvironmentConfig.standards_source` > `"huggingface"`. New `core/src/standards/hf_source.py` uses stdlib `urllib` only — no `huggingface_hub` SDK dep. 3GPP FTP path unchanged and selectable.
**Why**: HF DOCX path is faster (594s vs 1384s observed = 2.3× speedup), goes through a single domain (easier corporate-proxy whitelisting), and ships only DOCX (no LibreOffice DOC→DOCX needed). DOCX over MD: `SpecParser` already targets DOCX so HF DOCX needs zero new parser code; MD coverage has gaps (Rel-20 still ingesting, only Rel-8..19 available); DOCX preserves font/style metadata and structural tables that the parser uses; MD's advantages (smaller files, pre-cleaned text) aren't load-bearing — we extract referenced sections + context once into `ExtractedSpecContent`, we don't ship raw specs anywhere. Default-flip to HF prioritizes the work-laptop case (dominant runner; proxy-constrained; benefits most from single-domain access). 3GPP FTP retained for full-coverage / lag-tolerance scenarios — HF mirrors authoritative content but lags upstream by hours-to-days.
**Consequences**: Default behavior changes for every existing environment that didn't explicitly set the source. Both sources land artifacts in the same `data/standards/TS_{spec}/Rel-{N}/` cache layout; downstream stages (parser, extractor) are source-agnostic; cache check arbitrates first regardless of source. LibreOffice becomes optional when source=huggingface (mandatory only for source=3gpp). `standards/MODULE.md` gains a new invariant: "Sources are interchangeable post-download — same `ResolvedSpec` schema; downstream stages source-agnostic." Adds dependency on `huggingface.co` availability for the default path; full outages fall through to manual `--standards-source 3gpp` until restored.
**Alternatives considered**: HF MD only (rejected — needs new MDSpecParser, has Rel-20 gap, loses font/style metadata that paragraph-anchored req detection relies on); HF as opt-in non-default (rejected — work-laptop is the primary runner and benefits most from HF; making it opt-in defers the speedup); single new `SpecSource` Protocol with full strategy-pattern refactor (rejected — `SpecDownloader` keeps its existing FTP-path implementation; an internal source-string switch is the smaller diff for the same swap-ability); `huggingface_hub` Python SDK (rejected — extra dep with no user-visible benefit over stdlib `urllib`; matches the `OllamaProvider` stdlib-only pattern).

---

## D-026: OpenAI-compatible LLM provider for cloud APIs; LLMProvider Protocol unchanged

**Status**: Active
**Date**: 2026-04-28
**Context**: Work-laptop pipeline runs against `gemma4:e4b` (4B local Ollama) ceilinged at 80.4% / 52.8% on the 18-Q OA eval. Model-quality lever was unmeasured — needed a stronger model to test whether the accuracy gap is model-bound or structural. DGX Spark on order but not yet shipped, so local-only path can't deliver the test today. OA documents are public (Verizon Open Access program) — no proprietary-content constraint blocking cloud APIs for that corpus.
**Decision**: New `OpenAICompatibleProvider` class in `core/src/llm/openai_provider.py` implementing the `LLMProvider` Protocol via structural typing (no inheritance). Single class works for any OpenAI Chat Completions endpoint — OpenRouter, Together AI, DeepInfra, Groq, Fireworks, vLLM/SGLang, and OpenAI itself — by swapping `base_url` + `api_key` + `model` (constructor args or `NORA_LLM_BASE_URL` / `NORA_LLM_API_KEY` / `NORA_LLM_MODEL` env vars). Stdlib `urllib` only, no `openai` / `httpx` SDK deps. Provider selected via `--llm-provider` CLI flag / `NORA_LLM_PROVIDER` env var / `EnvironmentConfig.model_provider` (`"ollama"` | `"openai-compatible"` | `"mock"`); precedence resolver in `env.config.resolve_llm_provider`. `LLMProvider` Protocol surface unchanged — caller modules (taxonomy, query, eval) consume it agnostic to provider class.
**Why**: OpenAI Chat Completions has become the de-facto cloud LLM API standard — one client class covers ~all providers we care about; per-provider classes would be over-engineering. Stdlib-only matches the `OllamaProvider` pattern, installs cleanly on offline-leaning hosts (no SDK pin / wheel-resolution issues), and avoids `openai` SDK's auth/retry surface that we don't need at this scale. Env-var driven config means secrets never enter committed config files. Cloud LLM is necessary now to bound the model question independent of DGX Spark availability — same model (Qwen3-235B-A22B per D-028) will run on Spark when it ships, so the integration is forward-compatible.
**Consequences**: Two LLM deployment paths now coexist. Cloud path is **only for non-proprietary corpora** (OA on dev PC); work-laptop / VoWiFi continues on Ollama. Cost surface introduced — sub-$1 per full pipeline run measured in practice but metered. Silent fallback to MockLLMProvider when `--llm-provider openai-compatible` is set but env vars missing has bitten us once (run A2 wasted ~30 min on mock-generated eval that looked plausible) — hardening (`require_real=True` when CLI flag explicit) is on the Next list. `last_call_stats` schema is identical across providers, so taxonomy/eval observability shape stays consistent. When DGX Spark arrives, switching from cloud to DGX is a two-env-var change (`NORA_LLM_PROVIDER=ollama` + appropriate `NORA_LLM_MODEL`) — zero code change.
**Alternatives considered**: per-provider classes (`OpenRouterProvider`, `TogetherProvider`, ...) — rejected; ~all share OpenAI Chat Completions schema, per-provider classes would duplicate code without benefit; `openai` SDK as the client — rejected, dep without value at this scale, and SDK's retry/auth/streaming surface goes unused; defer cloud entirely until DGX ships — rejected, blocks the model-as-variable measurement we need now and DGX ETA is unknown; commit Anthropic API client specifically — rejected, OA docs being public doesn't change the same-class-fits-many advantage; route through a third-party gateway (LangChain LiteLLM, etc.) — rejected, dep weight + opinions we don't share.

---

## D-027: Parser Requirements anchored from table cells in addition to paragraphs; paragraph wins on duplicate

**Status**: Active (regression observed; mitigation pending)
**Date**: 2026-04-28
**Context**: 138 of 301 internal cross-references on the OA corpus marked `broken` (46%) — investigation showed 126 unique target req-IDs are real (sequential blocks like `VZ_REQ_LTEB13NAC_36963..36968`) and exist in the source PDFs, but only inside table cells. The parser's `_is_req_id_block` heuristic only inspects paragraph blocks (small font + matches `requirement_id.pattern`), so table-cell-only req-IDs never become `Requirement` nodes. Eval breakdown showed citation=100% / accuracy=60.2% — answers grounded in available content were correct, but missing-table-content answers were fabricated. Diagnosis chain: parser ignores req-IDs in table cells → 126 Requirement nodes never created → 138 internal refs broken → table content never chunked into vectorstore → eval queries can't ground in missing reqs.
**Decision**: Add a table-cell req-ID anchor pass inside `GenericStructuralParser._build_sections`. For each `BlockType.TABLE` block under a current paragraph-anchored section: scan column 1 of each row first; if no IDs there, fall back to scanning all cells. One anchor per row maximum. Paragraph anchors win on duplicate `req_id`s (track `paragraph_req_ids` set during the walk; table-anchor pass dedups against it). Table-anchored Requirements have `section_number=""` and inherit `parent_section` / `parent_req_id` / `zone_type` from the surrounding paragraph-anchored section; `hierarchy_path` is filled by a new `_propagate_hierarchy_to_table_reqs` post-pass after `_link_parents`. Row content is serialized as text (preserves the column→value mapping that *is* the requirement's content). New invariant in `parser/MODULE.md`: every Requirement is anchored from exactly one source block — paragraph OR table.
**Why**: Telecom requirement docs frequently define requirements through cross-reference tables — without this fix, those reqs are invisible to NORA. Column-1-first heuristic matches the dominant OA layout (req-ID in column 1, attribute name + value in subsequent columns); all-cells fallback handles tables that use column 1 for a row label like "Req-1" with the req-ID in column 2. One-anchor-per-row prevents over-splitting when multiple cells happen to contain matches. Paragraph-wins-on-duplicate keeps precedence deterministic and preserves paragraph-anchored Requirements' richer body content (paragraph = full prose; table-row = thin keyed cells). Same `requirement_id.pattern` regex from the profile is reused — adding a new MNO still requires no parser code change. Single-pass during `_build_sections` (vs separate post-pass) reuses the heading-walk's `current_section` state and avoids recomputing block-to-section position lookup.
**Consequences**: +274 Requirements detected on the OA corpus (711 → 985); -34 unresolved internal refs (138 → 104); +21% graph edges; +19% vectorstore chunks; taxonomy features 28 → 35. **Soft-flag MODULE.md update**: invariant relaxed from "every Requirement has a section_number" to "every Requirement is anchored (paragraph OR table)" — `section_number=""` is now a valid state for table-anchored reqs. Existing `test_pipeline.py:test_requirements_have_section_numbers` rewritten as `test_every_requirement_is_anchored`. Downstream consumers (resolver, graph, taxonomy, vectorstore) automatically see the new Requirements without code change — schema unchanged. **Observed regression on the 18-Q OA eval set**: overall 86.2 → 81.7, accuracy 60.2 → 54.6, citation 100 → 94.4, completeness 88.9 → 86.1. Diagnosed as retrieval pollution — the new table-anchored thin chunks distract retrieval from canonical paragraph-anchored chunks the eval set was authored against. Eval Q&A doesn't ask about any of the 274 new req-IDs, so they're pure noise for these 18 questions despite being real Requirements. Mitigation paths open: enrich table-anchored chunks with parent-section context, retrieval-side boost paragraph anchors, broaden the eval set to cover table-anchored content, or roll back D-027. Resolution pending per-question diagnosis from `~/work/env_vzw/out/eval/report.json` (tracked in STATUS In Progress + Flags). 426 unit tests pass (10 new for this change).
**Alternatives considered**: column-1-only (rejected — misses OA tables that use column 1 for row-labels; verified empirically); all-cells without column-1 priority (rejected — over-triggers on tables where multiple cells contain IDs, splits one logical requirement across rows); table-wins-on-duplicate (rejected — discards paragraph context which is richer); skip detection in TOC-zone tables (deferred — `document_zones` doesn't currently distinguish a TOC zone, dedup against paragraph anchors handles the common case); separate post-pass after `_build_sections` (rejected — would have to recompute "current section at this block", duplicating logic); insist ground-truth eval covers table-anchored reqs before landing the parser change (rejected — chicken-and-egg, parser change is substantively correct; eval-set expansion is its own work).

---

## D-028: Qwen3-235B-A22B + OpenRouter as the best-case baseline configuration; same model targets DGX Spark when it ships

**Status**: Active
**Date**: 2026-04-28
**Context**: Need a "best-case" reference run to compare work-laptop `gemma4:e4b` numbers against — without one, accuracy gap on work-laptop can't be attributed to model vs structural causes. User has DGX Spark on order (128GB unified RAM, GB10 Grace-Blackwell Superchip) but no shipping ETA. Want a model that maps cleanly from cloud now to DGX later for operational consistency, and isolates the LLM as the only variable when comparing baselines across hardware.
**Decision**: Use **Qwen3-235B-A22B** as the best-case baseline model. Provider for now: **OpenRouter** (cloud); future: Ollama on DGX Spark. Switch via `--llm-provider` flag + env vars — same `OpenAICompatibleProvider` (D-026) implementation works against OpenRouter; same model name `qwen/qwen3-235b-a22b` resolves to the appropriate Ollama tag on DGX. Cloud-baseline runs are cleared only for the OA corpus (public); proprietary corpora (VoWiFi on the work laptop) remain on local Ollama only.
**Why**: Qwen3-235B-A22B is MoE (~22B active per token) — better quality-per-bandwidth on DGX Spark's memory-rich / bandwidth-modest profile (~273 GB/s LPDDR5X) than a dense 70B; cloud cost lower per query than dense alternatives. Hybrid thinking mode benefits query-synthesis and eval stages. Strong IFEval scores directly target the instruction-following gap that drove `profile_debug --create` failures with `gemma4:e4b` (model summarized instead of emitting JSON). Native 128K context comfortable for telecom-spec retrieval. Same model on cloud and DGX gives operational consistency: switching is a two-env-var change with no model-quality re-baseline needed. OpenRouter as the cloud provider: single API + key for ~100 models including Qwen3, Llama 3.3, DeepSeek-V3 — switch model by changing one env var if availability or quality issues arise; the ~10% markup over native (Together / DeepInfra) costs cents on a sub-$1 baseline run, well worth the optionality.
**Consequences**: Established baseline numbers: Run A (pre-parser-fix) overall=86.2% / acc=60.2% / citation=100%; Run A3 (post-parser-fix) overall=81.7% / acc=54.6% / citation=94.4%. Both via Qwen3-235B-A22B / OpenRouter on dev PC (Core Ultra 9 185H, 15 GB RAM, no GPU). Cost per full pipeline run measured ~$0.30-1; OpenRouter dashboard tracks live spend. When DGX Spark arrives, baseline replicates with two env-var changes and no model swap — first DGX run becomes a hardware-only re-baseline. Two corpora, two LLM setups codified: OA/dev-PC → OpenRouter cloud; VoWiFi/work-laptop → local Ollama with smaller model. Cloud path is operational only — never carries proprietary content.
**Alternatives considered**: Llama 3.3 70B Instruct (rejected — dense, no thinking mode, bandwidth-unfriendly on Spark; broadly available and a fine fallback if Qwen3 unavailable); DeepSeek-V3 (671B MoE — rejected, doesn't fit 128GB at any usable quant; cheap on DeepSeek's own API but Chinese hosting jurisdiction matters even for public docs); Qwen2.5-72B-Instruct dense at Q8 (~75GB) (rejected as primary — dense throughput on Spark's bandwidth profile is worse than MoE; kept as backup if 235B-MoE is unstable on Spark at launch); start with native cloud provider (Together/DeepInfra/Groq) for lower cost (rejected — OpenRouter's optionality is worth ~10% markup at this scale; Groq's catalog historically Llama-heavy and Qwen3-235B availability uncertain); cloud Anthropic/OpenAI proprietary models (rejected — explicit user constraint to use open-source models so cloud and DGX setups can match); cheaper open models (Mixtral, smaller Llamas) (rejected — IFEval gap is what hurts NORA; smaller models don't move the bottleneck).
