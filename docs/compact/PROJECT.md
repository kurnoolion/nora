# Project: NORA — Network Operator Requirements Analyzer

*Identity: who / why / scope boundaries. Behavioral specs (FR / NFR) live in `requirements.md`.*

*Draft seeded from `docs/compact/design-inputs/` on retrofit init (2026-04-21). Refine during requirements phase.*

**One-line**: AI system combining a unified Knowledge Graph with targeted RAG to query, cross-reference, and answer compliance-shaped questions over US MNO device requirement specifications. v1 ships against a single-MNO (Verizon Feb 2026) corpus; schema and pipeline are multi-MNO-ready for post-v1 expansion.

**Problem**: US MNOs (Verizon, AT&T, T-Mobile) publish device requirement specifications quarterly — hundreds of documents per release, totaling GBs, referencing 3GPP/GSMA standards and customizing them with MNO-specific overrides. Device teams need to answer questions like *"how does VZW differ from TMO on IMS registration?"*, *"what changed in VZW eSIM from Oct 2025 to Feb 2026?"*, and *"is this chipset compliant with the VZW Feb 2026 data-retry requirements?"* Pure vector RAG was tried and fails — it cannot follow cross-document dependencies, destroys hierarchical structure, misses standards context, and has no MNO/release awareness. NORA replaces the bespoke per-MNO parser model with a generic profile-driven parser plus a KG routing layer and a targeted RAG ranking layer.

**Users**:

- Solution architects and AI + telecom experts driving the system design and curating its outputs
- Python developers implementing pipeline stages, web UI, and integrations
- Telecom domain experts reviewing extracted profiles, taxonomies, and query answers through a web UI (no CLI required)
- Device teams on Windows PCs submitting pipeline jobs and running queries

**In scope for v1 (PoC)**:

- Ingestion pipeline across 5 publicly available VZW OA documents (Feb 2026 release) — v1 corpus is single-MNO, single-release
- DocumentProfiler (LLM-free) + generic structural parser driven by JSON profiles
- Cross-reference resolution (internal, cross-plan, standards)
- Standards ingestion (3GPP, section-level selective — "Option C Hybrid")
- Feature taxonomy derivation (bottom-up, LLM-assisted, human-reviewed)
- Unified knowledge graph (networkx) — v1 holds VZW × Feb 2026; schema supports multi-MNO × release × doc-type for post-v1 expansion
- Unified vector store (ChromaDB) with metadata filtering
- Query pipeline with MNO/release resolution, graph scoping, targeted RAG, LLM synthesis
- Web UI (FastAPI + HTMX + Bootstrap 5) with pipeline submission, job queue, query interface, corrections workflow
- Compact reporting formats (RPT / MET / FIX / QC) and stable error codes for remote debugging
- Offline install path for Ollama + HuggingFace embeddings

**Out of scope (explicit non-goals)**:

- Compliance agent (post-PoC — covers single-requirement compliance against Excel sheets, cross-doc consistency, auto-fill from chipset docs, delta compliance between releases)
- Multi-MNO corpus in v1 (graph and vector schema are multi-MNO-ready; ingesting AT&T / T-Mobile proprietary documents on-prem follows v1)
- Test-case parsing in v1 (Test_Case node type preserved in graph schema per TDD design decision #11; separate parser deferred post-v1)
- Production deployment on proprietary MNO data
- Integration with proprietary on-premise LLM (PoC uses Claude / local Ollama)
- Hard-coded per-MNO parsers (replaced by profile-driven generic parser)
- Multi-user authentication / RBAC on the Web UI
- Real-time ingestion (batch pipeline is sufficient for quarterly release cadence)

**Success criteria**: Query pipeline answers requirements-related questions against the v1 VZW corpus at the accuracy bar set in `requirements.md` (NFR threshold ≥ 90% on user-curated Q&A); cross-doc queries succeed across the 5-doc corpus; A/B eval shows KG+RAG outperforming pure-RAG baseline on the user-supplied Q&A; full pipeline runs end-to-end on 5 VZW docs from both CLI and Web UI. *Cross-MNO and release-diff success criteria are post-v1 — no multi-MNO / multi-release corpus in v1.*

**Open questions** *(maintained during Requirements phase; removed when resolved or deferred)*:

*(none — all 7 triaged 2026-04-27: 2 moved to STATUS.md Flags, 1 became NFR-16 in `requirements.md`, 1 became a Constraint below, 3 dropped as resolved or post-v1.)*

**Constraints** *(persistent, not phase-scoped)*:

- On-premise only in production; no external cloud AI for proprietary data
- Offline / air-gapped install must work for Ollama and HuggingFace
- Resource-constrained inference: 16 GB RAM CPU-only (personal PC); 16 GB NVIDIA GPU (work laptop)
- Chat-mediated remote collaboration: AI partner cannot see production / work-PC artifacts; all cross-environment collaboration must use compact reports + stable error codes + no proprietary content
- Production deployment runs behind an authenticating reverse proxy; the system itself does not implement authentication

**Contributors**:

| Stakeholder / Role | Contributes | Interface | Feedback loop | Validation channel |
|---|---|---|---|---|
| Solution architect (AI + telecom SME) | Design direction, requirement curation, profile / taxonomy review, query validation | Direct file edit + CLI + Web UI | DECISIONS log on each session close; compact RPT / QC / FIX reports pasted into chat | Self-review against TDD + design-inputs; `/drift-check` audits before phase transitions |
| Python dev team | Pipeline stage code, web UI, tests, error-code registrations, metrics emission | Direct git + pytest + MODULE.md edits | PR review; `/drift-check dev-module <name>` after material changes | pytest suite (426 tests) + integration tests on real parsed data; module-level drift-check |
| Telecom domain experts / reviewers | Corrections on extracted profiles, taxonomies, citation flags, query validation | Web UI (Bootstrap 5 + HTMX); direct JSON edit under `<doc_root>/corrections/` | Pipeline re-run auto-picks corrections; FIX reports summarize overrides | Architect review of the compact FIX report before each correction-driven pipeline re-run |
| Windows-PC team members | Pipeline job submission, shared-doc browsing, Q&A usage | Web UI + shared folder with Windows↔Linux path mapping | SSE log stream; MET metrics dashboard | Architect / domain-expert spot-check of produced answers; eval Q&A accuracy |
| AI partner (Claude / proprietary LLM / Ollama) | Feature tagging, query synthesis, citation, design reasoning | Structured prompts via `LLMProvider` Protocol | `OllamaProvider.last_call_stats`; compact reports from remote environments | A/B eval framework (graph-scoped vs pure-RAG); citation-quality scoring against expected req IDs |

*Every row names who, what they contribute (Contributes), how they submit it (Interface), how it reaches the system (Feedback loop), and how it's verified (Validation channel). Gaps — unowned validation, no correction path for AI output, no eval-data channel — are v1 risks; call them out in Open questions above.*
