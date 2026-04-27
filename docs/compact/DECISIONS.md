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
