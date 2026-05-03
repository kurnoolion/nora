# Decisions

*D-001..D-012 reconstructed 2026-04-21 from `design-inputs/SESSION_SUMMARY.md` and TDD; rationale partial.*

<!--
Template (keep entries tight — this file is always in context):

## D-XXX: Short title
**Status**: Active · **Date**: YYYY-MM-DD
**Decision**: What was chosen.
**Why**: Reason; rejected alternatives inline (vs X: ...).
**Consequences**: What this forces or rules out.
-->

---

## D-001: KG + RAG hybrid over pure vector RAG
**Status**: Active · **Date**: 2026-04-21
**Decision**: Knowledge Graph routes queries (WHERE), targeted vector RAG ranks within scope (WHAT), requirement hierarchy provides structural CONTEXT.
**Why**: Pure vector RAG failed on MNO Q&A — no relationships, undirected scope, lost hierarchy, weak telecom terminology. Graph traversal captures cross-doc/MNO/release links pure RAG can't follow. Vs pure-graph: loses semantic ranking.
**Consequences**: `src/graph/` owns routing, `src/vectorstore/` ranking, `src/query/` orchestrates. Unscoped vector search is a hard-flag.

---

## D-002: Single unified graph + vector store, not MxN partitioned
**Status**: Active · **Date**: 2026-04-21
**Decision**: One graph + one vector store across all MNOs/releases. Logical partitioning via `mno`/`release`/`doc_type` metadata. Standards and Feature nodes shared.
**Why**: Cross-MNO comparison and cross-release diffs become natural traversals; partitioned stores make them merge-with-correctness-risk operations.
**Consequences**: Every node/chunk carries MNO/release/doc_type. Filters enforced at every retrieval path.

---

## D-003: Profile-driven generic structural parser, no per-MNO code
**Status**: Active · **Date**: 2026-04-21
**Decision**: LLM-free `DocumentProfiler` derives a JSON profile (headings, req-ID pattern, zones, cross-refs); `GenericStructuralParser` applies it to emit a `RequirementTree`. New MNO = new profile, no code.
**Why**: Eliminates per-MNO parser drift; keeps LLM out of structural path (determinism, speed); profile is human-reviewable. Vs per-MNO parsers: maintenance grows linearly. Vs LLM parsing: cost/latency/non-determinism.
**Consequences**: Profile quality is critical — wrong profile poisons all that MNO's docs. Validation against held-out docs required. Profiler and parser stay decoupled.

---

## D-004: Option C Hybrid Selective for standards ingestion
**Status**: Active · **Date**: 2026-04-21
**Decision**: Ingest cited 3GPP/GSMA/OMA sections plus parent section, adjacent subsections, and definitions. Aggregate references by `(spec, release)`; download once.
**Why**: Full specs prohibitive in size; section-only loses interpretability; Option C bounds cost while preserving context.
**Consequences**: `src/standards/` resolves spec+release → URL, parses 3GPP DOCX trees. Release-aware: separate `Standard_Section` nodes per release.

---

## D-005: Bottom-up LLM-derived feature taxonomy with mandatory human review
**Status**: Active · **Date**: 2026-04-21
**Decision**: LLM extracts candidate features per doc, consolidator merges them, human review required before graph consumption. Human edits land in `<doc_root>/corrections/taxonomy.json`.
**Why**: Pre-defined taxonomies drift from corpus; bottom-up stays aligned. LLM for extraction only; review prevents hallucinated features.
**Consequences**: Pipeline has a human checkpoint. Corrections workflow (D-011) is hard dep. Unreviewed runs degrade answers.

---

## D-006: `LLMProvider` Protocol (structural typing)
**Status**: Active · **Date**: 2026-04-21
**Decision**: Protocol in `src/llm/base.py` with `complete(prompt, system, temperature, max_tokens) -> str`. Any class with matching `complete()` satisfies it; swap by instance.
**Why**: Multi-LLM support (Claude design-time, Ollama PoC, on-prem proprietary) without caller changes. Vs ABC: no inheritance lock-in.
**Consequences**: No LLM SDK imports outside `src/llm/`. All callers import the Protocol. Protocol signature change is hard-flag.

---

## D-007: `EmbeddingProvider` and `VectorStoreProvider` Protocols
**Status**: Active · **Date**: 2026-04-21
**Decision**: Same pattern as D-006 for embeddings and vector stores. `VectorStoreConfig` (JSON) selects provider/model/metric/chunking. Initial impls: `SentenceTransformerEmbedder`, `ChromaDBStore`.
**Why**: A/B evaluation across embedding models/backends without caller changes; uniformity with D-006.
**Consequences**: `chromadb` and `sentence-transformers` imported only inside vectorstore module. Experimentation is config-driven. Protocol change is hard-flag.

---

## D-008: Web UI = FastAPI + Bootstrap 5 + HTMX
**Status**: Active · **Date**: 2026-04-21
**Decision**: FastAPI + Bootstrap 5 + HTMX + jinja2. Zero npm/JS build. Vendored static assets. Background jobs via `asyncio.create_task()`. SSE log streaming. SQLite job queue via `aiosqlite`. Reverse-proxy compatible via `root_path`.
**Why**: Vs Streamlit (single-user); vs Gradio (ML-demo abstractions); vs Airflow (heavyweight). FastAPI+HTMX = multi-user, async, partial updates, no JS build, Python-native.
**Consequences**: `src/web/` is first-class. CDN fetches are hard-flag. Multi-user auth deferred (production concern).

---

## D-009: Metrics — 5-category SQLite, fire-and-forget middleware
**Status**: Active · **Date**: 2026-04-21
**Decision**: Categories: REQ (endpoint timing), LLM (`OllamaProvider.last_call_stats`), PIP (stage timing), RES (CPU/RAM/GPU via `/proc` + `nvidia-smi`), MET (custom). Persistent SQLite at `web/nora_metrics.db`. `MetricsMiddleware` never blocks responses. `compact_report()` emits MET lines with no proprietary content.
**Why**: Production runs hardware AI partner can't see; observability via compact reports. Vs Prometheus/Grafana: too operational. Vs psutil: dep not always available.
**Consequences**: Every stage emits PIP; every LLM call emits LLM; long stages emit RES. Schema is internal contract. No proprietary content in metric values.

---

## D-010: Multi-format extraction via normalized `DocumentIR`
**Status**: Active · **Date**: 2026-04-21
**Decision**: Per-format extractors emit a common `DocumentIR` (ContentBlock, FontInfo, Position, BlockType). DOC → DOCX via headless LibreOffice. Downstream consumes only `DocumentIR`.
**Why**: One IR isolates format concerns to extraction boundary; downstream contracts stay stable. pymupdf for text+fonts, pdfplumber for tables.
**Consequences**: New format = new extractor only. `DocumentIR` schema is internal contract. Font metadata must be preserved (profiler clusters on font size).

---

## D-011: Corrections override pattern
**Status**: Active · **Date**: 2026-04-21
**Decision**: Auto-generated artifacts → `<doc_root>/output/`. Human overrides → `<doc_root>/corrections/` (same filenames). Pipeline copies `corrections/*.json` over outputs on every run.
**Why**: File-based convention; no DB, no merge logic. Human authority is explicit. Pairs with `/switch-phase` review.
**Consequences**: Every artifact with human-review need uses this. `src/corrections/` owns diff/compactor/FixReport. Web UI writes directly to `corrections/`.

---

## D-012: Chat-mediated collaboration — stable error codes + compact reports
**Status**: Active · **Date**: 2026-04-21
**Decision**: (a) Every pipeline failure emits a stable prefixed code (`EXT-`, `PRF-`, `PRS-`, `RES-`, `TAX-`, `STD-`, `GRA-`, `VEC-`, `EVL-`, …) registered in `src/pipeline/error_codes.py`; logs persist locally. (b) Every cross-boundary artifact has a paired compact format — RPT (pipeline), MET (metrics), FIX (corrections), QC (quality). One record per line, no internal content. (c) QC templates are fixed-field (numbers + Y/N).
**Why**: AI partner can't see proprietary artifacts; compact + stable codes turn that into a tractable surface. No-proprietary-content is a hard invariant.
**Consequences**: Every new artifact ships error-code prefix + compact schema + QC template. `drift-check`/`close-session` hard-flag artifacts missing these. Authority for remote-collaboration NFRs.

---

## D-013: v1 PoC corpus = single-MNO (Verizon Feb 2026); multi-MNO is post-v1
**Status**: Active · **Date**: 2026-04-27
**Decision**: v1 ships against VZW Feb 2026 only. Cross-MNO and release-diff success criteria are post-v1. Schemas (D-002) stay multi-MNO-ready: every node/chunk carries `mno`/`release`.
**Why**: NFR-15 (≥90% weighted overall) must be reachable before adding corpus complexity. Multi-MNO needs proprietary-LLM integration that's also out of v1. Validate KG+RAG architecture on a known dataset first.
**Consequences**: PROJECT.md In/Out scope marks multi-MNO post-v1. NFR-15 measured on VZW only. Cross-MNO/release-diff are *capabilities* but not *v1-verified outcomes*. Adding 2nd MNO triggers KG memory ceiling re-eval.

---

## D-014: Test_Case node/edge types kept in schema, populated post-v1
**Status**: Active · **Date**: 2026-04-27
**Decision**: Schema retains Test_Case nodes/edges. v1 populates zero. FR-7 documents this; FR-26 (Deferred) parks the parser.
**Why**: Schema stability avoids future migration on persisted graph state. Test_Case is known-future, not hypothetical.
**Consequences**: Graph builder paths compile but emit empty. `drift-check` shouldn't flag the slot as unused.

---

## D-015: NFR-15 acceptance = weighted-overall ≥ 90%, not raw req-ID accuracy
**Status**: Active · **Date**: 2026-04-27
**Decision**: NFR-15 binds acceptance to weighted-overall ≥ 90% on user-curated A/B eval set. Five metrics: completeness/accuracy/citation/standards/hallucination-free at 0.30/0.25/0.20/0.15/0.10. Raw req-ID recall is the 25% accuracy slice, not standalone.
**Why**: Weighted is harder to game — high req-ID recall can coexist with poor citations and standards integration.
**Consequences**: Eval reports must show per-metric + weighted overall. Changing weights = hard-flag DECISIONS event. NFR-16 binds dataset to user-curated only — synthetic Q&A doesn't count.

---

## D-016: Production runs behind authenticating reverse proxy; no in-app auth
**Status**: Active · **Date**: 2026-04-27
**Decision**: Auth is a deployment responsibility. System never sees raw login traffic. `root_path` (D-008) accommodates reverse-proxy.
**Why**: In-app auth couples to an IdP, contradicts on-prem-only (NFR-1), bloats v1. Corp envs already run authenticated proxies.
**Consequences**: No password storage, sessions, or IdP integration. `root_path` honored end-to-end (FR-19). Direct FastAPI exposure = deployment misconfig, not v1 bug.

---

## D-017: Domain-expert correction validation = architect FIX-report review (workflow rule)
**Status**: Active · **Date**: 2026-04-27
**Decision**: Architect reviews compact FIX report before each correction-driven re-run. Workflow rule, not code-enforced gate. Pipeline does not block on approval.
**Why**: Code-gated approval needs RBAC/workflow engine — incompatible with v1's no-RBAC stance. FIX reports already strip proprietary content (D-012). Idempotent pipeline (NFR-13) enables revert by editing corrections file.
**Consequences**: Trusted-team assumption explicit. Sloppy review is the v1 failure mode. No rollback infrastructure needed. Contributors table owns this validation channel.

---

## D-018: DOC and XLS parked as Deferred FR-27; TDD design intent preserved
**Status**: Active · **Date**: 2026-04-27
**Decision**: TDD §5.1 multi-format design (PDF/DOC/DOCX/XLS/XLSX) preserved. v1 implements PDF+DOCX+XLSX. DOC and XLS land in Deferred FR-27, revisit "when corpus needs them".
**Why**: Trimming TDD erases known-future capability the abstraction (D-010) was built for. Adding extractor is single integration path.
**Consequences**: drift-check won't flag DOC/XLS as missing. New extractor = new module, no design rev.

---

## D-019: Three-tier code organization — `core/` + `customizations/` + `config/`
**Status**: Active · **Date**: 2026-04-27
**Decision**: `core/` = AI-generated source (`core/src/`, `core/tests/`). `customizations/` = AI-scaffolded code humans complete or own. `config/` = per-module settings.
**Why**: Makes AI/human collaboration boundary explicit in filesystem; lets `drift-check`/`regen-map` apply per-zone rules.
**Consequences**: All MODULE.md paths change. CLI: `python -m core.src.<module>.<module>_cli`. Triggers a reorg session.

---

## D-020: Bi-directional `core ↔ customizations` deps; no AI/human authorship marking in git
**Status**: Active · **Date**: 2026-04-27
**Decision**: `core/` and `customizations/` may import each other freely. Commits don't mark authorship — directory implies it. Manual core edits exceptional, not forbidden.
**Why**: Real dep flow is bidirectional (core's `LLMProvider` consumed by customizations; customizations' profiles consumed by core's parser). Boundary is structural, not authorial.
**Consequences**: drift-check accepts cross-boundary cycles. `regen-map` recognizes both directions. No CI rule for "AI-only commits to core" — review governs.

---

## D-021: One config file per module under `config/`; runtime DBs and per-env data are not config
**Status**: Active · **Date**: 2026-04-27
**Decision**: `config/<module>.json` per module. Runtime SQLite DBs → `<env_dir>/state/`. Per-env user data (corrections, eval Q&A) → env, not `config/`.
**Why**: Centralized + per-module avoids change-magnet single file. Excluding state/per-env keeps `config/` to deploy/install settings, committable to git.
**Consequences**: Modules read only their own config file. New module config = new file under `config/`. `web/config.json` migrated to `config/web.json`.

---

## D-022: Per-env runtime directory `<env_dir>` as single root for all runtime data
**Status**: Active · **Date**: 2026-04-27
**Decision**: `<env_dir>` partitions: `input/<MNO>/<release>/`, `out/{extracted,parsed,resolved,taxonomy,standards,graph,vectorstore}/`, `state/{nora.db, nora_metrics.db}`, `corrections/`, `reports/`, `eval/`. Resolved via `environments/<name>.json` or `--env-dir`.
**Why**: Self-contained pipeline invocations; `rm -rf <env_dir>` is safe; env state can be zipped/shipped. Repo stays artifact-free.
**Consequences**: All file-writing modules take `env_dir` as parameter. `data/` deprecated. Repo-root PDFs move to `<env_dir>/input/VZW/Feb2026/`. Web UI runtime DBs move to `<env_dir>/state/`.

---

## D-023: Source documents under `<env_dir>/input/<MNO>/<release>/`
**Status**: Active · **Date**: 2026-04-27
**Decision**: Path-encoded MNO (upper-case: VZW/ATT/TMO) and release tag (e.g., `Feb2026`). Pipeline reads from path, not filename.
**Why**: Path-encoded metadata survives renames; aligns with multi-MNO post-v1 (new MNO = new directory).
**Consequences**: `infer_metadata_from_path` is authoritative; filename fallbacks deprecated.

---

## D-024: Initial `customizations/` = `profiles/` + proprietary-LLM provider boilerplate
**Status**: Active · **Date**: 2026-04-27
**Decision**: `customizations/profiles/<profile>.json` (was repo-root `profiles/`); `customizations/llm/<provider>.py` (proprietary-LLM scaffold). Co-located tests. New human-touch surfaces move here with their own DECISIONS entry.
**Why**: Profiles are human-curated against real docs; proprietary-LLM provider is sensitive and per-deployment. Two anchors give the convention shape.
**Consequences**: `profile_cli` loads from `customizations/profiles/`. LLM registration looks at both `core/src/llm/` and `customizations/llm/`. Corrections data stays under `<env_dir>/corrections/` (D-022).

---

## D-025: HuggingFace as default 3GPP source; DOCX over MD; FTP retained as fallback
**Status**: Active · **Date**: 2026-04-28
**Decision**: Pluggable `SpecDownloader.source` (`"huggingface"` | `"3gpp"`); HF default. Use HF `original/Rel-{N}/{NN}_series/` DOCX side. Precedence: CLI `--standards-source` > `NORA_STANDARDS_SOURCE` env > `EnvironmentConfig.standards_source` > `"huggingface"`. New `core/src/standards/hf_source.py` uses stdlib `urllib` only.
**Why**: HF DOCX 2.3× faster than FTP (594s vs 1384s for 54 specs), single-domain (proxy-friendly), no LibreOffice DOC→DOCX needed. DOCX over MD: parser already targets DOCX; MD has Rel-20 gaps and loses font/style. Default-flip helps work-laptop case most.
**Consequences**: Both sources land same `data/standards/TS_{spec}/Rel-{N}/` cache; downstream source-agnostic. LibreOffice optional unless source=3gpp. Adds dep on `huggingface.co` for default path; outages → manual `--standards-source 3gpp`.

---

## D-026: OpenAI-compatible LLM provider for cloud APIs
**Status**: Active · **Date**: 2026-04-28
**Decision**: `OpenAICompatibleProvider` in `core/src/llm/openai_provider.py` satisfies `LLMProvider` Protocol. One class for any OpenAI Chat Completions endpoint (OpenRouter, Together, DeepInfra, Groq, vLLM/SGLang, OpenAI itself) via `base_url`/`api_key`/`model` (constructor or `NORA_LLM_*` env vars). Stdlib `urllib` only. Selected via `--llm-provider` / `NORA_LLM_PROVIDER` / `EnvironmentConfig.model_provider`. Protocol surface unchanged.
**Why**: OpenAI Chat Completions is de-facto cloud standard — one class covers ~all providers. Stdlib matches `OllamaProvider`. Cloud needed now to test model-vs-structural accuracy gap independent of DGX Spark availability.
**Consequences**: Cloud path **only for non-proprietary corpora** (OA on dev PC); work-laptop/VoWiFi stays on Ollama. Cost ~$0.30-1 per full run. Silent fallback to MockLLMProvider when env vars missing burned us once — `require_real=True` hardening on Next list. DGX swap when shipped = two-env-var change.

---

## D-027: Parser anchors Requirements from table cells; paragraph wins on duplicate
**Status**: Active (eval regression observed; mitigation pending — see STATUS Flags) · **Date**: 2026-04-28
**Decision**: Add table-cell req-ID pass in `GenericStructuralParser._build_sections`. Per `BlockType.TABLE` row: scan column 1 first, fallback to all cells; one anchor per row. Paragraph wins on duplicate `req_id`. Table-anchored Requirements have `section_number=""` and inherit `parent_section`/`parent_req_id`/`zone_type` from the surrounding paragraph-anchored section; reuses profile's `requirement_id.pattern`.
**Why**: ~46% of internal refs were `broken` because target IDs live only in table cells and never became Requirements. Column-1-first matches the dominant OA layout; all-cells fallback handles row-label tables. Paragraph-wins preserves richer body content.
**Consequences**: MODULE.md invariant relaxed to "Requirement is anchored (paragraph OR table)" — `section_number=""` is valid. Test renamed `test_every_requirement_is_anchored`. Schema unchanged → downstream stages absorb new Requirements without code change. Eval regression on the OA 18-Q set diagnosed as retrieval pollution from thin table chunks; metrics + mitigation paths in STATUS.

---

## D-028: Qwen3-235B-A22B + OpenRouter as best-case baseline; same model targets DGX Spark
**Status**: Active · **Date**: 2026-04-28
**Decision**: Qwen3-235B-A22B via OpenRouter now; via Ollama on DGX Spark when shipped. Same `OpenAICompatibleProvider` (D-026); swap = two env vars, no re-baseline at hardware change. Cloud cleared **only for OA corpus** (public); proprietary corpora stay on local Ollama.
**Why**: MoE (~22B active) suits Spark's memory-rich/bandwidth-modest profile (~273 GB/s LPDDR5X) better than dense 70B. Hybrid thinking + strong IFEval target the instruction-following gap (`gemma4:e4b` summarized instead of emitting JSON). 128K native context. OpenRouter: one API/key for ~100 models; ~10% markup buys optionality at sub-$1 per run.
**Consequences**: Two corpora / two LLM setups codified: OA → OpenRouter cloud; VoWiFi → local Ollama smaller model. Cloud path never carries proprietary content. DGX arrival → hardware-only re-baseline. Baseline numbers in STATUS Done entries.

---

## D-029: LLM and embedding provider/model selectable at runtime; cloud LLM + local embeddings in v1
**Status**: Active · **Date**: 2026-04-29
**Decision**: Symmetric precedence for both: **CLI flag > `NORA_*` env > `EnvironmentConfig` > built-in default**. LLM: `ollama` or `openai-compatible`. Embedding: `sentence-transformers` or `ollama` (new `OllamaEmbedder` alongside existing `SentenceTransformerEmbedder`). Pipeline talks only to `make_embedder(config)`; new backend = new file. `EnvironmentConfig` carries `model_provider`/`model_name`/`embedding_provider`/`embedding_model`.
**Why**: Different deployments have different access (cloud vs air-gapped). Embedding was hard-coded in `VectorStoreConfig()`. Vs config-only: loses ergonomic CLI overrides. Cloud embedding deferred — OpenRouter doesn't host embeddings, separate API seam adds creds/billing surface for marginal v1 benefit.
**Consequences**: `environments/<name>.json` is canonical record of deployed models. `NORA_EMBEDDING_PROVIDER`/`NORA_EMBEDDING_MODEL` are public env contracts. Cloud-embedding remains deferred, not non-goal. Preserves D-006/D-007 (Protocol + injection).
**Related**: D-006, D-007, D-026.

## D-030: Form-factor applicability — per-Requirement attribute with parser-side hierarchical inheritance

**Date**: 2026-05-01
**Status**: Accepted
**Phase**: Architecture

**Context**
FR-32 introduces form-factor applicability (e.g. `["smartphone", "tablet"]`)
as a per-`Requirement` attribute with hierarchical inheritance: explicit
value on the requirement wins; otherwise inherit from parent up the chain;
otherwise fall back to a document-level applicability section if present.

**Decision**

- **Schema**: `Requirement.applicability: list[str]` (parser/structural_parser.py),
  free-form labels per FR-32. Empty list = unknown; downstream stages don't
  filter on empty.
- **Profile** (profiler/profile_schema.py): new `ApplicabilityDetection`
  dataclass on `DocumentProfile` with two fields —
  - `requirement_patterns: list[str]`: regex patterns; first-match wins;
    group 1 contains the comma/pipe-separated form-factor text.
  - `global_section_pattern: str`: regex for the heading text of a
    document-level applicability section; that section's contents seed
    the root default.
  Regex-only by direction; no keyword bag-of-words fallback.
- **Parser pass**: new `_apply_applicability(sections, profile)` post-pass
  after `_link_parents`. Walk sections in document order; resolve global
  root default once; per section try patterns against the section's own
  text, else inherit from parent's already-resolved applicability, else
  fall back to root default.
- **Table-anchored Requirements** inherit through the existing
  `_propagate_hierarchy_to_table_reqs` pass (extended to copy
  `applicability` alongside `hierarchy_path` / `zone_type`).
- **Downstream**: graph + chunk_builder gain one-line `r.get("applicability", [])`
  propagation, mirroring the existing `zone_type` pattern. Metadata-only in v1;
  no retrieval-time `where` filter.

**Why this over alternatives**
- *Side-channel manifest*: rejected. Applicability is intrinsic to each
  requirement; splitting it complicates corrections, graph hydration, audit.
- *Inline detection in `_build_sections`*: rejected. Parent's applicability
  isn't resolved at section-creation time. Post-pass mirrors how
  `zone_type` already flows.
- *Keyword bag-of-words fallback*: dropped per user direction. Trade-off:
  varied phrasings need one regex each; corrections workflow makes that a
  JSON edit, not a code change.
- *Controlled vocabulary*: deferred per FR-32 (free-form labels in v1;
  revisit at second carrier).

**Consequences**
- Additive schema change to `Requirement` and `DocumentProfile` — soft flag
  in parser/MODULE.md and profiler/MODULE.md.
- Profiler does **not** auto-derive these patterns in v1. Humans curate
  `requirement_patterns` per corpus via corrections (D-011, FR-15);
  auto-detection becomes possible once a second corpus reveals patterns
  worth generalizing.
- Future query-side filtering (`where={"applicability": "smartphone"}`)
  is a one-line addition — left as a Deferred capability.

**Related**: FR-32, FR-15, D-007, D-011, parser MODULE.md `zone_type`
propagation pattern.

## D-031: Strikeout-content omission — `FontInfo.strikethrough` IR field, format coverage, parser drop semantics

**Date**: 2026-05-01
**Status**: Accepted
**Phase**: Architecture

**Context**
FR-33 requires the system to detect strikethrough formatting and drop the
affected content (struck-through requirements are document-author deletions
that must not surface to the user or downstream stages). FR-33 covers all
three supported formats: PDF, DOCX, XLSX.

**Decision**

- **IR schema** (models/document.py): `FontInfo.strikethrough: bool = False`.
  Default False keeps existing IR JSONs readable without migration.
  Extractors that can't determine the signal leave it False (never None;
  binary signal keeps the consumer contract simple).
- **Per-format extractor surfacing**:
  - PDF: PyMuPDF `flags` bit 8 (`TEXT_FONT_STRIKEOUT`). Mixed-strike blocks
    use majority-of-characters; 50% defaults to False (no drop on ambiguity).
  - DOCX: `Run.font.strike` / `.dstrike`. Block-level signal is `any` —
    any run struck → whole paragraph struck.
  - XLSX: `Cell.font.strike`. Row drop only when **all** non-empty cells
    in the row are struck; partial strike is treated as in-cell editing.
    Sheet headings (synthesized from sheet titles) cannot be struck.
- **Drop point**: the **parser**, not the extractor. The IR is a faithful
  source representation; interpretation (including the
  `ignore_strikeout` toggle) lives in the parser. This keeps drops
  overrideable via corrections without re-extracting PDFs.
- **Override knob** (profile_schema.py): top-level
  `DocumentProfile.ignore_strikeout: bool = True`. Default ON makes
  FR-33 active out of the box; flip to False (via corrections workflow)
  for corpora that use strikethrough for annotation rather than deletion.
- **Parser behavior**: in `_build_sections`, when both
  `profile.ignore_strikeout` and `block.font_info.strikethrough` are
  True, skip the block (no heading classification, no body append, no
  table emission), increment a counter, log once per parse.
- **Compact-report visibility**: `RequirementTree` gains
  `parse_stats.struck_blocks_dropped: int`; the parse stage's compact
  RPT line gains a `struck=N` token alongside `req=N dep=N docs=N`.
  Per NFR-9.

**Why this over alternatives**
- *Drop at extractor*: rejected. Faithful IR enables corrections-workflow
  override without re-parsing PDFs and keeps IR auditable.
- *Per-span strike state in IR*: rejected. IR block granularity is
  paragraph-shaped; carrying span-level strike would require a much larger
  schema change than this FR justifies. Block-level majority/any/all
  per format is sufficient.
- *Always drop, no toggle*: rejected. Some carriers use strikethrough as
  emphasis. Default ON keeps FR-33 active; the toggle handles edge corpora.
- *Auto-detect "is this corpus strikethrough-as-deletion or
  strikethrough-as-emphasis?"*: deferred. Heuristic isn't reliable
  without labelled data; explicit toggle is more honest.

**Consequences**
- Soft-flag schema additions in models/MODULE.md, profiler/MODULE.md,
  parser/MODULE.md (additive, no breaking change).
- All three extractors gain strike-detection paths.
- Compact RPT format gains `struck=N` (NFR-9 honored).
- Existing IR JSONs and profile JSONs load safely with defaults that
  preserve correctness.

**Related**: FR-33, FR-15 (override path), NFR-9 (compact-format
counterpart), D-007 (profile is human-editable input).

## D-032: Definitions/acronyms expansion — per-document map on RequirementTree, chunk-build-time expansion

**Date**: 2026-05-01
**Status**: Accepted
**Phase**: Architecture

**Context**
FR-35 requires the profiler to detect each document's definitions /
acronyms / glossary section, extract `term → expansion` pairs, and have
the chunk builder expand the first occurrence of each term inline before
embedding. Per FR-35, expansion is per-document, not corpus-wide, to
preserve locality (e.g. `RAT` may mean different things in different MNO
documents).

**Decision**

- **Map location**: on `RequirementTree` (per-document parse output), not
  on the profile. New field `RequirementTree.definitions_map: dict[str, str]`,
  populated by the parser. Profile carries detection rules only; extracted
  values are corpus-content and belong with the parsed tree.
- **Expansion timing**: at chunk-build time, not query-time. Expanded text
  is what gets embedded — vectors carry the signal that retrieval scores
  against.
- **Detection** (profiler):
  - `DocumentProfile.heading_detection.definitions_section_pattern` (regex
    against heading text; default `(?i)acronym|definition|glossary`).
  - `DocumentProfile.definitions_entry_pattern` (regex with two capture
    groups; default supports common dash/colon separators: 16-char term
    cap to avoid prose-line false positives).
- **Extraction** (parser): new post-pass `_extract_definitions` after
  `_link_parents`. The definitions section is kept in the parsed tree.
- **Chunker behavior** (vectorstore/chunk_builder.py): `ChunkBuilder`
  accepts an optional `definitions_map`. First-occurrence-per-chunk
  expansion via `\b<term>\b`. Idempotent. Skips chunks belonging to the
  definitions section itself (avoid double-expansion).
- **Per-document scoping**: vectorstore builder threads each tree's
  `definitions_map` into the chunker per tree; never aggregated across
  trees. Enforced at chunk-build, not at store level (D-002 unified store
  preserved).
- **Corrections workflow**: per-document corrections at
  `<env_dir>/corrections/definitions/<plan_id>.json`. Pipeline merges
  correction values over extracted values for the same term.
- **Compact reports**: parse RPT gains `defs=N`; vectorstore RPT gains
  `expanded=N`. New error-code prefix `DEF-` (`DEF-E001`: definitions
  section detected but entry pattern matched zero entries). Honors NFR-9.

**Why this over alternatives**
- *Map on DocumentProfile*: rejected. Profile is per-corpus rules; map is
  per-document content. Mixing violates the existing profile↔tree seam.
- *Side-channel JSON outside the parsed tree*: rejected. New file, new
  producer, new corrections drop-path — too much surface for one field.
- *Query-time expansion*: rejected. Vectors computed from un-expanded
  text don't improve retrieval recall — defeats the purpose.
- *Corpus-wide map*: rejected per FR-35 (locality is the point).
- *Expansion every occurrence*: rejected. Over-anchors embeddings on the
  same expansion. First-per-chunk is enough signal.
- *Config knob to disable expansion*: rejected. Empty map = no-op; no
  switch needed. Corrections workflow handles edge cases.

**Consequences**
- Soft-flag schema additions in parser/MODULE.md
  (`RequirementTree.definitions_map`), profiler/MODULE.md (two pattern
  fields), vectorstore/MODULE.md (chunker constructor argument).
- New corrections drop-path under `<env_dir>/corrections/definitions/`
  with associated error-code prefix `DEF-` and compact-format counterpart
  per NFR-9.
- Embedding quality on acronym-shaped queries improves at the cost of
  slightly larger chunk text (bounded: one expansion per term per chunk).

**Related**: FR-35, FR-15, NFR-9, D-007, D-002.

## D-033: Heading classification — numbering required, style advisory

**Date**: 2026-05-01
**Status**: Accepted
**Phase**: Architecture (captured retroactively from the development-phase
commit that landed it; commit 3839dcb)

**Context**
Real-corpus review of profile.json against the VZW OA documents surfaced
two structural problems in the previous heading classifier:
- The numbering regex `^((?:\d+\.)+\d*)\s` required at least one dot, so
  top-level chapters in the form `"1 LTE Data Retry"` (no trailing dot)
  were silently rejected — entire subtrees missing from the parsed
  structure.
- `_classify_heading` required both a font/style match AND a numbering
  match; real-world specs apply styling inconsistently, so valid
  headings were dropped when fonts diverged. The profiler responded by
  emitting one detection rule per (font_size, bold, all_caps) cluster
  — three rules all at the same 13.5pt size in the OA corpus, none
  load-bearing.

**Decision**
Numbering pattern is the **necessary** signal for heading classification;
style/font in `profile.heading_detection.levels` is consulted as a hint
only and never gates.

- Relaxed numbering pattern: `^(\d+(?:\.\d+)*)\s+\S` — matches `"2"`,
  `"2.1"`, `"2.1.1.1"` uniformly. Section depth =
  `section_number.count(".") + 1`.
- Section-number extraction in the parser uses an internal canonical
  regex (`_SECTION_NUM_RE`), not the profile's, so older profiles with
  different capture-group shapes keep working.
- False-positive guards in `_classify_heading`: text length capped at 200
  chars; terminal-punctuation rejection (`. ! ?`) for blocks longer
  than 80 chars. Rejects numbered list items in body text
  (`"1. The system shall ..."`).
- **Section-number deduplication** in `_build_sections`: the first
  heading with a given section number wins; subsequent matches are
  demoted to body text appended to the current section.
- Profiler: when numbering depth >= 2, emit `method="numbering"` and a
  single advisory level rule capturing the dominant heading style
  (kept for human curation, ignored by the parser). When numbering is
  absent or shallow, fall back to legacy `method="font_size_clustering"`.
- Default `HeadingDetection.method` changed from `"font_size_clustering"`
  to `"numbering"`.

**Why this over alternatives**
- *Style-as-gate (previous behavior)*: rejected. Real specs apply
  styling inconsistently; fonts get lost in extraction; valid headings
  drop. The bug we fixed.
- *Hybrid (require style OR numbering)*: rejected. Without firm length /
  punctuation guards, numbered list items in body flip to headings.
- *Stricter regex (require trailing dot)*: rejected per OA evidence —
  top-level chapters use `"N Title"` form without a dot. A stricter
  regex misses an entire hierarchy level.
- *Per-line section-number-from-profile capture-group shape*: rejected.
  Profile patterns vary in capture-group structure (legacy
  `^((?:\d+\.)+\d*)\s` vs new `^(\d+(?:\.\d+)*)\s+\S`); decoupling the
  gate from the section-number extractor avoids breaking older profiles.

**Consequences**
- Public-surface contract shift on parser's `_classify_heading` — gate
  semantics changed. Soft flag in parser/MODULE.md (additive Invariant
  noting numbering-as-gate).
- Section-number uniqueness is now a hard invariant — first heading wins.
- Profile schema: `HeadingDetection.method` default changed; old JSONs
  with `"font_size_clustering"` keep loading and working (parser still
  gates on numbering; the old levels become inert advisory).
- Surfaced 5 new ingestion FRs (FR-31..FR-35) as a side effect of the
  corpus review — those are addressed under D-030, D-031, D-032 plus
  FR-31 / FR-34 batches without their own decisions.
- 7 new heading-classification tests + 1 profiler integration test pin
  the new behavior.

**Related**: FR-3, D-003, D-030, D-031, D-032.

## D-034: Parser hardening — corpus-correctness rules from real-PDF review

**Date**: 2026-05-01
**Status**: Accepted
**Phase**: Development (architectural rules captured retroactively from
the parser-hardening session against the 5 Verizon OA PDFs)

**Context**
User-driven review of `~/work/env_vzw/out/profile/profile.json` and the
parsed trees against the source PDFs surfaced multiple corpus-correctness
bugs in `_build_sections` and adjacent code paths. Each bug had a
specific root cause and a specific fix; consolidated here because they
all shape the parser's contract for handling real-world PDF extraction
artifacts.

**Decision** — five layered rules:

1. **Req-id placement is a corpus property, default `trailing`** (currently
   hardcoded in parser, TODO to move to profile-stage detection). OA
   places small-font req_id blocks AFTER the heading they belong to
   (trailing markers); the parser's pre-fix `pending_req_id` behavior
   (leading markers, where extras lateral to the next heading) produced
   a systematic off-by-one cascade. Now: when a req_id block is
   encountered and `current_section.req_id` is already set, the new id
   is IGNORED with a debug log (first-id-wins) — never lateralled.
   `pending_req_id` only fires when no section has been opened yet.

2. **Table-anchored extraction is deferred to a second pass**. Tables
   are collected during the main walk; table-anchored req extraction
   runs after `paragraph_req_ids` and `struck_req_ids` are fully
   populated. Paragraph anchors and struck ids take precedence over
   table-cell ids regardless of source order. Eliminates the duplicate-
   when-table-precedes-anchor pattern (a req_id whose paragraph anchor
   is on page 34 but who appears in a cross-reference table on page 3
   was getting both nodes).

3. **Heading-continuation defense**. PyMuPDF wraps long headings across
   multiple text blocks; when the continuation line happens to start
   with `<digits><space><uppercase>`, the relaxed numbering gate
   misclassifies it as a phantom depth-1 chapter. Fingerprint (all
   three required): depth-1 section_number + a deeper section already
   seen + previous block was heading-shaped (no body text or req_id
   between). When the fingerprint matches, the new "section" is
   appended to the current section's title as continuation rather than
   creating a phantom chapter.

4. **Req-id whitespace canonicalization**. PDF text extraction
   occasionally fuses bold runs and drops the underscore in
   `VZ_REQ_PLAN_NUM` → arrives as `VZ_REQ_PLAN NUM`. Profile patterns
   accept either separator (`[_\s]\d+`); parser canonicalizes every
   matched id (whitespace → underscore) before storage and comparison
   so the same requirement is never tracked under two identifiers.
   `_canonicalize_req_id` helper + `_find_req_ids` wrap site.

5. **`DocumentProfile.enable_table_anchored_extraction: bool = True`**.
   Default preserves D-027 behavior (back-compat for MNOs that
   genuinely use table-defined reqs). Set to False via the corrections
   workflow for paragraph-only-requirement corpora (Verizon OA): table
   extraction becomes a no-op, eliminating cross-reference / changelog
   table phantoms in one move.

**Why this over alternatives**
- *Per-MNO hardcoded behavior in parser* — rejected. Violates D-003;
  the profile is the single source of corpus-specific rules.
- *Single-pass table extraction with retroactive dedup* — rejected for
  rule 2; the deferred two-pass approach is simpler and order-
  independent. Forward-only single-pass needed an extra reconciliation
  pass anyway.
- *Tighter heading-continuation heuristic* (e.g. require the depth-1
  number to be unrelated to previous) — rejected; the three-part
  fingerprint is precise enough in practice (4/4 confirmed false
  positives caught, no real chapter transitions broken in the OA
  corpus where multi-chapter docs don't exist).
- *Strict req-id pattern (no whitespace)* — rejected; PDF extraction
  artifacts are real and silent ID drops are worse than slightly more
  permissive matching with canonicalization.
- *`enable_table_anchored_extraction` defaults to False (drop D-027 by
  default)* — rejected; D-027 is a real architectural decision about
  multi-MNO support. Defaulting True keeps it on; corpora that don't
  use table-anchored reqs flip the flag via corrections.

**Consequences**
- Parser semantics shift: req-id assignment is now first-id-wins and
  trailing-only (without profile-stage detection of leading-marker
  corpora, the current implementation may misbehave there — see
  Flag 2026-05-01).
- All req_ids stored under canonical underscore form regardless of
  PDF extraction artifacts; consumers comparing ids never need to
  whitespace-normalize.
- Profile schema gains: `requirement_id.placement` (TODO),
  `enable_table_anchored_extraction`. Old profile JSONs load with
  default values intact.
- Empirical corpus result: OA req count 985 (broken A3 baseline) →
  1015 (with `enable_table_anchored_extraction=False`) or 1048
  (default). Phantom duplicates eliminated; off-by-one cascade gone;
  17 ground-truth pairs verified; parse_audit confidence 96%/3%/0.1%.

**Related**: FR-3, D-003 (no per-MNO code), D-027 (table-anchored
extraction architecture), D-031 (strikeout drop), D-033 (numbering-
driven heading classification).


## D-035: Profile-driven revision-history table omission (FR-34)

**Date**: 2026-05-02
**Status**: Accepted
**Phase**: Development

**Context**
OA documents have a revision/change-history table near the top of section 1.
Different MNOs use different headings ("Revision History", "Change History",
"Document Log", etc.) so detection cannot be hardcoded. Tables sometimes
span multiple pages — pdfplumber emits each page's slice as its own table
block.

**Decision**
- New `DocumentProfile.revision_history_heading_pattern: str` field. Default:
  `(?i)^\s*(revision|change|version|document)\s+(history|log)\s*$` — broad
  enough to catch common labels without per-MNO config.
- Profiler narrows the regex during scan to the most-frequent observed
  phrasing (whitespace-tolerant via `re.escape().replace(r"\ ", r"\s+")`),
  gated on the next non-image block being a table.
- Parser drops the matching paragraph, then consumes subsequent table/image
  blocks until the next paragraph (which is by construction the next
  section's heading). New `ParseStats.revhist_blocks_dropped` counter.

**Why this over alternatives**
- *Hardcoded keyword list in parser* — rejected. Violates D-003.
- *Per-corpus profile override only (no broad default)* — rejected. New corpora
  would silently retain revhist tables until someone curates an override.
- *Window-bounded next-block consume (3 blocks)* — initial design; replaced
  when corpus probe revealed revhist tables span multiple pages. Now
  consumes until next paragraph, unbounded by block count.

**Consequences**
- Profile schema gains `revision_history_heading_pattern`. Old profile JSONs
  load with the default intact.
- Parser drops any paragraph matching the pattern PLUS all subsequent
  non-paragraph blocks until the next paragraph.
- env_vzw empirical: `revhist=73` (5 heading paragraphs + 68 continuation
  blocks across 5 docs). Previously 10 with single-table window.

**Related**: FR-34, D-003, D-031.


## D-036: PDF table strike detection — row-edge filter + per-row cell strike

**Date**: 2026-05-02
**Status**: Accepted
**Phase**: Development

**Context**
FR-33 [D-031] geometric strike detection produced 93% false positives on
the OA corpus (709 of 762 tables flagged struck). Probe revealed pdfplumber
draws each row boundary as a horizontal line of full-cell width —
geometrically indistinguishable from a strike-through to `_table_is_struck`.
The `min_lines=2` threshold was protecting nobody: any 3+ row table with
grid lines trivially crossed it.

User feedback also clarified that real strike-throughs in OA tables are
*per-row*: short strike segments cover individual word/text spans inside
specific cells, never spanning the full table width. Whole-table strikes
exist but are rare.

**Decision** — two complementary filters:

1. **Row-edge filter on `_table_is_struck`**. Strike candidates whose y aligns
   with any `Table.rows[*].bbox` edge (within `edge_tol=1.5pt`) are excluded
   from the threshold count. The tolerance handles paired top-of-row-i /
   bottom-of-row-(i-1) draws that some PDF generators emit at adjacent ys.
   Real strike-throughs draw at the *middle* of a text row, well away from
   row boundaries; they survive the filter.

2. **Per-row cell strike via `_detect_struck_rows`**. Walks `table_obj.rows`
   and flags rows whose interior (`y_top + 1.5 < y < y_bot - 1.5`) contains
   ≥1 horizontal strike line. Header row (index 0) is never marked struck
   (OA tables retain their header even when all data rows are deleted).
   Struck rows are dropped from the IR's `rows` list at extraction time;
   if all data rows drop, the whole table is marked `strikethrough=True`
   so the parser drops it via the existing FR-33 path.

**Why this over alternatives**
- *Raise `min_lines` threshold* — rejected. To rule out 3-row grid lines we'd
  need ≥4, which would miss small 2-row genuinely struck tables.
- *Abandon geometric detection on tables* — rejected. Real cell strikes
  (LTEAT p38 KEYPAD CONTROL row) need to be caught somehow.
- *Per-row strike with table-bbox-width gate* — rejected. Real cell strikes
  cover only the cell's text width, not the table's. Counting in row
  interior without horizontal coverage thresholds matches the actual
  geometry pattern.
- *Add per-row strike metadata to IR (preserve rows, mark struck)* — rejected
  for now. Dropping at extraction simplifies the parser-side contract; if a
  future workflow needs to override per-row strikes, the corrections file
  is the seam.

**Consequences**
- `_table_is_struck` signature gains `row_edge_ys: list[float] | None`,
  `edge_tol: float = 1.5`. Caller passes edges from pdfplumber `Table.rows`.
  None/empty preserves legacy behavior (back-compat).
- New `_detect_struck_rows(table_obj, strike_lines, edge_tol=1.5)` returns
  data-row indices to drop. Header excluded.
- Extract-time row drops are silent (no per-row strike marker in IR);
  callers can't recover them.
- env_vzw empirical: tables flagged struck 709/762 → 0/762; row-level drops
  emptied 280 tables (whole-table strikethrough propagated to parser);
  parse_stats.struck_blocks_dropped 1088 → 659 (paragraphs + emptied
  tables).

**Related**: FR-33, D-031.


## D-037: Section-heading cascade for struck headings

**Date**: 2026-05-02
**Status**: Accepted
**Phase**: Development

**Context**
User feedback identified two pages where a struck section heading should
cause its descendants to be dropped even when the descendants themselves
are not individually marked struck:

- LTEB13NAC p310: parent heading `_6289` "LTE Test Application for Antenna
  Testing Requirements" is struck; the (non-struck) TIS table directly
  below it should be dropped as part of the deleted section.
- LTEB13NAC p68: heading `1.3.1.2.7.15 RSSI ...` is struck; the section
  body and any sub-sections under it are also gone in source.

The pre-cascade parser dropped the struck heading paragraph but left
descendant blocks orphaned, attaching them to the previous (live) section
or producing phantom Requirements.

**Decision**
Parser maintains a single `cascade_depth: int | None` state across the
block walk. When a struck paragraph is also a section heading (depth
detected via `_classify_heading` so the cascade boundary uses EXACTLY the
same definition of "heading" as the rest of the parser), `cascade_depth`
is armed to that heading's depth. Subsequent blocks are dropped until a
new heading appears at depth ≤ `cascade_depth` (a sibling or shallower
section), at which point cascade ends and that block is processed normally.
Tables, images, and body paragraphs all get dropped. Deeper-nested struck
headings inside an already-cascading section don't tighten the boundary
(only shallower struck headings do — protects against late corrections
narrowing scope). New `ParseStats.cascade_blocks_dropped` counter.

**Why this over alternatives**
- *Drop only the heading paragraph* — rejected. Leaves orphan tables/sub-
  content under the previous live section.
- *Cascade until next paragraph (no depth check)* — rejected. Would terminate
  cascade on the first body sentence, missing sub-headings and tables that
  belong to the deleted section.
- *Cascade indefinitely (drop everything after a struck heading)* — rejected.
  A depth-5 struck heading would erase its depth-2 siblings.
- *Use the profile's `numbering_pattern` directly for boundary detection* —
  rejected. The pattern's capture-group shape varies per profile;
  delegating to `_classify_heading` reuses the parser's own length-cap and
  punctuation guards.

**Consequences**
- New parser invariant: a struck section heading deletes the entire section
  subtree (down to depth ≤ cascade_depth boundary).
- `ParseStats.cascade_blocks_dropped` reports drops; env_vzw empirical: 301
  blocks dropped across 5 docs.
- Cascade test depends on `_classify_heading`'s definition of heading,
  which means if heading classification changes, cascade boundaries shift
  in lockstep — desirable.

**Related**: FR-33, D-031, D-033.


## D-038: Table-anchored definitions extraction (extends D-032)

**Date**: 2026-05-02
**Status**: Accepted
**Phase**: Development

**Context**
D-032 specified that `_extract_definitions` scans the matched glossary
section's body text line-by-line via `definitions_entry_pattern`. On the
OA corpus, this returned `defs=0` despite all 5 docs having a glossary
section: the section body is a thin intro paragraph ("This section defines
acronyms used throughout the document.") and the actual term/expansion
pairs live in 2-column tables (`Acronym/Term | Definition`,
`Term [Abbreviation] | Definition`, etc.). Body-text scan never sees them.

**Decision**
`_extract_definitions` scans BOTH layouts — body-text via the existing
pattern (preserved unchanged), AND tables. For each row of length ≥ 2 in
the matched section's tables, col[0] is the term and col[1] is the
expansion; whitespace (including embedded newlines from PDF wrap) is
collapsed. First-occurrence-wins precedence applies across both layouts:
body-text scans first, then tables in document order. No profile flag
gates the table path — any 2+ col table inside a glossary section is
treated as a glossary table by convention.

**Why this over alternatives**
- *New profile flag `definitions_layout: str = "body" | "table" | "auto"`*
  — rejected. The two layouts don't conflict (a doc with both gets both),
  and "auto" is what humans want by default. Adding a flag for a no-cost
  combined behavior is over-engineering.
- *Detect column header ("Acronym/Term", "Term", etc.) before treating
  rows as defs* — rejected. Different MNOs use different column headers;
  the structural position (col[0], col[1]) is the reliable signal.
- *Cap term length to filter prose-shaped first-cells* — rejected for
  table layout. The structural gate (must be inside a glossary section's
  table) is strong enough.

**Consequences**
- `_extract_definitions` yields entries from EITHER body text OR tables OR
  both — callers don't need to know which.
- env_vzw empirical: defs=0 → 158. LTEAT 26, LTEB13NAC 63,
  LTEDATARETRY 36, LTEOTADM 18, LTESMS 15.
- Minor extraction artifact: PDF wrap can split "3rd" across lines as
  "rd\n3" → expansion text reads "rd 3 Generation Partnership Project..."
  Term key (`3GPP`) is correct; expansion remains readable. Acceptable
  for v1.

**Related**: FR-35, D-032.


## D-039: Entity-priority graph scoping

**Date**: 2026-05-02
**Status**: Accepted
**Phase**: Development

**Context**
A4 evaluation's `traceability` category scored 16.7% accuracy. trace_01
("What is requirement VZ_REQ_LTEDATARETRY_7754?") returned 10 *other* req
chunks — not _7754 — despite _7754 existing in the graph and vector store.
Trace: the analyzer correctly extracted _7754 as an entity; the graph
scoper's `_entity_lookup` correctly found the node; but `_feature_lookup`
THEN expanded via the DATA_RETRY feature (~700 mapped reqs) into a
794-candidate seed. ChromaDB's `where: req_id IN [794 ids]` filter then
ranks by vector similarity — _7754 didn't make top-10 because the literal
query text isn't semantically close to its chunk content.

**Decision**
When `_entity_lookup` yields any matches, treat those as authoritative for
the scope:
- Skip `_feature_lookup`, `_plan_lookup`, and `_title_search` expansion.
- Step-5 edge traversal (depth=2 from entity seeds) still runs, providing
  the entity's immediate neighborhood — sibling sections, referenced
  standards, parent containers — without flooding the candidate set with
  feature-wide reqs.

For queries WITHOUT specific entity matches (the analyzer extracted
nothing or only false-positive concepts), the existing flow (feature →
plan → title-search → traversal) is unchanged.

**Why this over alternatives**
- *Always merge entity + feature seeds (status quo)* — rejected. Diluted
  the 1-req entity match into a 794-candidate scope where vector ranking
  couldn't surface the named req.
- *Boost entity-match chunks at retrieval time (rerank)* — rejected as
  the primary fix. Reranking is one more knob; the upstream fix (don't
  add the 793 unrelated reqs to the scope) is cleaner. Reranking can be
  added later orthogonally.
- *Bypass vector retrieval entirely when entity matches exist (direct chunk
  lookup by req_id)* — rejected. Loses the neighborhood-context retrieval
  that makes "what is X and how does it relate" queries work.

**Consequences**
- Specific-id queries ("What is VZ_REQ_X?") get tight scope rooted at the
  named req, with depth-2 neighborhood for context.
- Concept queries (no entity match) behave as before.
- env_vzw empirical: trace_01 acc 0% → 100%; traceability 16.7% → 50%
  (then 66.7% after ground-truth refresh).

**Related**: FR-22 (graph-scoped retrieval), D-002 (unified store with
metadata filters).


## D-040: Type-aware retrieval `top_k` + cross-doc list-style detection

**Date**: 2026-05-02
**Status**: Accepted
**Phase**: Development

**Context**
A4 evaluation's `cross_doc` category scored 0% accuracy on all 4 questions
("What are all the SMS over IMS requirements?" / "What are the PDN
connectivity requirements across all VZW plans?" etc.). Investigation
revealed two compounding issues:

1. **Misclassification.** `_classify_query_type` only flagged `CROSS_DOC`
   when ≥2 plan aliases appeared in the query — a high bar that misses
   concept-shaped breadth questions. cross_01 was classified `SINGLE_DOC`.

2. **Insufficient `top_k`.** List/breadth questions expect parent or
   overview reqs (e.g., `VZ_REQ_LTESMS_30258` "SMS OVER IMS - OVERVIEW",
   chunk len ~280 chars: heading + path only) whose chunks are
   intentionally short. Vector similarity ranks them below richer leaf
   chunks (663 chars of body). With `top_k=10`, expected reqs land at
   rank #15+. Probed distances on cross_01:
   ```
   top-10 range:    0.314–0.372
   expected reqs:   0.383, 0.386, 0.577 (just outside)
   ```

**Decision** — two coupled fixes:

1. **Analyzer adds list/breadth phrase triggers**. `_classify_query_type`
   gains an explicit pre-multi-plan-alias check on phrases:
   `across all`, `across the`, `in all`, `across vzw|mnos|plans|specs`,
   `all the requirements`, `all reqs`, `what are all`, `what requirements`.
   These map to `CROSS_DOC`. FEATURE_LEVEL still wins on more-specific
   phrasing ("everything about", "related to") so the existing
   classification contract holds (FEATURE_LEVEL > CROSS_DOC ordering).

2. **`QueryPipeline` picks `top_k` from `_TYPE_TOP_K`** based on
   `intent.query_type`:
   - CROSS_DOC / FEATURE_LEVEL / STANDARDS_COMPARISON /
     CROSS_MNO_COMPARISON → 25
   - TRACEABILITY / RELEASE_DIFF → 20
   - SINGLE_DOC / GENERAL → fall through to constructor `self._top_k`
     (default 10)

   Pipeline takes `max(self._top_k, type_top_k)` so callers can still
   raise the floor explicitly.

**Why this over alternatives**
- *Uniform top_k=20 for all queries* — rejected. Wastes context on lookup-
  style queries that are already well-served by 10. Per-type tuning costs
  almost nothing and isolates the regression risk.
- *Analyzer-driven top_k (set in QueryIntent)* — considered but more
  surface area than needed. The pipeline knows the intent; encoding
  top_k in the QueryIntent dataclass would couple the schema to retrieval
  config. Keeping it in the pipeline keeps the analyzer's job pure.
- *Boost short/parent chunks at rerank time* — rejected as the primary
  fix. Same logic as D-039: upstream fix (more headroom) is simpler than
  reranking. Rerank stays orthogonal.
- *Raise CROSS_DOC bar to ≥1 plan alias instead of ≥2* — rejected. False
  positives multiply (any mention of "LTE" or "SMS" would trigger). The
  list/breadth phrase set is more precise.

**Consequences**
- New `_TYPE_TOP_K` constant in `query/pipeline.py`. Map values are
  tuneable knobs, not architectural commitments.
- Cross-doc queries cost more LLM tokens (more chunks → larger context).
  Manageable: qwen3-235b-a22b has 128k context; 25 chunks × ~500 chars
  is well under.
- env_vzw empirical: cross_doc 0% → 37.5%; overall avg_accuracy
  56.5% → 64.8%; no regression in single_doc (still 79%).

**Related**: FR-22, D-039 (entity-priority scoping — companion fix for
the lookup side).


## D-041: BM25 hybrid retrieval — sparse index, RRF fusion, per-type weights

**Date**: 2026-05-02
**Status**: Accepted
**Phase**: Development

**Context**
A4 evaluation showed pure-dense retrieval missed concept queries with
specific high-IDF terms. `standards_comparison` was at 50% accuracy
because queries like "How does VZW T3402 differ from 3GPP TS 24.301?"
have rare terms (`T3402`, `24.301`) that pure-dense embeddings spread
thin across many topically-related chunks. The expected reqs existed
in the graph and vector store but ranked outside top-k.

A purely-dense system has no good way to surface these. BM25 over
chunk text is the textbook complement — it weights rare exact terms
by IDF, so `T3402` (occurring in 1-2 of 794 chunks) ranks the
matching chunks above topically-broader matches.

**Decision** — five layered choices:

1. **Add `rank_bm25.BM25Okapi` as the sparse retriever**. Pure-Python,
   no compilation, ~10KB wheel. Sufficient for corpus sizes through
   the v1 multi-MNO scope (~10k chunks max).

2. **Build the index in-memory at `QueryPipeline` init time** from a
   one-shot `store.get_all()` snapshot. ~50-100ms for 794 chunks; fits
   the cost of a CLI/process startup. Persistence would add a build
   step + a stale-index-vs-store contract; not worth the complexity
   at this corpus size.

3. **Custom telecom-aware tokenizer**. Standard tokenizers split on
   underscores/dots/hyphens, breaking the corpus's most discriminating
   tokens (`vz_req_lteat_45`, `24.301`, `rel-9`) into uninformative
   sub-tokens. Pattern: `[a-z0-9_.\-]+` lowercase, drop len-1 tokens.
   No stemming (telecom acronyms don't stem), no stopword filtering
   (BM25's IDF already penalizes common words).

4. **Reciprocal Rank Fusion (RRF) with per-list weights**, k=60
   (Cormack 2009). Chosen over score normalization because BM25 raw
   scores and dense distances aren't on comparable scales; rank-based
   fusion sidesteps the issue. Weighted variant
   `score(d) = Σ w_i / (k + rank_i(d))` lets dense dominate when the
   query type doesn't benefit from BM25.

5. **Per-query-type BM25 weights**, configured in
   `pipeline._TYPE_BM25_WEIGHT`:
   - `STANDARDS_COMPARISON` / `TRACEABILITY` / `SINGLE_DOC` → 0.5
   - `CROSS_DOC` / `FEATURE_LEVEL` (and unmapped types) → 0.0 (pure
     dense)
   Empirically: BM25 hurts cross-doc / breadth queries because the
   expected hits are thin parent/overview chunks that BM25 ranks low;
   richer leaf chunks dominate the fusion. CROSS_DOC went 37.5%→8.3%
   when BM25 fired uniformly at 0.5; per-type policy preserves the
   gain on standards / traceability without the regression elsewhere.

**Why this over alternatives**
- *Always-on uniform BM25 weight* — rejected. Regressed cross-doc
  by 29pp; the parent/overview-chunk problem is real and corpus-
  shape-dependent.
- *Persisted BM25 index alongside ChromaDB* — rejected for v1.
  Build cost is negligible at corpus scale; persistence adds a
  stale-vs-store contract to maintain.
- *Off-the-shelf tokenizer (NLTK / sklearn)* — rejected. Splitting
  `vz_req_lteat_45` into `vz / req / lteat / 45` discards the most
  important token signal in the corpus.
- *Score normalization + linear combination* — rejected over RRF.
  BM25 scores are corpus-size-dependent and dense distances are
  metric-specific; normalizing them to a comparable scale is
  brittle. RRF is rank-based and parameter-free aside from `k`.
- *Cross-encoder reranker on dense top-k* — deferred (see Next).
  Higher leverage but heavier lift (model selection, latency
  budget, training data); BM25 was the cheap win to capture first.

**Consequences**
- `VectorStoreProvider` protocol gains `get_all() -> QueryResult`
  with empty `distances`. Existing implementers need to add it;
  back-compat preserved by the optional `from_store(store)`
  fallback in `BM25Index` returning None when the method is absent.
- `RAGRetriever` constructor accepts optional `bm25_index`; the
  `retrieve()` signature gains optional `bm25_weight: float | None`
  for per-call override. None / 0.0 / `bm25_index=None` all
  short-circuit to pure-dense (back-compat, perf when not needed).
- BM25 filter must use `metadata.req_id` (not `chunk_id`) to gate
  candidates — chunk_ids are `req:<req_id>` while the dense path
  filters on the metadata field. Mismatched filter spaces produce
  empty BM25 results; learned the hard way during integration.
- `_TYPE_BM25_WEIGHT` is empirical tuning, not architectural
  contract. Numbers will shift as the eval set grows; treat as
  hyperparameter, not invariant.
- Empirical impact on env_vzw: standards_comparison 50%→83.3%
  (+33pp); traceability +16.7pp via the companion `mention`
  classifier route; overall accuracy 67.6%→73.1% (+5.5pp from
  BM25 alone).

**Related**: D-001 (KG-scoped RAG), D-002 (unified vector store),
D-039 (entity-priority graph scoping — companion fix for the
specific-id lookup path), D-040 (per-type top_k + cross-doc
detection — the existing companion that BM25 was layered onto).


## D-042: Parent-chunk subsection augmentation — opt-in, default off

**Date**: 2026-05-02
**Status**: Accepted
**Phase**: Development

**Context**
After BM25 hybrid retrieval landed, A4 still had concept queries
where the right req existed in the corpus but didn't rank in
top-k. Hypothesis: parent/overview chunks (`SMS over IMS - OVERVIEW`,
`DETACH REQUEST`, etc.) lose to richer leaf chunks because their
own bodies are heading-only — a query about "SMS over IMS
requirements" has a rich-body chunk about MO-SMS-procedure outranking
the literal "SMS over IMS - OVERVIEW" parent.

Mooted fix: augment parent chunks with their immediate children's
titles so the parent's chunk text gains breadth-relevant tokens
without changing the leaf chunks. Tested empirically.

**Decision**
Implement the augmentation as a chunk-builder feature, ship default
**off**, expose three config knobs for opt-in tuning:

- `VectorStoreConfig.include_children_titles: bool = False`
- `VectorStoreConfig.children_titles_body_threshold: int = 300` —
  body-thinness gate (only parents with `len(body) < threshold` get
  augmented)
- `VectorStoreConfig.max_children_titles: int = 3` — cap on the
  emitted list with `(+N more)` overflow marker

When enabled, `_build_chunk_text` appends a single line:
`[Subsections: child1; child2; (+N more)]` after the body / tables /
images.

**Why default off** — empirical tuning on env_vzw (BM25 hybrid +
per-type top_k baseline = 88.9% / 80.1%):

  Augmentation on, cap=12: 88.6% / 78.8% (single_doc +8pp,
    cross_doc -14pp — net -1.3pp accuracy)
  Augmentation on, cap=3:  88.8% / 79.7% (single_doc +8pp,
    cross_doc -10pp — net -0.4pp accuracy)
  Augmentation off:        88.9% / 80.1% (baseline)

Body-thinness gate doesn't selectively help on OA: 89% of parents
have body<50 chars (heading-only), 94% are <300. The gate's
selectivity is corpus-shape-dependent.

The single_doc gain is real (parents become findable for "find
this section" queries) but the cross_doc loss is structurally the
same effect from the other side — augmented parents displace their
own children from top-k, and breadth queries explicitly want the
children. The two effects offset.

**Why this over alternatives**
- *Augment unconditionally (default on)* — rejected. -1.3pp
  accuracy regression on the eval the user just curated.
- *Per-query-type augmentation in retrieval (use augmented chunks
  for SINGLE_DOC, plain chunks for CROSS_DOC)* — rejected for v1.
  Would require dual-indexing (two embedding sets per chunk) or
  retrieval-time text manipulation; both add surface area for a
  feature that's a wash.
- *Augment but don't include in the embedded text — only in the
  chunk metadata* — rejected. Metadata isn't searched by the
  embedder or BM25; the augmentation has no effect.
- *Drop the feature entirely* — rejected. Implementation cost is
  paid; the principled hypothesis is sound; corpora with rich-
  bodied parents (less heading-only) should benefit; lookup-heavy
  question mixes should benefit. Keep it behind a flag for those
  cases; document the tradeoff so future evaluators don't flip it
  blind.

**Consequences**
- New config surface stays back-compat (default-off preserves
  prior chunk text exactly).
- `ChunkBuilder._build_chunk_text` signature gained an optional
  `id_to_title: dict[str, str] | None` param; `_build_tree_chunks`
  builds the lookup once per tree.
- 8 new tests pin the contract: emit-when-enabled, suppress-when-
  disabled, cap behavior, overflow marker, unresolved-child-id
  defense, body-thinness gate fires correctly on both sides.
- Future eval re-runs on TMO / AT&T corpora are the right
  trigger to re-evaluate the default. If their parent sections
  carry substantive body content (less heading-only than OA), the
  augmentation tradeoff likely flips positive and the default
  could be flipped to on.

**Related**: FR-3 (profile-driven generic parser produces the
hierarchy this consumes), D-027 (table-anchored Requirements that
make some "parents" thin), D-040 (per-type top_k — interacts with
augmentation because wider top_k gives more room for both parents
and children to coexist).
