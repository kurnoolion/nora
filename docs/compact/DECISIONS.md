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
