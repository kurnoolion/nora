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

## D-013: v1 PoC corpus = single-MNO (MNO-A Feb 2026); multi-MNO is post-v1
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

## D-029: LLM and embedding provider/model selectable at runtime; remote LLM + local embeddings in v1
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
**Status**: Accepted (extended by [D-060](#d-060-unified-strike-model--partial-text-strike-via-runs-mark-dont-drop-at-extract-time))
**Phase**: Architecture
**Note (2026-05-09)**: D-060 extends this ADR. Geometric strike-line detection (`_table_is_struck`, `_detect_struck_rows`, `_span_struck`) and the `FontInfo.strikethrough` field stay. What changed: PDF/XLSX extractors no longer **drop** struck rows from the IR — they mark via `row_runs`, parser drops at parse time. DOCX gains partial-text strike via runs; the "any-run-struck" rule for paragraph-level `font_info.strikethrough` becomes "every-run-struck" (partial strike now keeps the block and uses runs to drop spans). See D-060 for the full unified-model rationale.

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
commit that landed it; commit 9df8a19)

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
- Future eval re-runs on TMO / MNO-B corpora are the right
  trigger to re-evaluate the default. If their parent sections
  carry substantive body content (less heading-only than OA), the
  augmentation tradeoff likely flips positive and the default
  could be flipped to on.

**Related**: FR-3 (profile-driven generic parser produces the
hierarchy this consumes), D-027 (table-anchored Requirements that
make some "parents" thin), D-040 (per-type top_k — interacts with
augmentation because wider top_k gives more room for both parents
and children to coexist).


## D-043: Acronym lookup chain — parser fix + glossary chunks + query-side pin

**Date**: 2026-05-03
**Status**: Accepted
**Phase**: Development

**Context**
A user-submitted question on the Test page — *"What is SDM?"* —
returned a hallucinated definition ("SIMOTA Device Management
server"). The corpus glossary in fact defines SDM as "Subscriber
Device Management — APN Management and Device profiling" in the
LTEOTADM document. Three independent failures stacked:

1. The parser's `definitions_map` was missing SDM (18 of 19
   acronyms extracted). Markdown extractors split the glossary
   into two tables when a divider line (`|---|`) appears mid-
   table; the row immediately after the divider lands in
   `tbl.headers`, not `tbl.rows`. `_extract_definitions` only
   walked rows, so SDM was silently dropped.
2. Even with the chunk text containing the acronym, retrieval
   couldn't rank it for *"What is SDM?"*. The glossary chunk is
   dominated by 18 other acronyms; BM25 weights the chunk with
   the most "SDM" mentions (an operational chunk), and dense
   similarity for a 4-token query is noisy.
3. No mechanism to let the system route definitional queries
   directly to glossary entries.

**Decision**
Implement a three-layer fix; ship all three together because any
one alone is insufficient.

**A. Parser** (`structural_parser._extract_definitions`)
- Walk `tbl.headers` in addition to `tbl.rows`.
- Filter the canonical column-header row via a token-set check:
  both columns' headers must be entirely from a known canonical
  set (`acronym, term, definition, abbreviation, meaning,
  description, …`) to be treated as a real header. Otherwise
  fold them into the candidates list.
- VZW LTEOTADM `definitions_map`: 18 → 19 entries.

**B. Glossary chunks** (`vectorstore.chunk_builder._build_glossary_chunks`)
- Each entry in `definitions_map` becomes its own chunk:
  - `chunk_id = "glossary:<plan_id>:<acronym-slug>"` —
    slug strips non-`[A-Za-z0-9_-]+`.
  - `metadata.doc_type = "glossary_entry"`,
    `metadata.{acronym, expansion}` populated.
  - Text leads with `<ACRONYM>: <expansion>` so BM25 (high TF)
    and dense (concise definition) both rank it top for short
    acronym queries.
- These are *additional* to the requirement chunk for the
  definitions section, not a replacement.

**C. Glossary pin** (`query.rag_retriever`)
- `_ACRONYM_QUERY_RE` matches: "What is X", "What does X mean",
  "What does X stand for", "Define X", "Definition of X",
  "Meaning of X", "Expand acronym X" / "Expand X".
  X = 2-15 chars, first char letter, rest letters/digits/dash/
  underscore. Case-insensitive.
- `RAGRetriever.__init__` builds `_glossary_by_acronym` once by
  scanning `store.get_all()` for `doc_type=glossary_entry`
  chunks. Empty on pre-D-043 corpora (back-compat).
- `retrieve()` runs normal retrieval (graph scope → BM25+dense →
  rerank → diversity) FIRST, then if the regex matches AND the
  acronym is in the index, prepends matched glossary chunks
  with dedup-by-chunk-id, and trims back to top_k.
- Pin runs *after* the cross-encoder so the encoder doesn't
  demote a chunk we know is the answer.

**Why this over alternatives**
- *Augment retrieval with a synthetic acronym field instead of a
  separate chunk* — rejected. Would require dual-indexing
  (acronyms vs body) or per-query field weighting; adds surface
  area without obviously winning over the deterministic pin.
- *Boost glossary chunks via a score multiplier in
  `_TYPE_BM25_WEIGHT`-style policy* — rejected. Score-boosting is
  fragile (depends on score distribution) and doesn't solve the
  case where the glossary chunk doesn't make the candidate cut.
- *LLM-side fix only — let the synthesizer query a definitions
  service* — rejected. Adds an LLM call per acronym query,
  doubles latency, and removes citations (the corpus chunk *is*
  the citation surface).
- *Parser fix only* — rejected per §1.2-3 above; necessary but
  not sufficient.

**Consequences**
- New `Citation.llm_cited: bool` flag (default False) lets
  callers separate LLM-extracted citations from context-fallback
  citations. Eval keeps the legacy aggregate count via
  `len(response.citations)` — the new field is purely additive.
- New `QueryResponse.retrieved_chunks: list[RetrievedChunk]`
  surfaces the post-Stage-4 retrieval set so the Test page can
  render "Returned by RAG" alongside "Cited by LLM". Off the LLM
  hot-path.
- `RAGRetriever.__init__` reads from the store at construction
  time. With the pipeline cache on `app.state` (see web routes),
  this happens once per process.
- 13 new tests (3 parser + 3 chunk-builder + 7 retriever
  glossary-pin scenarios) pin the contract: regex coverage,
  unknown-acronym fall-through, non-acronym queries skipped,
  dedup, back-compat with empty index, slug safety for
  acronyms with spaces / punctuation.
- Future corpora benefit automatically — any plan whose
  `definitions_map` contains an acronym gets a glossary chunk
  AND becomes pin-eligible.

**Related**: D-032 (per-document definitions_map + chunk-build
inline expansion), D-038 (table-anchored definitions extraction),
D-041 (BM25 hybrid — pin runs after fusion). See
[`core/src/query/RETRIEVAL.md`](../../core/src/query/RETRIEVAL.md)
for the end-to-end retrieval architecture in which this lookup
chain sits.


## D-044: Unified LLM/embedding config — `config/llm.json` + uniform 3-tier resolution

**Date**: 2026-05-04
**Status**: Accepted
**Phase**: Development

**Context**
LLM and embedding settings were scattered across four surfaces:
- `EnvironmentConfig` fields (`model_provider`, `model_name`,
  `model_timeout`, `embedding_provider`, `embedding_model`) on
  `environments/<name>.json`.
- `WebConfig` fields (`ollama_url`, `default_model`) on
  `config/web.json`.
- Per-knob env vars (`NORA_LLM_PROVIDER`, `NORA_LLM_MODEL`,
  `NORA_LLM_BASE_URL`, `NORA_LLM_API_KEY`, `NORA_EMBEDDING_PROVIDER`,
  `NORA_EMBEDDING_MODEL`, `NORA_OLLAMA_TIMEOUT_S`).
- CLI flags on `pipeline/run_cli.py` (`--llm-provider`,
  `--model`, `--model-timeout`, `--embedding-provider`,
  `--embedding-model`).

The dispersion meant: machine-specific defaults landed in tracked
env-config files (problem for teammates committing their personal
paths); web and pipeline read different sources for the same
"what LLM should we use?" question; the documented precedence
order varied per knob; and adding a new knob required edits in
five places.

**Decision**
One canonical home, one resolution rule:

- **File**: `config/llm.json` (tracked template, empty defaults).
  Fields: `llm_provider`, `llm_model`, `llm_timeout`, `llm_base_url`,
  `llm_api_key`, `embedding_provider`, `embedding_model`,
  `ollama_url`, `ollama_timeout_s`, `skip_taxonomy`, `skip_graph`.
  Loaded once per process via `LLMConfigFile.load()` (cached).

- **Resolution chain (per field, highest priority first)**:
  1. CLI flag (`--llm-provider`, `--model`, …).
  2. Env var (`NORA_LLM_PROVIDER`, `NORA_LLM_MODEL`, …).
  3. `config/llm.json` field.
  4. Built-in default.

  Each `resolve_*` function in `core/src/env/config.py` walks the
  chain in this exact order and returns on the first non-empty value.

- **Back-compat**: legacy `EnvironmentConfig` LLM/embedding fields
  remain a fallback **below** `config/llm.json` (so existing
  `environments/env_vzw.json` still works) with a log line
  documenting deprecation. Removable in a future release once
  user envs have migrated.

**Why this over alternatives**
- *Keep settings spread across `WebConfig` + `EnvironmentConfig`*
  — rejected. Adding a new LLM knob requires edits in both files,
  duplicate validation, and inconsistent precedence (web reads
  one path, pipeline reads another for the same question).
- *Merge into `config/web.json`* — rejected. Web-specific
  settings (host, port, root_path, path_mappings) are unrelated
  to "what LLM is the project using?" and tying them couples
  unrelated lifecycles.
- *Flat env-var-only config* — rejected. Twelve knobs is too
  many env vars for a clean shell prompt; users want a file
  they can edit + check into a personal setup script.
- *Strict 3-tier (drop env-config back-compat entirely)* —
  rejected for v1; would force teammates to migrate their
  existing `environments/<name>.json` files in lockstep with
  this commit. Deferred until a major-version bump.

**Consequences**
- One new tracked file (`config/llm.json`); empty defaults so
  fresh clones are unaffected unless the user opts in.
- Each `resolve_*` in `core/src/env/config.py` walks 4 tiers
  (CLI → env var → config/llm.json → env-config back-compat) and
  returns the first non-empty value.
- New `LLMConfigFile` dataclass + module-level cache + test
  `_reset_llm_config_cache()` hook.
- Module-level constants `DEFAULT_LLM_CONFIG_PATH`,
  `SKIP_TAXONOMY_ENV_VAR`, `SKIP_GRAPH_ENV_VAR`, `RAG_ONLY_ENV_VAR`
  added to the env module's public surface.
- 11 new tests pin the per-field resolution chain (CLI beats env
  var beats config beats env-config beats default for
  `llm_provider`; analogous pins for `embedding_provider`,
  `embedding_model`, `skip_taxonomy`, `skip_graph`,
  `NORA_RAG_ONLY` implies both).
- README config table consolidates 5 prose subsections into one
  19-row, 4-column reference (commit babd9f0).

**Related**: D-022 (per-env runtime directory; settings scoped to
the env vs settings global to the install — `config/llm.json` is
intentionally the latter), D-045 (RAG-only mode shares the same
3-tier resolution shape for `skip_taxonomy` / `skip_graph`).


## D-045: RAG-only pipeline mode — skip taxonomy + graph, stub-graph fallback at query time

**Date**: 2026-05-04
**Status**: Accepted
**Phase**: Development

**Context**
The pipeline assumes a full nine-stage build: extract → profile →
parse → resolve → taxonomy → standards → graph → vectorstore →
eval. Two stages — taxonomy and graph — make every run depend on
LLM-derived feature mappings (taxonomy) and a constructed
`networkx.DiGraph` (graph). This is structurally fine but operationally
heavy:

- The taxonomy LLM is the dominant source of run-to-run
  non-determinism (A8 variance experiment showed up to 6.7pp
  spread across three runs on the same vectorstore).
- For concept-shaped queries (cross_doc, standards_comparison)
  the graph scoping in Stage 3 of the query pipeline can do more
  harm than good — it shrinks the candidate pool to a feature-
  mapped subset, occasionally excluding the correct chunk that
  RAG would otherwise rank high.
- A user wanting to A/B "what does retrieval look like without
  the graph?" had no way to answer that without manually deleting
  artifacts and writing a stub.

A baseline-comparison option that pipes around taxonomy + graph
without rewriting the pipeline is a real need.

**Decision**
Add a runtime mode that skips the two stages and makes the rest
of the pipeline tolerate their absence.

- **Three knobs, parallel 3-tier resolution** (per D-044's chain):

  | Knob | CLI | Env var | `config/llm.json` |
  |---|---|---|---|
  | `skip_taxonomy` | `--skip-taxonomy` | `NORA_SKIP_TAXONOMY=1` | `skip_taxonomy: true` |
  | `skip_graph` | `--skip-graph` | `NORA_SKIP_GRAPH=1` | `skip_graph: true` |
  | both at once | `--rag-only` | `NORA_RAG_ONLY=1` | (set both fields) |

  Existing `EnvironmentConfig.skip_taxonomy` (D-040 era) gains a
  parallel `skip_graph: bool = False` field for env-config
  back-compat below the new file.

- **Pipeline-side**: `pipeline/run_cli.py` stage-filter drops
  `taxonomy` and/or `graph` from the run list when the corresponding
  knob is on.

- **Query-side**: `core/src/query/pipeline.py` gains
  `build_stub_graph_from_store(store) -> nx.DiGraph` which derives
  a minimal MNO/Release/Plan-only graph from chunk metadata. Both
  the web `/test` route and the eval stage detect missing
  `<env_dir>/out/graph/knowledge_graph.json` and:
    1. Build the stub via `build_stub_graph_from_store`.
    2. Construct `QueryPipeline(graph=stub, …)`.
    3. Set `pipeline._bypass_graph = True` so Stage 3 emits an
       empty `CandidateSet`.
    4. Stage 4 (`RAGRetriever.retrieve`) routes to the metadata-
       only path (`_retrieve_metadata`) — filters by MNO/release
       only, no candidate-req-id gate.
  EvalRunner picks up the bypass via its existing
  `run_all(questions, bypass_graph=True)` hook from D-001 era.

**Why this over alternatives**
- *Pure runtime flag (no stage-filter)* — rejected. Building a
  graph nobody will read wastes a stage's worth of LLM calls and
  ~30s of compute on every run.
- *Drop `_bypass_graph` and let the resolver/scoper handle a
  None graph* — rejected. Resolver depends on graph for available-
  MNO/release discovery; making graph nullable cascades type
  changes through five files. Stub graph is cheap (~3 nodes, ~2
  edges for env_vzw) and keeps every existing constructor working.
- *Make RAG-only the default* — rejected for v1. Graph scoping
  is a known win on lookup-shaped queries (D-039 entity priority);
  the empirical question of which mode wins per category is what
  the new flag exists to answer.
- *Build the stub at vectorstore stage time, not lazily at query
  time* — considered. Lazy is better because:
    a. Vectorstore stage currently has no graph dependency; adding
       one would couple a previously-clean module boundary.
    b. The stub construction is fast (~milliseconds for env_vzw
       scale) so caching it on disk has negligible benefit.
    c. Lazy means the stub auto-updates if the vectorstore changes
       without requiring a separate "stub-rebuild" step.

**Consequences**
- New invariant exception in `core/src/query/MODULE.md`: the
  "Graph-first, then RAG" rule (D-001) is suspended when
  `pipeline._bypass_graph = True`. Documented inline.
- Eval results in RAG-only mode are NOT directly comparable to
  full-pipeline baselines — Stage 3 is a different filter (or
  no filter). New baselines need their own A-letter labels in
  STATUS.md.
- `_run_query_sync` on the web side previously errored on missing
  graph file ("Knowledge graph not found at …"); the early exit
  is removed, replaced with the stub-graph fallback.
- `_build_pipeline` in `routes/query.py` switches from
  hardcoded `SentenceTransformerEmbedder` to `make_embedder(
  vs_config)` factory — needed because RAG-only with Ollama-
  built vectorstores (e.g. `qwen3-embedding:4b`) was unreachable
  through the old path. (Caught and fixed mid-session — error
  was "Repo id must use alphanumeric chars" because HF prefixed
  the Ollama model name with `sentence-transformers/`.)
- 4 new tests pin the stub-graph contract: emits the right node
  types per metadata, wires `has_release` / `contains_plan`
  edges, omits Requirement / Feature / Standard nodes, and an
  end-to-end QueryPipeline+stub+`_bypass_graph=True` returns
  chunks for a query.
- Future per-corpus tuning: corpora with rich-bodied parents and
  lookup-heavy questions should keep graph mode on; corpora with
  thin parents and concept-heavy questions are candidates to
  flip RAG-only on by default.

**Related**: D-001 (graph-routes-then-RAG; this is the explicit
exception), D-039 (entity-priority graph scoping; only useful
when graph is on), D-040 (per-type top_k; both modes use it),
D-041 (BM25 hybrid; works in both modes), D-044 (unified LLM
config; this decision uses the same 3-tier shape for its
knobs).

---

## D-046: Document-rooted hierarchy paths in chunks (text + metadata)

**Date**: 2026-05-05
**Status**: Accepted
**Phase**: Development

**Context**
Pre-D-046, `chunk_builder` emitted `[Path: SCENARIOS > ATTACH]`
on chunk text — section path within the document, no document
identifier in the path string. The full Document > Section >
Subsection chain wasn't anywhere on the chunk: the embedder
only saw "SCENARIOS > ATTACH", and the retrieval-side context
builder had to walk back to the graph node to recover which
document the chunk came from.

This caused two problems:
- Embeddings for chunks from different documents that happened
  to share a section title clustered together. "ATTACH" reqs
  from LTEDATARETRY and LTEOTADM were near-duplicates in vector
  space because the embedded text was identical structurally.
- Retrieval-side grouping (the eventual Step 3 hierarchy
  grouping) needed to read paths from somewhere structured.
  Reading from the graph forced a graph dependency on the
  grouping logic; reading from chunk text required parsing the
  `[Path: ...]` line back out of free-form text.

**Decision**
Two coupled changes:

- **In chunk text**: prepend `plan_name` (or `plan_id` fallback
  when `plan_name` is empty) as the root segment of the
  `[Path: ...]` line. Output: `[Path: LTEDATARETRY > SCENARIOS
  > ATTACH]`. Disabled when `include_hierarchy_path=False`.
  Suppressed entirely when both `plan_name` and `plan_id` are
  empty AND the requirement's hierarchy is empty.

- **In chunk metadata**: store the full path as a `list[str]`
  under the `hierarchy_path` key on every chunk's metadata
  dict (requirement chunks AND glossary chunks). Always
  populated; `include_hierarchy_path` only gates the text
  prefix, not the metadata. Glossary chunks store
  `[doc_root]` (single-element list).

`context_builder._enrich_chunk` prefers the chunk-metadata
path; falls back to the graph node's `hierarchy_path` when
the metadata is absent (back-compat for vectorstores built
before D-046).

**Why this over alternatives**
- *Path in text only, not metadata* — rejected. Forces
  retrieval-side grouping to parse free-form chunk text. Brittle
  if the prefix line ever changes shape (e.g. when the MNO
  header or req_id line is reordered).
- *Path in metadata only, not text* — rejected. The embedder
  loses the structural signal. The whole point is for the dense
  vector to encode "this chunk is from document X about topic Y"
  not just "this chunk is about topic Y".
- *Use plan_id everywhere (not plan_name)* — rejected. plan_id
  is opaque (`LTEDATARETRY`); plan_name is human-readable
  (`LTE Data Retry`). Embeddings benefit from the natural
  language form. plan_id remains the fallback when plan_name
  is missing.
- *Include the full hierarchy chain in chunk text already* —
  was already the case for hierarchy below the document; this
  decision only adds the document root above it.

**Consequences**
- **Vectorstore must be rebuilt** to surface the new metadata
  field on existing data. Old vectorstores work — context
  builder falls back to the graph node — but they won't get
  the embedding-quality benefit until rebuilt.
- ChromaDB metadata supports `list[str]` values, so the path
  stores natively. They can't be used in `where=` equality
  filters but read-back works fine.
- New `_build_chunk_text` parameter `plan_id: str = ""` with
  the corresponding call-site update in `_build_tree_chunks`.
- 11 new tests in `core/tests/test_chunk_builder_hierarchy.py`
  pin: text format, plan_id fallback, both-empty suppression,
  metadata always-present (independent of text flag),
  glossary-chunk root.
- Existing `test_vectorstore.py::test_chunk_text_has_hierarchy_path`
  updated for the new `LTE_DATARETRY > ROOT > Section 2 Title`
  format.
- Verified live: env_vzw chunks show paths like
  `['LTE_ATCommands_For_Test_Automation', 'LTE AT commands for
  Test automation']`.

**Related**: D-001 (graph + RAG hybrid; this strengthens RAG by
giving embeddings document-level structure), D-038 (definitions
extraction; glossary chunks now also carry the metadata path),
D-043 (acronym lookup chain; glossary chunks were the unit
introduced there, this adds doc-root metadata to them), D-047
(threshold filter consumes the same chunk metadata for the
"not found" path).

---

## D-047: Relevance threshold + "not found" response (Stage 4.5)

**Date**: 2026-05-05
**Status**: Accepted
**Phase**: Development

**Context**
Off-topic queries ("recipe for chocolate cake") still produced
synthesized answers because the LLM was given Stage-4 retrieval
even when every chunk was a weak distance match. Empirical
sweep against env_vzw + qwen3-embedding:4b-q8_0:

- Relevant queries (T3402, attach reject): cosine distances
  0.20–0.41
- Off-topic queries (Westphalia, cake): cosine distances
  0.74–0.77

A 0.33-wide gap between the worst relevant and the best
off-topic chunk. The LLM was synthesizing from chunks at 0.75
distance — primary source of confabulated answers on queries
that simply have no good match in the corpus.

The user's stated principle: *"the system shall not pretend it
is an Oracle."* Off-topic queries should return an explicit
"not found" message rather than an LLM hallucination dressed up
in formatting that mimics a cited answer.

**Decision**
New optional Stage 4.5 in `QueryPipeline.query()`:

- New constructor param `max_distance_threshold: float | None
  = None`. None → filter disabled (back-compat).
- After Stage 4 retrieval, drop chunks where
  `similarity_score > threshold`.
- If the filtered list is empty, return a `QueryResponse` with
  the deterministic `_NOT_FOUND_ANSWER` text **without** running
  Stage 5 (context assembly) or Stage 6 (LLM synthesis). This
  saves the LLM call AND prevents the LLM from being given an
  empty context that it would politely confabulate around.
- New `_TYPE_MAX_DISTANCE` dict keyed by `QueryType`, empty for
  now. Reserved for Step 4 (intent classification) where the
  Fact intent will need a stricter threshold than the general
  pipeline default.
- Web pipeline build sets the default to **0.5** with a
  `NORA_MAX_DISTANCE_THRESHOLD` env-var runtime override
  (`off`/`none`/`""` disables; any float overrides). Logs the
  effective value at pipeline-build time.

**Why this over alternatives**
- *Filter inside `RAGRetriever`* — rejected. The retriever is
  generic and shared between query paths (eval, web, CLI). A
  threshold is a query-pipeline policy, not a retrieval
  concern. Keeping it in `QueryPipeline` lets the retriever
  return raw scores and lets the pipeline (which has the query
  type / intent) make the policy decision.
- *Filter inside `ContextBuilder`* — rejected. Context builder
  doesn't know about per-query-type thresholds and would have
  to grow that responsibility. Stage 4.5 (between retrieval and
  context assembly) is the natural place.
- *Use a similarity score (1 - distance) instead of distance* —
  considered. Would let users think in "min similarity" terms
  (higher = stricter), which matches their mental model better
  than "max distance" (lower = stricter). Rejected for now to
  keep `similarity_score` field semantics stable across the
  codebase; flipping the field at this point would change UI
  display values that users have already seen. Revisit if a
  cleaner abstraction emerges.
- *Per-vectorstore threshold (stored in the saved config.json
  next to chroma data)* — considered. Threshold is calibrated
  to the embedding model + corpus, so co-locating it with the
  vectorstore is logically clean. Rejected for v1 because no
  existing code reads back from the saved config at query
  time, and adding a path felt premature for a single tuning
  value. Promote to that scheme if/when multiple vectorstores
  with different models coexist in one process.
- *Hard-fail with an exception* — rejected. The "not found"
  outcome is a normal answer in the user's workflow, not an
  error. Returning `QueryResponse` keeps the call site uniform.

**Consequences**
- **Threshold is model-specific.** Default 0.5 is pinned to
  qwen3-embedding:4b-q8_0 on the OA corpus. Switching the
  embedding model requires a re-sweep. Flagged in STATUS.md.
- New public field on `QueryPipeline.__init__`; new module-
  level constants `_NOT_FOUND_ANSWER`, `_TYPE_MAX_DISTANCE`.
- 13 new tests in `core/tests/test_query_threshold.py` pin:
  threshold disabled (back-compat), all-above, all-below,
  mixed, exactly-at-threshold, just-above-threshold,
  not-found shape (intent carried, candidate count carried,
  no citations, message non-empty), strict/lenient sweeps.
- Web wiring (`core/src/web/routes/query.py`) adds the helper
  `_resolve_max_distance_threshold` reading
  `NORA_MAX_DISTANCE_THRESHOLD` and logs the effective value
  at pipeline-build time.
- `_TYPE_MAX_DISTANCE` is the seam Step 4 will populate for
  per-intent overrides — Fact intent will get a stricter cap.

**Related**: D-046 (chunk metadata; threshold reads
`similarity_score` populated alongside the new
`hierarchy_path`), D-040 (per-type top_k; per-type threshold
mirrors the same shape), D-041 (BM25 hybrid; threshold is
applied after fusion + diversity, not per-component).

---

## D-048: `vectorstore_cli` `--config <path>` replaces `config/llm.json` (Option A precedence)

**Date**: 2026-05-05
**Status**: Accepted
**Phase**: Development

**Context**
`vectorstore_cli`'s `_build_config` previously knew nothing
about `config/llm.json` — it read `--config <path>` if given
and fell back to its own dataclass defaults otherwise. Users
setting `embedding_model: qwen3-embedding:4b-q8_0` in
`config/llm.json` were surprised when `vectorstore_cli` still
defaulted to `sentence-transformers/all-MiniLM-L6-v2`. The
pipeline runner (`run_cli.py`) honored `config/llm.json`; the
standalone vectorstore CLI didn't, so the two diverged on the
"which embedding model is the project using?" question.

Wiring `config/llm.json` into `vectorstore_cli` was
straightforward — the existing `resolve_embedding_provider` /
`resolve_embedding_model` helpers in `core/src/env/config.py`
already implement the canonical 3-tier rule
(CLI > env > config/llm.json > default). The interesting
question was: how should `--config <path>` interact with
`config/llm.json`?

Two options were on the table:

- **Option A (chosen).** `--config <path>` *replaces*
  `config/llm.json` at the config-file tier. Precedence:
  CLI > env > (`--config` if supplied, else `config/llm.json`)
  > default.
- **Option B.** `--config <path>` is just another value
  source. Precedence: CLI > env > config/llm.json > `--config`
  > default. Both files contribute; one of them wins by some
  sub-rule.

**Decision**
**Option A.** When `--config <path>` is supplied, the resolver
chain treats that file as the config-file tier and skips
`config/llm.json` entirely for that run.

Implementation (in `_build_config`): if `args.config` is set,
load the file and inline the tier walk
(`args.provider or env_var or config.embedding_provider or
DEFAULT`). If not set, call the existing `resolve_*` helpers
(which read `config/llm.json` at tier 3).

**Why this over Option B**
- A user typing `--config experiment.json` is being explicit
  about reproducing a frozen experiment. Letting
  `config/llm.json` silently override the experiment defeats
  the purpose of pinning the config file at all.
- Option B's "stack and let one win" rule (whichever wins) is
  hard to predict from the call site. Option A is one rule:
  "file you point at replaces the project default."
- The user's stated rule is "CLI > env var > config json
  file under config/" — three tiers. Option A keeps three
  tiers from the user's perspective; Option B introduces a
  fourth.
- CLI flags + env vars still override `--config`, so the user
  can scope-narrow within an experiment without editing the
  pinned file.

**Consequences**
- New imports from `core.src.env.config` in
  `vectorstore_cli._build_config`:
  `DEFAULT_EMBEDDING_*`, `EMBEDDING_*_ENV_VAR`,
  `EMBEDDING_PROVIDERS`, `resolve_embedding_*`.
- Two pre-existing `test_env_config.py` bugs surfaced + fixed:
  `test_resolve_embedding_provider_precedence` and
  `test_resolve_embedding_model_precedence` previously
  assumed `config/llm.json` was empty (the project's prior
  state). Now monkey-patch `DEFAULT_LLM_CONFIG_PATH` to a tmp
  empty file. These tests were latent and only failed once
  the user populated `config/llm.json`.
- `_build_config`'s docstring now spells out the 4-tier
  resolution explicitly so future maintainers don't re-derive.
- Inverse-scenario verified live: with `config/llm.json`
  populated, no `--config`, no env var → resolver picks up
  qwen3-embedding:4b-q8_0. With `--config experiment.json`
  pinning bge-large → resolver picks up bge-large
  (config/llm.json is bypassed).
- The same pattern (Option A) is the likely answer for any
  future CLI that adds a `--config <path>` flag while
  participating in `config/llm.json`-driven defaults.

**Related**: D-044 (unified LLM/embedding config; this
extends D-044's resolution chain to a CLI that previously
didn't participate). The "back-compat to deprecated env-config
fields" tier from D-044 stays at the bottom and is unaffected.

---

## D-049: Stage 4.7 — hierarchy-based grouping with user-facing disambiguation

**Date**: 2026-05-06
**Status**: Accepted
**Phase**: Development

**Context**
After D-046 (chunk metadata `hierarchy_path`) and D-047 (relevance
threshold filter), retrieval still produced a failure mode: when a
query genuinely had multiple plausible answers in the corpus
(e.g. "What are the security requirements?" hits chunks under
multiple specs and multiple subsections), the LLM would synthesize
a single answer that conflated topics — picking somewhat arbitrarily
which chunks to lean on, and producing prose that read as
authoritative but was a low-confidence collapse of distinct
realities.

The user's stated principle, surfaced at the start of the
retrieval-improvements plan: *"the system shall not pretend it is an
Oracle."* When retrieval can't distinguish between plausible answer
groups, it should surface the choice rather than fabricate a synthesis.

**Decision**
New optional **Stage 4.7** in `QueryPipeline.query()`, between the
threshold filter (D-047, Stage 4.5) and context assembly (Stage 5):

1. Cluster post-threshold chunks by **greedy longest-common-prefix**
   on `hierarchy_path` metadata. Two chunks share a group iff they
   share at least the document root. Adjacent chunks in the
   alphabetically-sorted path order extend the running group's LCP.
2. **Group score** = `min(c.similarity_score for c in chunks)`. The
   best chunk anchors the group's relevance; weak siblings don't
   drag.
3. **Decision rule**: when `gap_between_top_groups(groups) >=
   gap_threshold`, **auto-commit** to the top group — its chunks
   alone go to Stage 5. When gap < threshold, return a
   `QueryResponse(disambiguation_required=True, groups=[…])` and
   skip Stages 5 and 6 (mirrors D-047's `_NOT_FOUND_ANSWER`
   short-circuit).
4. **Disambiguation UX**: the test page renders one Bootstrap card
   per group, each with the path breadcrumb, representative section
   titles, and a "Synthesize from this group" button. Click submits
   the picked group's chunk IDs to a new `pinned_chunk_ids` path
   that re-runs synthesis from those chunks only.
5. **Per-intent opt-out**: SUMMARIZE intent (D-051) is added to
   `_TYPE_DISABLE_GROUPING` because it inherently wants ALL groups
   merged into one synthesis — picking one defeats the purpose.

Three knobs in the unified resolver chain (D-050 / `config/retrieval.json`):

- `enable_grouping: bool` — global toggle. Default False (preserves
  pre-Step-3 behavior); flip to True to opt in.
- `gap_threshold: float` — distance gap below which disambiguation
  triggers. Default 0.05.
- `gap_threshold_by_type: dict[str, float]` — per-intent overrides.

**Why this over alternatives**
- *Pick the highest-scoring chunk and synthesize* — the pre-Step-3
  behavior. Loses the user's information-need signal whenever the
  top-K spans semantically-distinct groups; produces the
  "authoritative-looking but conflated" hallucination class.
- *Always return all groups; let the LLM merge* — rejected. The
  whole point of grouping is to let the user pick when the system
  can't. Always-merge would still pretend the system has one answer.
- *Cluster by k-means on the embeddings rather than by hierarchy* —
  rejected. Hierarchy is a structural signal the corpus authors
  provided; using it directly is more interpretable to users
  (path breadcrumbs are human-meaningful) and cheaper than running
  clustering at every query.
- *Group score = mean / max distance* — rejected for v1. min picks
  up "this group has at least one strong match"; mean dilutes when
  the group has weak siblings; max is dominated by the worst chunk.
  Empirically, min is the cleanest signal for "is this group
  relevant at all?"
- *Hard-fail / raise on ambiguity* — rejected. Disambiguation is a
  normal answer in the user workflow, not an error.

**Consequences**
- **Off by default.** `enable_grouping=False` preserves pre-Step-3
  behavior bit-for-bit; existing callers see no change.
- **Threshold is calibrated to the embedding model.** 0.05 default
  came from in-session tuning on env_vzw + qwen3-embedding:4b-q8_0.
  Different models will need different defaults — the per-type
  override map is the migration path.
- **New `QueryResponse` fields**: `disambiguation_required: bool`
  and `groups: list[ChunkGroup]`. Old API consumers that ignore
  unknown fields are unaffected; new consumers need to check the
  flag before assuming `answer` is a real synthesis.
- **New `pinned_chunk_ids` parameter on `QueryPipeline.query()`** —
  bypasses Stages 2-4.7 (resolver / graph scope / rewrite / RAG /
  threshold / grouping) and goes straight to synthesis. Powers the
  card-click flow; also a useful primitive for "synthesize from a
  hand-picked set" use cases.
- **New module `core/src/query/grouping.py`** with
  `group_chunks_by_hierarchy()` and `gap_between_top_groups()`.
  Singletons, multi-doc clusters, and back-compat (chunks with
  empty `hierarchy_path`) all handled.
- **Web layer adds a new endpoint** (`POST /api/test/synthesize-group`)
  + Bootstrap card rendering in `_answer.html`.
- **38 new tests** across grouping logic + pipeline integration +
  pinned-chunks path + cap interaction.

**Related**: D-046 (chunk metadata hierarchy_path; the input
grouping reads), D-047 (threshold filter; runs before grouping),
D-050 (Phase 3-config infrastructure; the knobs ride on it),
D-051 (FACT/SUMMARIZE intents; SUMMARIZE opts out of grouping),
D-052 (citation audit; runs after this stage).

---

## D-050: Phase 3-config — `config/retrieval.json` extends the unified resolver chain

**Date**: 2026-05-06
**Status**: Accepted
**Phase**: Development

**Context**
Stage 4.7 (D-049) introduced two new tunable knobs (`enable_grouping`,
`gap_threshold`) plus a per-type override map. The user's request
when Step 3 was scoped: *"all tunable parameters [shall] be
configurable through the standard 3-tier config architecture (CLI
> env > config-file > default)."*

Existing retrieval tunables (`_TYPE_TOP_K`, `_TYPE_BM25_WEIGHT`,
`_TYPE_RERANK_ENABLED`, `_TYPE_REWRITE_ENABLED`) lived as
hand-edited dicts in `core/src/query/pipeline.py` — not configurable
without code changes. The decision: do we (a) migrate everything in
one big refactor, or (b) seed new infrastructure for the Step 3
knobs and migrate existing knobs incrementally?

**Decision**
Phased approach. **Phase 3-config (this commit)** adds the
infrastructure — a new file, dataclass, and resolver helpers — but
wires only the Step 3 knobs through it. **Phase 4-migrate (next
session, separate scope)** migrates the existing per-type dicts
into the same file with backward-compatible defaults. Each migrated
knob is its own atomic commit.

Concretely:

- New `config/retrieval.json` parallel to `config/llm.json`
  (D-044). Schema seeded with `enable_grouping`, `gap_threshold`,
  `gap_threshold_by_type`. Comment in the file documents Phase
  4-migrate's planned additions.
- New `RetrievalConfig` dataclass in `core/src/env/config.py`
  mirrors `LLMConfigFile`'s shape (cached via
  `_retrieval_config()`, test hook `_reset_retrieval_config_cache()`).
- Two new resolver helpers `resolve_grouping_enabled` /
  `resolve_gap_threshold` follow the D-044 chain: CLI > env var >
  config file > default. The threshold helper additionally honors a
  per-type override (`gap_threshold_by_type[query_type]`) above the
  scalar default in the file.
- New env vars `NORA_RETRIEVAL_GROUPING_ENABLED` /
  `NORA_RETRIEVAL_GAP_THRESHOLD`. Naming convention is
  `NORA_RETRIEVAL_<KNOB>` for everything in `config/retrieval.json`,
  parallel to D-044's `NORA_LLM_*` / `NORA_EMBEDDING_*`.
- **Per-type maps are file-only.** No env var or CLI flag for them
  — JSON-typed and rarely need shell-level override.

**Why this over alternatives**
- *Migrate all retrieval knobs at once* — rejected. Each knob needs
  its own resolver, env var, CLI flag, doc update, and test;
  bundling would produce a 6-hour commit chain blocking Step 3
  shipping. Phased migration keeps each piece reviewable.
- *Drop the per-type dicts and only have file-driven config* —
  rejected. The dicts encode empirical tuning rationale (comments
  explain why each value); preserving them as built-in defaults
  the file overrides keeps that institutional knowledge visible.
- *One mega config file (`config/all.json`)* — rejected. The D-044
  separation (`config/llm.json` for LLM/embedding,
  `config/retrieval.json` for retrieval, `config/web.json` for web
  serving, `config/env.json` for DB paths) keeps unrelated lifecycles
  separate.
- *Env vars for everything (no JSON file)* — rejected for the same
  reason D-044 rejected it: too many knobs make for messy shell
  prompts.

**Consequences**
- **One new tracked file** `config/retrieval.json` ships with empty
  defaults so fresh clones are unaffected.
- **Pattern set for Phase 4-migrate**: each knob gets a `resolve_*`
  helper, optional env var, dataclass field, test isolation pattern
  (monkey-patch `DEFAULT_RETRIEVAL_CONFIG_PATH` to a tmp file).
- **Two pre-existing test bugs surfaced** when extending the
  resolver test patterns to `RetrievalConfig`:
  `test_resolve_embedding_provider_precedence` and
  `test_resolve_embedding_model_precedence` weren't isolating from
  `config/llm.json` on disk. Fixed alongside this commit.
- **9 new resolver tests + 22 grouping tests** pin the precedence
  chain end-to-end.
- **D-053 (Config-page DB layer) builds on this**: when the DB
  hydrates the cached `RetrievalConfig` instance at startup, the
  existing resolvers automatically pick up the DB layer with no
  code changes — no ad-hoc plumbing needed for new knobs.

**Related**: D-044 (unified LLM config; this is the same pattern
extended), D-049 (Stage 4.7; the first set of knobs to ride on this
infrastructure), D-053 (Config-page DB layer; slots into the same
chain).

---

## D-051: FACT and SUMMARIZE intent classification — query-shape vs query-intent

**Date**: 2026-05-06
**Status**: Accepted
**Phase**: Development

**Context**
The existing `QueryType` values (SINGLE_DOC, CROSS_DOC,
CROSS_MNO_COMPARISON, RELEASE_DIFF, STANDARDS_COMPARISON,
TRACEABILITY, FEATURE_LEVEL, GENERAL) classified queries by their
**scope shape** — how many documents / specs / MNOs the answer
spans. The pipeline used the type to drive `top_k` widening, BM25
weighting, rerank toggling, etc.

But shape isn't intent. "Explain authentication requirements"
classified as SINGLE_DOC or GENERAL (no breadth triggers in the
phrasing) — and got `top_k=10` plus Stage 4.7 grouping that picked
the deepest LTEOTADM AUTHENTICATION subsection, returning 3 chunks
when the user wanted a survey across all the auth-related content
in the corpus. Conversely, "What is the value of T3402?" needed
*precision* (tight top_k, strict threshold, contradiction surfacing)
that none of the shape-types encoded.

The user's contribution: *"add 'Fact' intent — high similarity in
all fragments, per-sentence attribution, contradiction detection
mandatory"* and *"add 'Summarize' intent — structural navigation,
TL;DR first, per-group summaries"*.

**Decision**
Two new `QueryType` values, classified by phrasing:

- **`QueryType.SUMMARIZE`** — survey/summarize intent. Triggers:
  `explain `, `summarize`, `summary of`, `describe `, `give me an
  overview`, `overview of`, `tell me about `. Per-intent knobs:
  `top_k=50` (wide), `bm25_weight=0.2` (mostly dense — user
  paraphrases the topic), `rerank_enabled=False` (cost vs benefit
  at top-50; LLM reads everything anyway), `rewrite_enabled=True`
  (term expansion gathers more), `max_distance_threshold=0.7`
  (lenient — wants breadth including parent/overview chunks),
  `_TYPE_DISABLE_GROUPING={SUMMARIZE}` (skip Stage 4.7 entirely —
  auto-commit to one group throws away the breadth the user wants).
  System prompt: *"Structure your answer in two parts: TL;DR + per-
  section breakdown."*

- **`QueryType.FACT`** — fact-lookup intent. Triggers: `value of`,
  `what value`, `default value`, `default for`, `how many`,
  `how long`, `maximum value`, `minimum value`, `exact value`,
  `specific value`, `what is the limit`, `what is the threshold`.
  Per-intent knobs: `top_k=10` (tight; 1-3 chunks usually carry the
  fact), `bm25_weight=0.5` (term-match for specific tokens),
  `rerank_enabled=True` (precision matters), `rewrite_enabled`
  intentionally absent — default False, because paraphrasing a
  fact-shaped query risks substituting it into a definitional query
  (D-043 acronym path is wrong for "what's the *value*"),
  `max_distance_threshold=0.4` (strict — fact-shaped answers from
  weak chunks are the worst-case hallucination), grouping enabled
  (one fact = one group typically). System prompt: *"Direct answer
  + per-sentence attribution; contradiction handling: surface
  disagreement explicitly when sources differ."*

**Classification priority**: FACT is checked *before* SUMMARIZE in
`_classify_query_type` so "Explain the value of T3402" routes to
FACT (precision) not SUMMARIZE (breadth) when both phrasings
appear. Bare "what is X" stays out of FACT — it's definitional
(D-043 acronym pin) or falls through.

**Why this over alternatives**
- *Add a separate `Intent` enum orthogonal to `QueryType`* —
  considered. Cleaner conceptually (shape and intent are different
  axes) but doubles the routing matrix and requires every per-type
  dict to become a 2-D map. Deferred — single-axis enum works
  while we have only two intent values; revisit if more intents
  land.
- *LLM-driven classification (use `LLMQueryAnalyzer` for FACT/SUMMARIZE
  detection)* — rejected for v1. Phrase triggers are deterministic,
  fast, and explainable; an LLM call adds latency and a failure mode.
  The trigger list can grow as miss cases surface.
- *Add Comparison intent now* — explicitly **deferred**. User asked
  to skip until multi-MNO / multi-release ingestion lands; no test
  data to validate against today.
- *Make breadth-trigger phrases SUMMARIZE instead of CROSS_DOC* —
  rejected. CROSS_DOC ("what are all the X requirements") is a
  *scope* signal — "show every relevant requirement, structured by
  doc". SUMMARIZE is an *output-shape* signal — "produce a TL;DR +
  breakdown." A query can be both (cross-doc summarize); the
  routing currently picks the more-specific intent (SUMMARIZE wins
  when phrased explicitly).

**Consequences**
- **Per-type dicts grew** (`_TYPE_TOP_K`, `_TYPE_BM25_WEIGHT`,
  `_TYPE_RERANK_ENABLED`, `_TYPE_REWRITE_ENABLED`,
  `_TYPE_MAX_DISTANCE`); new `_TYPE_DISABLE_GROUPING` set. Phase
  4-migrate (D-050) will move these into `config/retrieval.json`.
- **Stage 4.7 honors per-intent grouping opt-out** — pipeline checks
  `intent.query_type not in _TYPE_DISABLE_GROUPING` before clustering.
- **Two new `_SYSTEM_PROMPTS` entries** in `context_builder.py`
  with TL;DR-vs-fact framing.
- **29 new tests** pin classification (5 SUMMARIZE phrasings, 6 FACT
  phrasings, classification priority FACT-beats-SUMMARIZE), per-
  intent knob assertions, system-prompt content assertions, and
  Stage 4.7 bypass for SUMMARIZE.
- **`/v1` of contradiction detection is prompt-only.** The FACT
  prompt asks the LLM to surface disagreements; deterministic
  semantic-comparison detection across chunks is left for a future
  step.
- **Comparison intent still deferred** — flagged in STATUS.md
  Next; revisit when second MNO corpus ingests.

**Related**: D-039 (entity-priority graph scoping; FACT queries that
name a specific req still hit this path), D-040 (per-type top_k +
list-style detection; this extends the same shape into intent
routing), D-043 (acronym pin; bare "what is X" stays in this path,
not FACT), D-049 (Stage 4.7; SUMMARIZE opts out via
`_TYPE_DISABLE_GROUPING`).

---

## D-052: Stage 6.5 — per-sentence citation audit

**Date**: 2026-05-06
**Status**: Accepted
**Phase**: Development

**Context**
Synthesis prompts demand inline citations (D-001 invariant: every
factual claim must reference a `(VZ_REQ_X)` or 3GPP TS section).
The synthesizer already extracts citations the LLM mentioned and
back-fills missing ones from context. But two error classes still
slipped through unnoticed:

1. **Uncited factual claims** — sentences in the answer that don't
   cite anything. May be paraphrasing correctly across multiple
   reqs, may be hallucinating; the user has no way to tell.
2. **Fabricated citations** — req IDs in the answer that don't
   appear in the chunks the LLM actually received. Worst-case error
   class — looks authoritative, isn't real. Surfaced in the
   user's session via "What is SDM?" hallucinating "SIMOTA Device
   Management" before D-043 fixed the retrieval-side path; same
   phenomenon for any topic where retrieval misses and the LLM
   invents.

The user's stated principle from Step 5 scoping: *"per-sentence
citation polish layer."*

**Decision**
New **Stage 6.5** runs after synthesis on the normal path:
`audit_answer_citations(response.answer, available_req_ids)` walks
the answer sentence-by-sentence and produces a `CitationAudit`:

- **Sentence splitter** is regex-based with abbreviation handling
  (e.g., i.e., etc., ...), markdown-header detection, and bullet/
  numbered-list awareness. Each list item is its own sentence;
  headers are marked `is_meta=True` and excluded from the cited-
  percentage metric.
- **Citation detector** matches the same regex patterns the
  synthesizer's `_extract_citations` uses (`VZ_REQ_X` and
  `3GPP TS Y, Section Z`). A sentence is considered cited if it
  contains either form.
- **Fabrication detector** flags req IDs in the answer that are
  NOT in `available_req_ids` (the chunks passed to the LLM). 3GPP
  spec citations are external and always pass.
- **`CitationAudit` schema dataclass** carries per-sentence audits
  + summary counts (`cited_sentence_count`, `factual_sentence_count`,
  `fabricated_count`, `cited_percent` property,
  `uncited_sentences` property).

`QueryResponse.citation_audit: CitationAudit | None` — populated on
the normal synthesis path AND the pinned-chunks path; None on
disambiguation/not-found paths (no real answer to audit).

Web layer surfaces the audit in `_answer.html`:
- Inline summary `4/6 sentences cited (66.7%) · 1 fabricated`.
- Collapsible "show uncited" list with yellow border per sentence.
- Red alert banner when fabricated citations exist, listing the
  bad req IDs and the sentence containing them.

**Why this over alternatives**
- *LLM-judged audit (second LLM call to grade the answer)* —
  rejected. Adds latency and another failure mode; deterministic
  regex sufficient for "is there a citation token?" — that's the
  bar.
- *Re-prompt the LLM to fix uncited sentences (Phase 5c)* —
  **deferred**. Costly (a second LLM call per query) and unclear if
  the retry would do better. Real-world miss rates need measuring
  first; revisit after a few weeks of usage data.
- *Strict-mode synthesis (refuse to render any uncited sentence)* —
  rejected. Prose flow needs transition sentences ("The X timer
  governs the procedure.") that don't cleanly attach to one req.
  Too aggressive; would force unnatural phrasing or many false
  positives.
- *Inline highlight of uncited spans in the rendered answer* —
  considered, but rendering deletes the sentence boundaries our
  audit operates on. Showing the audit as a collapsible side-list
  is simpler and doesn't fight the markdown renderer.

**Consequences**
- **Always-on, no LLM call.** Adds < 1ms to every synthesized
  query; a regex pass over a few thousand chars.
- **Two new schema dataclasses** on `QueryResponse.citation_audit`:
  `SentenceAudit` (per-sentence) and `CitationAudit` (summary).
  Old API consumers see new field they can ignore.
- **New module `core/src/query/citation_audit.py`** with the
  splitter + detector. Tested against realistic SUMMARIZE-style
  (TL;DR + bullets) and FACT-style (with contradictions) outputs.
- **Surfaces a metric per query**: `cited_percent`. Lets us see
  "is the LLM following the citation prompt?" objectively. Below
  ~80% suggests prompt-strength issue or weak model.
- **27 new tests** cover sentence splitting (single/multi/abbreviation/
  paragraph/bullet/numbered/header), markdown header detection,
  audit basics, fabrication detection, meta-sentence handling,
  uncited accessor, two realistic answer styles.
- **Phase 5c citation repair (re-prompt) deferred** to STATUS.md
  Next.

**Related**: D-001 (citation invariant; this is the audit layer
that makes it observable), D-043 (acronym pin; preventing the
retrieval-side root cause that this audit catches at the synthesis
side), D-049 (Stage 4.7 disambiguation; runs before this audit on
synthesis path), D-051 (FACT prompt asks for per-sentence attribution
explicitly; audit measures whether the LLM complied).

---

## D-053: Config-page DB layer slots between env vars and JSON files

**Date**: 2026-05-06
**Status**: Accepted
**Phase**: Development

**Context**
Through this session, the user repeatedly asked "did my config
change actually take effect?" — first when wiring a custom Ollama
proxy, then after switching embedding models, then when setting
top_k=25 and getting 50 chunks. Each time required either grepping
the server log for resolved values or running ad-hoc CLI tools.
The signal was clear: **admins want a UI for the config knobs, with
visible verification.**

The user's request that triggered the Config page implementation:
*"all configurable params (that are under config/) can be updated by
the user. All the updated values go into a config db ... user
provides full path from command line or env variable. ... If user
changes some config values, they shall be written to db, and then
rest of web app shall reflect the new values."*

The architectural question: **where does the DB sit in the resolver
chain?** Three plausible options.

**Decision**
The DB layer slots **between env vars and `config/*.json`**:

```
CLI flag > env var > ConfigStore (this DB) > config/*.json > defaults
```

The DB is a **persistent layer for user-edited overrides via the
web UI**. Higher than the JSON files because the user explicitly
set it through the page (more recent, more specific intent).
Lower than env vars because env vars remain the admin's hard-override
escape hatch ("set this for the next 5 minutes without touching the
DB").

Implementation:

- New `core/src/web/config_db.py` — synchronous SQLite-backed
  `ConfigStore` keyed by `(module, key)`. Values JSON-encoded so
  int / bool / float / list round-trip cleanly. Threadsafe via
  internal lock.
- New `core/src/web/config_schema.py` — hand-curated
  `CONFIG_SECTIONS` describing the 13 user-editable knobs (LLM and
  Retrieval sections; categories `feature` / `value` / `tunable`;
  kinds `bool` / `string` / `int` / `float` / `enum` / `password`).
  Drives the form rendering.
- **`apply_to_caches()` at app startup**: overlays every stored
  value onto the cached `LLMConfigFile` / `RetrievalConfig`
  instances. The existing `resolve_*` functions in
  `core/src/env/config.py` automatically pick up the DB layer
  with no plumbing changes — they were already reading from the
  cached instances, so mutating those instances after JSON load
  effectively inserts the DB tier into the chain.
- **`reapply_one()` after each save**: cheaper than a full re-apply
  when the UI edits one field. Pipeline cache (`app.state.query_pipeline`)
  is also invalidated on each save so the next query rebuilds.
- **Opt-in**: new CLI `--config-db` + env var `NORA_CONFIG_DB`. If
  unset, the page renders read-only with a notice; the resolver
  chain falls through as before. No default path, deliberate —
  the user must opt in.

**Why this over alternatives**
- *DB **above** env vars (DB always wins)* — rejected. Env vars
  are the admin's debug / emergency-override channel; making them
  losable to a stale DB row would be a footgun. Order preserves
  the principle that the most-specific, most-recent override
  wins (CLI flag = "I just typed this" beats env var = "this shell
  has it set" beats DB = "I saved this earlier" beats file =
  "this is the project default").
- *DB **below** the JSON files (file always wins)* — rejected.
  Defeats the purpose of the editor — saving a value through the
  UI would no-op if the JSON file had a different value. The DB
  must override the file for "user edited this through the UI" to
  mean anything.
- *Replace JSON files entirely with the DB* — rejected. JSON files
  are project-checked-in defaults; team members pulling main
  inherit them. Deleting that layer would force everyone to also
  set up a DB, breaking the "fresh clone works" property.
- *Modify `resolve_*` functions to read from the DB directly* —
  rejected. Would require a registry of "which DB connection are
  we in?" plumbed everywhere. The chosen approach (overlay onto
  the existing dataclass cache) is dramatically simpler and
  reuses the resolver chain unchanged.
- *Always-default DB at `<env_dir>/state/config.db`* — considered
  but rejected. Some users won't want persistence at all (CI runs,
  ephemeral test environments); explicit opt-in is cleaner than
  always-creating a DB no one asked for.

**Consequences**
- **One new SQLite file per env that opts in**, ~8 KB schema-only.
  Grows by ~100 B per saved value.
- **New `app.state.config_store`** — None when DB disabled (read-
  only Config page); ConfigStore instance when enabled. Other
  routes can also read from it (e.g. `routes/query.py` reads
  `pipeline.top_k_cap` and `pipeline.max_distance_threshold` for
  knobs that don't have a cached dataclass slot).
- **`apply_to_caches()` runs once at app startup**, before any
  request lands. The first query naturally builds its pipeline
  with the DB-overlaid cache state.
- **Save invalidates the cached pipeline.** Next query rebuilds
  with the new resolved values. The startup-log lines (`Web LLM
  resolved: …`, `Top-K cap: …`, `Stage 4.7 grouping: …`) print
  again on first query, so admins can see what landed.
- **Two pre-existing build-time-only knobs are now settable but
  misleading**: `embedding_provider` / `embedding_model` (pinned
  at vectorstore-build time per `<env_dir>/out/vectorstore/config.json`
  and consumed by the web app from there, not the LLMConfigFile);
  `skip_taxonomy` / `skip_graph` (pipeline-runner stage toggles,
  not query-time). Saving them populates the DB and overlays the
  caches but query behavior won't change without a vectorstore
  rebuild. Flagged in STATUS.md to either move to a "Pipeline
  (rebuild required)" section with caveat help text or drop.
- **DB-key change `top_k` → `top_k_cap`** in c2dff4f (one commit
  after the Config page shipped) — old DB rows are silently ignored
  by the new resolver. No migration shipped because the field had
  only existed for one commit.
- **25 new tests** cover ConfigStore CRUD (string/bool/int/float
  round-trips, missing-key, upsert, get_module, get_all, delete,
  cross-instance persistence), `apply_to_caches` overlay, schema
  integrity, and end-to-end route smoke (GET /config renders both
  modes; POST persists).

**Related**: D-022 (per-env runtime directory; the DB lives at
`<env_dir>/state/config.db` by convention even though no default
is enforced), D-044 (unified LLM config; the chain this layer
extends), D-050 (Phase 3-config infrastructure;
`config/retrieval.json` is one of the JSON files this layer sits
above).

---

## D-054: Cline scaffold for on-prem teacher/student collaboration

**Date**: 2026-05-07
**Status**: Accepted
**Phase**: Development

**Context**
NORA processes proprietary US-MNO requirement documents that cannot
leave the on-prem network. Two debug-loop pain points emerged this
work-week:

1. The user couldn't show me corpus content (no copy-paste between
   on-prem and cloud machines) so I was designing parser rules and
   profile schemas blind, relying on the user to manually translate
   observations into compact reports each iteration. Slow and
   error-prone.
2. Existing on-prem AI partners (Cline) could see the corpus and
   were perfectly capable of running NORA's CLIs, profiling docs,
   running pipelines — but had no structured contract telling them
   what their role was vs the Teacher LLM's, what to leak vs not,
   and how to format outputs so the user could hand-type them
   into the Teacher LLM chat.

The user proposed: split responsibilities. On-prem AI (Cline) sees
the corpus, profiles, debugs, and produces compact redacted
reports. The Teacher LLM (intentionally generic — the
scaffold doesn't name a vendor) sees the full repo, designs and
codes. Code transfers via git; observations transfer via hand-typed
reports. Per-project scaffolding tells Cline what to do.

**Decision**
14-file scaffold under the NORA repo, structured as:

- **`.clinerules/` (always-on, ~7KB total)** — Cline's rule engine
  auto-loads everything in this directory:
  - `00-project.md` — what NORA is, where to read more
  - `01-role.md` — Cline as on-prem student, the cloud "Teacher LLM"
    as teacher, the standard loop diagram (playbook → redacted
    report → hand-typed → code via git → re-test)
  - `02-content-safety.md` — full redaction protocol with literal-
    string mapping at `<env_dir>/state/cline-mapping.json` (on-prem
    only; placeholders `<MNO{N}>` / `<PLAN{N}>` / `<REQID-{N}>`);
    forward redaction for outgoing reports, reverse substitution
    for incoming Teacher LLM responses; hard rules for what never
    leaves on-prem (verbatim prose, file paths under `<env_dir>/input/`,
    requirement-body content, table cell data)
  - `03-output-discipline.md` — hand-typeable reports (≤30 lines
    max), tabular over prose, fixed format per playbook, six
    standard report types (ORIENT / MAP / PROF / RULE / RPT /
    BUNDLE)
- **`cline-playbooks/` (invokable manually)** — 6 initial
  playbooks (orient / mapping / profile-corpus / debug-pipeline
  / derive-rule / share-back) + 3 bootstrap-related additions
  (annotation-schema / bootstrap / feedback-loop, captured
  separately as D-055).

Workflow loop:
```
   ┌── on-prem (Cline + corpus) ──┐               ┌── cloud (Teacher LLM) ──┐
   │  1. invoke playbook          │   manual      │  3. read report         │
   │  2. produce compact report   │   typing      │  4. design + code       │
   │  6. git pull                 │ ◀── git ────  │  5. commit              │
   │  7. run new code             │               │                         │
   │  8. produce next report      │ ───────────▶  │  9. respond             │
   └──────────────────────────────┘               └─────────────────────────┘
```

Steps 3 + 9 are user-typed manually. Code never moves through chat —
only through git.

**Why this over alternatives**
- *Everything in `.clinerules/` (single concatenated rules file)* —
  rejected. Cline concatenates every file in the directory into one
  always-on prompt; bundling 7 playbooks + 4 always-on rules would
  bloat every Cline interaction with playbook content not relevant
  to that conversation. Splitting playbooks into a manually-invoked
  directory keeps the always-on budget tight.
- *Have Cline write code under `core/src/`* — rejected. Two reasons:
  (a) Teacher LLM has the full design context; Cline doesn't need
  to and shouldn't second-guess architecture decisions; (b) bounding
  Cline's authority makes review easier — the user only reviews
  reports going out and Teacher LLM's commits going in, never an
  in-place Cline-edited core source file.
- *Generic "AI assistant" naming* — rejected (initially used "Claude"
  per the LLM provider in use at the time, but corrected per user
  preference to "Teacher LLM"). The vendor-neutral naming makes the
  scaffold portable (different team members may use different Teacher
  LLMs; the scaffold doesn't care).
- *Copy-paste between machines* — rejected by physics: the user's
  on-prem and cloud machines are air-gapped (no shared clipboard).
  Hand-typing budget drives every report to ≤30 lines, tabular,
  numerical-not-prose. Code transfer goes through git.
- *Always-default redaction mapping at `<env_dir>/state/`* —
  considered. Decided **opt-in** with an explicit `<env_dir>/state/
  cline-mapping.json` path. The mapping never enters git (env_dir
  is gitignored). Cline allocates new placeholders on demand;
  the user reviews periodically.

**Consequences**
- **Scaffold lives in NORA repo for v1.** When a second project
  with on-prem corpus needs the same pattern, lift to a portable
  `compact-cline-template/` (analogous to the COMPACT scaffold for
  Teacher LLM, which is the user-global `.claude/skills/`
  scaffold). For v1, NORA-specific paths/CLIs are hardcoded —
  faster to validate the design, cleaner to template after.
- **New on-prem-only file** at `<env_dir>/state/cline-mapping.json`.
  Per-env, never in git. Schema covers MNO short / MNO alias / MNO
  full name / Plan ID / Plan name / Release / Req ID. Stable
  indexes per category — once allocated, never changes. Cline
  emits a `MAPPING:` line inline whenever it allocates a new entry.
- **Hand-typing budget is the tightest constraint.** Every report
  type has a hard line limit. PROF ≤15. RULE ≤10. RPT ≤25. BUNDLE
  ≤40. Without these caps, the workflow doesn't scale.
- **Validation gap surfaced and tracked**: the loop is end-to-end
  unproven on a real corpus until the user runs orient → mapping →
  profile-corpus on a work-PC doc and reports back. Flagged in
  STATUS.md.
- **Annotation web UI for PDF/DOCX/XLSX deferred** as a separate
  Teacher-LLM task. Schema doesn't change when the UI lands;
  hand-typed JSON works in the interim.

**Related**: D-008 (web UI for non-CLI team members; the Cline
scaffold partners with that channel — Cline reads Parse Review
output, web UI hosts the human-review side), D-022 (per-env
runtime directory — the redaction mapping is one more file under
`<env_dir>/state/`), D-055 (bootstrap → feedback-loop pattern;
the Day-0 / Day-N rule-derivation flow that rides on this
scaffold). The scaffold itself is independent of any specific
ADR — D-054 stands alone as "how on-prem (Cline) and the Teacher LLM
collaborate in this project" and D-055 is the rule-derivation
pattern that runs on top of it.

---

## D-055: Bootstrap → feedback-loop pattern for human-in-the-loop rule derivation

**Date**: 2026-05-08
**Status**: Accepted
**Phase**: Development

**Context**
The cline scaffold (D-054) lets Cline derive parser/profile rules
from on-prem corpus content, but the v1 derivation path
(`derive-rule.md`) had Cline sample the corpus itself (10 instances
+ 10 NEAR-misses per element) and infer rules from those. That's
brittle: Cline's choice of "instances" can be unrepresentative;
self-reported coverage stats can be wrong because Cline scores its
own rule.

The user proposed a more grounded loop:

1. **Day 0** — humans annotate 3-5 corpus files marking regions of
   each kind (TOC / section_heading / strikethrough / etc).
   Annotations capture location + kind, not verbatim content.
2. **Day 0** — Cline reads the annotations, derives rules from
   the human-marked regions, emits a compact BOOTSTRAP report.
3. **Day 0** — Teacher LLM commits initial profile + parser code.
4. **Day N** — humans review parser output via the existing Parse
   Review web page, mark wrong rows / missed rows.
5. **Day N** — Cline reads the review-derived corrections (CSV from
   the web page) and emits a FEEDBACK report categorizing failure
   modes and proposing rule refinements.
6. **Day N** — Teacher LLM commits the refinement + an integration
   test that pins the failure mode it just fixed.
7. Loop steps 4-6 until coverage stabilizes.

**Decision**
Three new files in `cline-playbooks/`:

- **`annotation-schema.md`** (reference, ~225 lines) — JSON sidecar
  format per source doc at `<env_dir>/annotations/<plan>_annotations.json`.
  Supports 9 kinds: `section_heading`, `req_id`, `toc`,
  `strikethrough`, `version_history`, `definitions`, `applicability`,
  `priority`, `references` (with `intra_doc` / `cross_doc` / `spec`
  subkinds). Region format per doc-type: PDF (page+bbox or
  line_range), DOCX (paragraph indices or table+row), XLSX (sheet+rows).
  **Positive examples only** — false positives caught later by the
  feedback loop, not by negative annotations.
- **`bootstrap.md`** (invokable, ~120 lines) — reads annotations,
  groups by kind across docs, derives one rule per kind, emits
  BOOTSTRAP report (≤25 lines, one line per kind with regex/heuristic
  + sigma=annotation-count + TP).
- **`feedback-loop.md`** (invokable, ~110 lines) — reads
  `<env_dir>/reports/audit/<plan>_audit.csv` and per-req correction
  overrides, categorizes FPs/FNs by structural failure mode (max 3
  per kind), proposes rule refinement, emits FEEDBACK report (≤20
  lines, ≤3 kinds per report — split into multiple reports if more).

`derive-rule.md` (the pre-existing fallback playbook) is now
explicitly the **fallback** — for cases where annotations don't
exist yet AND the parser hasn't run yet. The README's
decision-diagram routes humans to bootstrap → feedback-loop when
annotations are available.

**Why this over alternatives**
- *Negative annotations (mark "this is NOT a TOC")* — rejected for
  v1. User feedback: "Difficult to provide negative examples for
  bootstrap annotations. However, human feedback later on actual
  parse output will catch FPs." Accept the FN-only signal at
  bootstrap; let the feedback loop catch FP rate after the parser
  has run on the full corpus.
- *Cline writes profiles directly* — rejected. Per D-054 invariant
  ("Cline doesn't write code under `core/src/`"). Cline emits the
  BOOTSTRAP/FEEDBACK reports; Teacher LLM commits the profile.json
  changes and any parser code. Stricter separation = easier review.
- *Annotation web UI for v1* — deferred. User selected option (b)
  ("Annotate page in NORA web UI") for the long term, but
  acknowledged the substantial scope (PDF.js for PDFs, IR-rendering
  for DOCX/XLSX). For v1, hand-typed JSON sidecars validate the
  schema and the loop end-to-end. Schema doesn't change when the
  UI lands.
- *Cline emits one report per kind* — rejected for bootstrap (one
  combined BOOTSTRAP report covering all annotated kinds is more
  efficient for the user's typing trip). Kept per-kind in feedback-
  loop because feedback usually focuses on 1-3 kinds at a time and
  per-kind detail helps Teacher LLM make targeted fixes.

**Consequences**
- **New on-prem-only directory** `<env_dir>/annotations/` for
  hand-typed JSON until the web UI lands. Per the ban on `<env_dir>`
  in git (D-022), these never enter the repo.
- **Reference subkinds become first-class.** The `references` kind
  with `intra_doc` / `cross_doc` / `spec` subkinds means the parser
  may need new code for cross-doc reference resolution and 3GPP
  spec citation handling. NORA's parser already has some of this
  (the resolve stage handles intra-doc + cross-doc xrefs); spec
  citations are partially captured but not as a first-class
  annotation kind. When the user runs bootstrap on a corpus and the
  BOOTSTRAP report names `references` as a kind to add coverage
  for, that becomes a Teacher-LLM commit.
- **Three new playbook files** — schema (reference), bootstrap
  (invokable), feedback-loop (invokable). `derive-rule.md` updated
  with a front-pointer noting the preferred path.
- **Bootstrap is positive-only** — accept that bootstrap rule rates
  are TP/sigma, not TP/FP/FN. The feedback loop is where FP rates
  get measured (against real parser output reviewed by humans).
- **Loop convergence is unproven on a real corpus.** Flagged in
  STATUS.md as "validate the cline scaffold end-to-end on the work
  PC" — orient → mapping → profile-corpus → bootstrap → run
  pipeline → feedback-loop. Iterate playbook formats based on what
  the work PC produces.
- **Annotations cap is empirical**: 3-5 docs, 5-10 examples per
  kind per doc. Smaller → low-confidence rule (BOOTSTRAP report
  flags `LOW_PROV: <kind>` when sigma < 3). Larger → tedious for
  the human. Tunable per project.

**Related**: D-008 (web UI for non-CLI team members; Parse Review
page is the feedback-loop input channel), D-027 (parser table-
anchored requirements; this loop is how new corpus types validate
that parser change generalizes), D-054 (cline scaffold; this is the
rule-derivation pattern that runs on top of it), D-038 (table-
anchored definitions extraction; same shape — corpus-derived rule
that could have come through bootstrap had the loop existed). The
loop is corpus-agnostic and stage-agnostic but currently exercised
mostly on `parse` and `profile`; could extend to `resolve` and
`eval` per the playbook table in `feedback-loop.md`.

---

## D-056: Build the annotation harness in NORA before exercising the Cline scaffold
**Status**: Active · **Date**: 2026-05-08
**Decision**: Ship a web-UI annotation editor (Bootstrap tab on the Parse page) that writes `<env_dir>/annotations/<plan>_annotations.json` per `cline-playbooks/annotation-schema.md`, before inviting the user to run Cline's `bootstrap.md` for the first time.
**Why**: Annotation quality is the bottleneck on whether bootstrap-derived rules generalize; hand-typing JSON for 3-5 docs × 5-10 examples × 9 kinds is tedious and typo-prone, gating every new corpus onboarding. Vs ad-hoc CLI: doesn't let the human visually align selections against IR + DOCX preview. Vs deferring to "after first dry run": the dry run depends on the artifact this UI produces, so deferring just postpones the same work.
**Consequences**: NORA web UI surface grows with one more page-tab. The DOCX renderer's index-alignment with `DOCXExtractor` becomes a load-bearing invariant (D-057). Annotation file format is now a contract between the UI and Cline's `bootstrap.md`; schema changes touch both. PDF/XLSX UIs still deferred; humans handwrite JSON for those formats until the next non-DOCX corpus arrives.

---

## D-057: Custom DOCX-to-HTML walker, index-aligned with DOCXExtractor
**Status**: Active · **Date**: 2026-05-08
**Decision**: `core/src/web/docx_html_render.py` walks `doc.element.body.iterchildren()` in the same order as `DOCXExtractor.extract` and applies the same skip rules (empty paragraphs return None; degenerate single-empty-column tables dropped). Every emitted HTML element carries `data-block-idx="N"` matching `ContentBlock.position.index`.
**Why**: The IR's flat block index is what annotations reference and what Cline's `bootstrap.md` consumes — the renderer must match it. Vs mammoth: doesn't know about IR alignment, would require a separate post-pass to drop empty `<p></p>` and re-number, which is exactly the custom walker minus the dependency. Vs pypandoc: requires the pandoc binary, breaking the offline-install path. Vs no preview (IR-only): user explicitly chose side-by-side because IR-only loses too much context for hand-annotation.
**Consequences**: Any change to `DOCXExtractor`'s iteration order or skip rules MUST mirror in `docx_html_render.py` or every saved annotation drifts on next render. Invariant added to `web/MODULE.md`. Test fixture (`test_parse_bootstrap.py::TestDocxRenderAlignment`) builds a DOCX in-memory and asserts indices match the extractor's output — the regression net. HTML output is functional, not pixel-faithful: no Word styles, no images rendered, no list bullets.

---

## D-058: Annotation region schema flattened to block_indices for PDF + DOCX
**Status**: Active · **Date**: 2026-05-08
**Decision**: PDF and DOCX annotation regions both use `region: {block_indices: [N, ...]}` (single block, range, or arbitrary set) with `region: {block_index: N, row_range: [start, end]}` for table-row precision. The earlier `paragraph_indices` (DOCX) / `page+bbox`/`page+line_range` (PDF) split is removed. XLSX retains sheet/cells/row_range (different IR shape).
**Why**: PDF and DOCX extractors emit the same flat `DocumentIR` with sequential `ContentBlock.position.index`. Using paragraph/table/page indices forced a translation step that could drift or miscount. Single shape simplifies the validator, the UI, and Cline's rule-derivation. Row-range survives via `block_index + row_range`. Vs keeping the per-format split: makes the UI carry two region shapes for what's the same underlying data; Cline reads the same JSON either way.
**Consequences**: `cline-playbooks/annotation-schema.md` updated; `bootstrap.md` and any future schema readers must consume the new shape (its prose doesn't reference old field names, so no edit needed today; flagged). No on-disk annotations existed under the old schema, so no migration. PDF UI when built inherits the same shape — no new region type needed. Loses the ability to express "this PDF page" as a first-class semantic; if needed later, can be re-added as an alternate region.

---

## D-059: Reference annotation taxonomy — 5 first-class kinds + optional target ground truth + reference_list pattern
**Status**: Active · **Date**: 2026-05-09
**Decision**: Annotation schema's reference handling reorganized into 5 top-level kinds: `reference_intra_doc` (same-doc), `reference_cross_doc` (other plan/MNO), `reference_spec` (public standard, with required `style` field: `direct` | `indirect`), `reference_list` (the bibliography section), `reference_list_entry` (individual numbered entry, optional ground truth). The single old `references` kind with a `subkind` field is gone. Each reference kind accepts an optional `target` dict with kind-specific allowed keys for resolver-eval ground truth — explicitly **ignored by Cline's rule derivation**. Indirect spec citations (`[5]`) flow through a two-step resolution path that mirrors the existing `definitions_map` pattern: parser builds `reference_list_map: dict[int, {spec, section?}]` from the bibliography section, resolver looks up bracketed numbers in that map at resolve time. New `core/src/profiler/ANNOTATIONS.md` is the human-annotator's guide covering all 13 kinds with examples for every variant.
**Why**: (a) Flat picker UX is clearer than nested-subkind for hand-annotation. (b) Direct vs indirect spec citations are structurally different (the first has the spec name inline, the second has only a number that requires lookup) — encoding `style` as a required field forces the annotator to make the distinction up-front, avoiding a downstream parser branch on heuristics. (c) Capturing target as ground truth is cheap (one optional dict), preserves a future resolver-eval path, but doesn't tax the bootstrap loop because Cline ignores it. (d) Reusing the `definitions` section-level + per-entry pattern for `reference_list` is consistent with how the parser already handles glossaries — the same code path generalizes. (e) ANNOTATIONS.md lives under `core/src/profiler/` (not parser, not web) because the profiler is the module that turns annotation patterns into rules; matches the precedent of `core/src/query/RETRIEVAL.md`.
**Consequences**: 5 new kinds in `bootstrap_schema.KINDS` (was 9, now 13). `bootstrap_schema.SPEC_REFERENCE_STYLES`, `REFERENCE_LIST_NUMBERING_STYLES`, `REFERENCE_LIST_LAYOUTS`, `TARGET_KEYS_BY_KIND` are new public constants. `REFERENCE_SUBKINDS` and `REFERENCE_TARGET_KINDS` removed. Validator now requires `style` for `reference_spec` (validation error if missing). UI's KIND_FIELDS / KIND_ORDER / kind picker / CSS color classes updated. `parse_bootstrap.js` collects `target.<sub>` form keys into a nested `target` dict on save, splits back to dot-keys for edit. `bootstrap.md` BOOTSTRAP report shape gains 5 reference-flavor lines (was 1 parent block with 3 nested children). Resolver-eval ground-truth path is now unblocked but unwired — a future task reads `target` from saved annotations and compares against resolver output.
- **Parser plumbing for `reference_list_map`** [landed 2026-05-09 via the first BOOTSTRAP from VZW <PLAN0_NAME>+OTADM corpus]: `DocumentProfile.reference_list_section_pattern` + `reference_list_entry_pattern` schema fields; `RequirementTree.reference_list_map: dict[int, {spec, title?, section?}]` + `reference_list_section_number`; `_extract_reference_list` mirrors `_extract_definitions` (body-text scan + table-anchored layout, first-occurrence wins). `ParseStats.refs_extracted` counter. Resolver consumer for indirect spec citations (`reference_spec` with `style=indirect`) still pending — lands when first corpus has indirect spec annotations.

---

## D-060: Unified strike model — partial-text strike via runs; mark, don't drop, at extract time
**Status**: Active · **Date**: 2026-05-09. Partially supersedes D-031 (the geometric strike-line detection in PDF stays; only the *consequence* changes from "drop at extract" to "mark at extract; drop at parse").
**Decision**: A single rule for every strike across every format: **the extractor marks; the parser/UI decide**. The IR carries per-run strike state on every text-bearing block — paragraphs, headings, table cells — so downstream consumers can drop fully struck blocks (cascade) AND drop only the struck spans within partially-struck blocks (keep the rest). Concretely: new `TextRun` dataclass with `text` + `struck`; new `ContentBlock.runs` (paragraphs / headings), `header_runs`, `row_runs` (tables). Helpers `live_text()`, `row_all_struck(i)`, `header_all_struck()`, `cell_live_text(r, c)` rebuild content with struck runs filtered out. `font_info.strikethrough` is now a derived signal — True iff every textful run is struck — used by the parser's existing FR-33 cascade for fully-struck blocks. PDF and XLSX extractors **no longer drop** struck rows from the IR (was D-031 behavior). DOCX gets first-class partial-text strike across paragraphs, headings, and table cells.
**Why**: Three prior policies had three behaviors — paragraphs were mark-and-keep (parser decided), PDF/XLSX table rows were drop-at-extract (gone forever, no audit trail), DOCX table rows weren't detected at all. The drop-at-extract path destroyed information the user can never recover, even when wrong. Auditability + false-positive recovery + parser-time policy choice (`profile.ignore_strikeout`) all argue for "extractor neutral; parser drops; UI shows everything." Partial-text strike specifically is the user's primary need — within a sentence, a heading, or a table cell, only some characters may be struck (typo correction, requirement amendment). Whole-block strike loses too much. Vs leaving DOCX-tables as the only gap: that breaks parity and makes mental-model load high. Vs keeping the drop-at-extract path: blocks audit and recovery; the IR becomes lossy.
**Consequences**:
- IR schema: `TextRun`, `ContentBlock.runs`, `header_runs`, `row_runs`. Backward-compatible — empty defaults; `live_text()` falls back to `text` + `font_info.strikethrough` for legacy IRs.
- DOCX extractor: walks `paragraph.runs` and `cell.paragraphs[*].runs`, populates per-run strike state. Whole-block `font_info.strikethrough` is computed (every textful run struck) — replaces D-031's "any-run-struck" coarse heuristic.
- PDF extractor: keeps every row in `ContentBlock.rows`; populates `row_runs[i]` with single-run cells whose `struck` flag comes from existing geometric `_detect_struck_rows`. Whole-table `font_info.strikethrough` from `_table_is_struck` plus the all-rows-struck shortcut. Per-character partial-strike on PDF (would require per-span line testing) deferred to a future ADR.
- XLSX extractor: keeps every row; populates `row_runs[i]` from `cell.font.strike`. `header_runs` similarly. Whole-table strike on header-struck or all-rows-struck; preserves the prior "header struck → table dropped" semantic via `font_info.strikethrough`.
- Parser: post-cascade, normalizes `block.text` → `block.live_text()` and `block.rows` → drops fully-struck rows + per-cell live text. Mines req_ids from struck spans into `struck_req_ids` so they don't surface via table-anchored extraction. Cascade behavior on fully-struck section headings unchanged.
- UI: span-level strike rendering in IR pane (Bootstrap tab + Parse Review tab) and DOCX preview pane. Whole-row / whole-table strike applied at the `<tr>` / `<table>` level.
- Migration: existing PDF corpora were extracted with rows dropped. Re-extract under the new logic recovers those rows in the IR. Parser's parsed-tree output is unchanged for end-users (struck rows still drop at parse time). No data migration; just re-run extract.
- Tests: existing strike-drop assertions for PDF / XLSX inverted to assert "row kept; row_runs marks struck". New `test_strike_runs.py` covers model helpers, DOCX run population, and parser partial-strike normalization.
- D-031 remains the canonical entry for PDF strike-line geometric detection (`_table_is_struck`, `_detect_struck_rows`). D-060 changes only what's done with the detection result.

---

## D-061: User-driven content removal via `remove` annotations — rides on D-060 strike rails
**Status**: Active · **Date**: 2026-05-09. Builds on D-060.
**Decision**: New annotation kind `remove` — explicit human intent to exclude content from downstream pipeline (e.g., "skip the test-plan-mapping section until the test plans are ingested"). The Bootstrap UI captures regions to exclude (`block_indices` for whole blocks; `block_index + row_range` for table rows); a new pre-parse pass (`core/src/parser/user_annotations.py::apply_user_annotations`) reads `<env_dir>/annotations/<doc_id>_annotations.json` and **mutates the IR by setting strike marks on the listed regions** (every textful `TextRun.struck=True`; `font_info.strikethrough=True` on the block). The parser's existing FR-33 cascade then drops the content uniformly — section-level cascade fires automatically when the marked block is a heading. Pipeline parse stage (`core/src/pipeline/stages.py`) calls the helper before `parser.parse(ir)` for each doc and accumulates a `user_removes` count for the compact RPT. Companion fix: `_heading_depth` now also returns `block.level` for `BlockType.HEADING` blocks (DOCX-style headings) — the cascade was a latent bug for genuinely-struck DOCX headings, also surfaced by remove-on-DOCX-heading.
**Why**: A general-purpose "exclude this from ingestion" knob is needed independently of source-document strike marks. Examples: a section refers to plans not yet ingested (broken downstream refs); a table is known to be incorrect or duplicated; a heading + content needs deferral while triage finishes. Vs a separate "exclusion list" YAML or DB table: every exclusion file would need its own rendering, validator, and parser hook — three more moving parts. By riding on the strike rails the user already mastered (D-060), every exclusion gets the same auto-cascade, partial-text safety, and visual rendering for free. Vs a soft "ignore" hint on annotations: would create two parallel drop paths (strike vs ignore) with subtly different cascade semantics; one rail is simpler. The drop is reversible at parse time — delete the annotation, re-run parse; nothing is lost from the IR (matches D-060's "extractor is neutral; parser drops; UI shows everything"). Vs editing the source document: out of scope (the source is authoritative; NORA shouldn't mutate it).
**Consequences**:
- New `remove` kind in `bootstrap_schema.KINDS` (was 13, now 14). No required fields; supports both region shapes; reuses the existing `notes` field for the human reason (≤30 chars).
- New module `core/src/parser/user_annotations.py` — `apply_user_annotations(ir, path) -> int`. Idempotent and side-effect-free for missing/malformed files (logs warning, returns 0).
- `core/src/pipeline/stages.py` parse stage applies user annotations before `parser.parse(ir)` for every doc. Adds `user_removes` to stats dict. No CLI flag needed — annotation file presence is the trigger.
- `_heading_depth` honors `BlockType.HEADING` + `block.level` (DOCX); fixes a latent bug for struck DOCX headings AND enables remove-with-cascade on DOCX. Tests cover both paths.
- UI: `remove` in kind picker under a "User overrides" subheading; CSS color (red — delete intent); rendered with the existing `docx-struck` styling so the visual signal is consistent with strikethrough.
- Annotations file is now load-bearing for the pipeline (parse stage reads it). Pre-D-061 it was purely a Cline-bootstrap input. Schema-version field on the file (`version: 1`) gates forward compatibility — the loader silently ignores unknown future versions, so older code reading newer files won't crash.
- ANNOTATIONS.md gains a `remove` section documenting when to use it and example payloads (whole section, paragraph, table-row range).
- Tests: `test_user_annotations.py` (apply mechanics + parser-after-apply cascade); `test_strike_runs.py` (DOCX HEADING cascade fix); `test_parse_bootstrap.py` (schema validation).
- Reversibility: delete the annotation → next parse run treats the content as live again. The IR is never modified on disk by the apply step (mutation is in-memory during parse).

---

## D-062: Placeholdered profiles + per-bootstrap mappings + runtime substitution
**Status**: Active · **Date**: 2026-05-09. Replaces the leaky `<mno>_<plan>_profile.json` naming convention introduced under D-059's first commit (which exposed proprietary plan names in the public mirror).
**Decision**: Profiles for proprietary corpora carry **redaction placeholders** in their regex strings (e.g., `<MNO0>_REQ_<PLAN>_\d+`) and are filed by **opaque bootstrap IDs** — `customizations/profiles/bs_<8 hex chars>.json` — with no MNO / plan / release info in filename or content. The placeholder→real-value mapping lives in `customizations/mappings/<bootstrap_id>.json`. At parse time, `core/src/profiler/profile_substitute.py::load_substituted_profile()` reads the profile, finds the matching mapping (snapshot first, then `<env_dir>/state/cline-mapping.json` fallback), and walks every regex-string field substituting specific placeholders (`<MNO0>` → `re.escape("VZ")`) and generic placeholders (`<PLAN>` → `[A-Z0-9_]+` regex char class). **The trust boundary keeping mappings off the public mirror is the work-PC `pre-push` hook installed by `~/work/utils/git-sync/sync-work.sh`** — it blocks any `git push` whose remote URL contains `github.com` (override: `NORA_ALLOW_PUBLIC_PUSH=1`). The mappings directory is **NOT** gitignored — that lets team members share one canonical mapping via the company-internal git remote. The hook (not `.gitignore`) is what enforces the public-mirror exclusion.
**Why**: D-059's first commit shipped `bs_d7a2c81f.json` to public github, which leaked: (a) the MNO short prefix in the filename, (b) plan names in `created_from`, `_provenance.notes`, and sample IDs, (c) the existence of those specific proprietary plans. Even if mapping snapshots stay private, a profile filename and content that name the corpus is enough to identify the customer. Vs continuing the per-MNO naming + carefully redacting content: subtle and prone to slipping. Vs encrypting profiles: adds a key-management surface and breaks every `git diff` workflow. Vs treating *all* profiles as private: the existing `vzw_oa_profile.json` is for a publicly-distributable corpus and is intentionally public; we want both modes to coexist. Bootstrap IDs are opaque hex (no date / no semantic content) — easier to reason about across multiple bootstraps without revealing chronology. Runtime substitution rather than pre-substitution-at-commit-time keeps the public artifact static and reproducible regardless of which work PC consumes it.
**Consequences**:
- New module `core/src/profiler/profile_substitute.py` — `substitute_placeholders(profile, mapping)`, `load_substituted_profile(profile_path, env_dir=None)`, `find_mapping_file(...)`, `_normalize_mapping(...)` to handle both Cline's live forward-redaction shape and the snapshot reverse shape.
- `core/src/pipeline/stages.py` parse-stage swaps `DocumentProfile.load_json` → `load_substituted_profile`. Profiles without a matching mapping load unchanged (covers the public corpus case `vzw_oa_profile.json`).
- `customizations/mappings/` added with `.gitkeep` + `README.md`. **NOT gitignored** — committed and pushed to company-internal git so the team shares one canonical mapping. The work-PC pre-push hook (installed by `sync-work.sh`) is the boundary that keeps the directory off the public mirror.
- Pre-push hook installed by `sync-work.sh` on every sync (idempotent). Defends against `git push origin` calls bypassing the sync script. Override `NORA_ALLOW_PUBLIC_PUSH=1` is for auditable history-rewrite force-pushes.
- `cline-playbooks/bootstrap.md` updated: new Step 1 (bootstrap_id read-or-generate), new Step 8 (mapping snapshot write), report header gains `bootstrap_id: bs_<id>` line. Derived regex strings emit placeholders directly.
- `cline-playbooks/mapping.md` extended: dual-mapping table (live vs snapshot), shape distinction (forward-redaction vs reverse), trust-boundary note (pre-push hook, not gitignore).
- `.clinerules/02-content-safety.md` extended: mapping snapshot location, trust-boundary note (pre-push hook).
- Existing leaky profile (`customizations/profiles/bs_d7a2c81f.json` introduced in commit `2f918a6`) deleted in this change. Forward fix done; **history rewrite (separate operation, requires force-push)** removes the file from history along with residual proprietary string mentions in commit messages.
- New tests in `test_profile_substitute.py` cover: specific + generic substitution; mapping shape normalization (both directions); fallback chain (snapshot → env_dir/state → no-op); load_substituted_profile end-to-end.
- Generic placeholder defaults: `<MNO>` → `[A-Z]{2,4}` (typical 2-4 letter MNO codes — VZ / TM / ATT). Future MNOs with longer codes need a profile-side override or a default broadening; flagged as a future limitation when seen.

## D-063: Generic-rules pivot — profile-driven DOCX parsing replaces per-corpus bootstrap annotation
**Status**: Active · **Date**: 2026-05-10. Pivots away from D-054/D-055/D-056 — user reviewed work-PC IR JSON samples (toc / sections / struck / vershist / glossary) and concluded structural patterns are general enough to encode as profile rules without manual annotation per corpus.
**Decision**: 5-phase parser overhaul. (Phase 1) Profile schema deltas: `TocDetection` class; `RequirementIdPattern.anchor: "last_run"|"trailing_text"|"leading_text"` + `.normalize: "upper"|"none"`; rename `revision_history_heading_pattern` → `revision_history_label_pattern` (with `_from_dict` migration); `definitions_table_term_column` / `_definition_column`; `embed_glossary`. (Phase 2) Runs-over-text invariant for value extraction; `_heading_req_id()` dispatches on anchor mode. (Phase 3) Style-driven TOC pre-pass + `_toc_lookup()` pair-by-req_id-or-title + `docx_styles` heading classification; `toc_pair_misses` counter + `parser.format_error: kind=toc_pair_miss` WARN. (Phase 4) Front-matter cutoff = `max(toc_end, revhist_end)`, gated on `toc_detection.style_pattern` so OA-style numbering corpora keep their inline-only revhist consume (revhist sits inside chapter 1 in OA — applying the cutoff there would drop chapter 1's heading). (Phase 5) Glossary table-form support + `embed_glossary=False` drops glossary subtree from RAG/KG while preserving `definitions_map` for body-chunk acronym expansion.
**Why**: Manual annotation per corpus didn't scale — user wanted to onboard a 135-doc DOCX corpus without typing rules document-by-document. Real DOCX corpora share more structure than OA-style PDFs: paragraph styles encode heading depth (`Heading N`), TOC is auto-generated by Word with `toc N` styles AND embedded section numbers, runs separate title from trailing req_id, glossary tables follow a canonical 2-column shape. All of these are stable enough to profile-drive. The bootstrap loop (D-054/D-055/D-056) is preserved as the escape hatch for unusual corpora — profile defaults can disable the new path per-corpus (`heading_detection.method` switch, empty `toc_detection.style_pattern`). Vs (a) keep manual-annotation only: doesn't scale; (b) global heuristic switching: fragile; (c) chosen: profile-driven opt-in per-corpus.
**Consequences**:
- ~880 LOC across `core/src/parser/structural_parser.py`, `profile_schema.py`, `profile_substitute.py`, `chunk_builder.py`, `parse_log.py`, `parse_review.py`; ~880 LOC of tests (+52 tests: test_last_run_req_id.py, test_toc_pairing.py, test_glossary_skip_from_rag.py + extensions).
- New WARN namespace `parser.format_error` for graceful-recovery cases (`empty_runs_heading`, `concatenated_run_heading`, `toc_pair_miss`) — surfaces source-doc formatting errors without failing the parse.
- Work-PC corpus validated end-to-end: `reqs=13372 defs=1410 toc=19209 frontmatter=998 toc_pair_misses=5` (all 5 are real human source-doc errors).
- Profile knobs: a non-DOCX corpus or one that doesn't fit can disable the new path via empty `toc_detection.style_pattern` (cutoff disabled) or `heading_detection.method = "numbering"` (classification falls back to OA-style numbering pattern).
- BlockType.HEADING routing fixed in body pass (was PARAGRAPH-only); same fix applied to revhist consume break. Documented as part of the pivot.

## D-064: Skip-resolve + skip-standards — independent pipeline stage skips with one-way cascade
**Status**: Active · **Date**: 2026-05-10.
**Decision**: New first-class skip flags via the 3-tier config chain (CLI > env var > `config/llm.json` > per-env > default `False`). `--skip-resolve` / `--skip-standards` CLI flags; `NORA_SKIP_RESOLVE` / `NORA_SKIP_STANDARDS` env vars; `LLMConfigFile.skip_resolve` / `.skip_standards`; `EnvironmentConfig` overrides. `skip_resolve` implies `skip_standards` (one-way cascade — the standards stage reads resolve's manifest_dir as input). The reverse is not enforced — explicit `--skip-standards` alone doesn't force `skip_resolve`.
**Why**: Matches the existing `skip_taxonomy` / `skip_graph` shape (D-044), so users get a uniform mental model. Enables fast iteration cycles during parser development (skip the slow standards download) and offline operation (no HF / 3GPP network). Downstream tolerance was verified: `graph._load_manifests` returns empty on missing dir; `_load_reference_index` already handled missing file (no graph regression).
**Consequences**:
- 4 new symbols in `core/src/env/config.py` (env var consts + `resolve_skip_resolve` / `resolve_skip_standards`).
- `EnvironmentConfig` per-env override fields.
- `run_cli.py` adds 2 argparse flags + cascade logic + stage-filter note.
- 2 new precedence tests (`test_resolve_skip_resolve_3tier`, `test_resolve_skip_standards_3tier`).
- `--skip-resolve --skip-standards --skip-taxonomy --skip-graph` is now the canonical "extract → parse → vectorstore only" fast-iteration combination.

## D-065: Cross-encoder reranker plumbed into production query path via 3-tier + Config-page DB knobs
**Status**: Active · **Date**: 2026-05-10.
**Decision**: Two new knobs, both flowing through the unified resolver chain (env var > Config-page DB > `config/llm.json` > per-env > default):
- `reranker_enabled: bool = False` — when True, `_get_or_build_pipeline` in `web/routes/query.py` constructs a `CrossEncoderReranker` and plumbs it into `QueryPipeline`. False (default) → `MockReranker` passthrough = previous production behavior.
- `reranker_model: str = ""` — empty falls back to `DEFAULT_RERANKER_MODEL` (`cross-encoder/ms-marco-MiniLM-L6-v2`). Accepts a HuggingFace model id OR a local filesystem path. Local paths sidestep the online HF download when the host is firewalled.

Per-query-type gating (`_TYPE_RERANK_ENABLED`) still applies after the reranker is attached — FACT / CROSS_DOC / FEATURE_LEVEL / STANDARDS_COMPARISON / CROSS_MNO_COMPARISON / TRACEABILITY / RELEASE_DIFF rerank; SUMMARIZE / SINGLE_DOC / GENERAL passthrough.
**Why**: Until today, the production query pipeline always ran with MockReranker because `_get_or_build_pipeline` never constructed one — the cross-encoder existed only in the eval stage. User wanted to test BGE rerankers locally on a firewalled work PC; the existing code couldn't accept either an off-default model or a local-filesystem path. Default-False preserves current eval baselines (the 2026-05-08 A11 result showed MiniLM was net-zero on telecom queries; we don't want to silently flip retrieval behavior). Local path support enables the firewalled-host workflow without code changes — pre-download with `huggingface-cli download <id> --local-dir <path>`, point the knob at the path.
**Consequences**:
- 2 new resolver functions (`resolve_reranker_enabled`, `resolve_reranker_model`); 2 new env vars; 2 new LLMConfigFile + EnvironmentConfig fields; 2 new ConfigField entries on the Config page.
- New `_resolve_reranker()` helper in `web/routes/query.py` — falls back to MockReranker silently on init failure (so a missing local cache doesn't crash the request).
- `DEFAULT_RERANKER_MODEL` constant. Suggested telecom-friendly upgrade: `BAAI/bge-reranker-base`.
- 2 new precedence tests.
- Once enabled with a good model, may shift retrieval rankings — eval baselines need a refresh comparison.

## D-066: Generic placeholders always substitute, regardless of mapping presence
**Status**: Active · **Date**: 2026-05-10. Fixes a behavior inconsistency in D-062's `load_substituted_profile`.
**Decision**: `load_substituted_profile` calls `substitute_placeholders(profile, mapping or {})` unconditionally. Previously, when no mapping snapshot was found, it returned the profile *without* calling `substitute_placeholders` at all — generic placeholders (`<PLAN>` / `<DIGITS>` / `<MNO>` / `<REL>`) stayed as literal text in compiled regexes despite the module docstring describing them as mapping-independent.
**Why**: The bug surfaced on the work PC when the user hand-edited `<MNO0>` → `VZ` in their profile JSON as a workaround for an unrelated `find_mapping_file` lookup issue. The substituted pattern became `VZ_REQ_<PLAN>_\d+` — literal `<PLAN>` substring, matching zero req_ids — and the chunk builder dropped all 13,425 requirements (chunks=0). The early-return was an oversight, not an intentional design. The module docstring already documented generic placeholders as "mapping-independent — substitute even when no mapping snapshot is found." Now the implementation matches the stated semantics.
**Consequences**:
- A pre-existing test (`test_no_mapping_returns_profile_unchanged`) had asserted the wrong direction (codified the bug). Updated to `test_no_mapping_still_substitutes_generic_placeholders`.
- New regression test (`test_workaround_user_replaced_specific_in_profile`) mirrors the user's hand-edited-profile shape.
- Public corpora (`vzw_oa_profile.json`) have no placeholders, so substitution is a no-op — zero behavior change for them.
- Specific placeholders without mapping entries still emit WARN log (existing behavior in `substitute_placeholders`) — caller can spot the un-substituted `<MNO0>` etc. and either provide a mapping or hand-edit.

## D-067: Per-chunk retry-with-shrink + skip-on-failure for Ollama 5xx, instead of failing the stage
**Status**: Active · **Date**: 2026-05-10.
**Decision**: `OllamaEmbedder` now retries HTTP 5xx responses with the text halved, up to `_MAX_SHRINK_RETRIES` (=2) attempts. After exhausted retries, raises a new `ChunkEmbeddingError(idx, preview, attempts, last_error)`. `VectorStoreBuilder._embed_batched` catches this per batch, falls back to one-at-a-time embedding for that batch (so the rest still embed), and records failed indices. The `build()` caller filters failed indices from ids/texts/metadatas before `store.add()` so the vector store stays internally consistent. 4xx responses propagate unchanged (no point retrying a bad model name / malformed request). Non-HTTP errors (DNS, refused, timeout) also propagate — those signal a server-level problem the caller must handle.
**Why**: A specific token-dense chunk from one plan kept failing at the 8000-char truncation cap with HTTP 500 — likely an Ollama-side token-budget overrun for unusually dense content. The previous behavior raised on first per-chunk failure → ~5 minutes of embedding work discarded per attempt. Vs (a) reducing the default `max_input_chars`: blanket regression for all chunks; (b) token-aware truncation (count true tokens via a tokenizer): bigger change, similar gain; (c) chosen: surgical retry-with-shrink, then skip. A handful of skipped chunks lose retrieval coverage but the rest of the pipeline finishes; the skipped chunk_ids are logged (capped at 10 per log line) for architect audit.
**Consequences**:
- New `ChunkEmbeddingError` exception class in `embedding_ollama.py` — caught specifically by builder; other RuntimeError subclasses still abort.
- `_embed_batched` return type changes from `list[list[float]]` to `tuple[list[list[float]], list[int]]` (skipped indices alongside the embeddings).
- Skipped chunks have no retrieval representation. If a query needs them, the query simply doesn't return them — no fabrication. WARN log surfaces the gap.
- 3 new tests (retry-success / exhausted-retries / 4xx-no-retry).
- A follow-up item (Next section: "Token-dense chunks skipped at embed time in one specific plan") tracks the longer-term fix (token-aware truncation OR per-row chunking for table-heavy reqs).

## D-068: Profile_miner — LLM-assisted regex mining as a NORA CLI, not a Cline playbook
**Status**: Active · **Date**: 2026-05-13.
**Decision**: Closes the human-in-the-loop loop for parser-detection misses. New `core/src/profile_miner/` module with two CLIs: `profile_miner_cli` reads `<env_dir>/corrections/*_corrections.json` (written by the Web-UI Review tab), joins each entry to its IR block via `block_idx` + ±2 neighbours, redacts proprietary tokens, clusters by `(expected_reason, block_type)`, asks an LLM (via the existing `LLMProvider` protocol + D-044 resolver chain — no provider-specific code) to propose one regex per cluster, and writes a structured `<env_dir>/reports/profile_patch_<doc>.json` per document. `apply_profile_patch_cli` merges those patches into `<env_dir>/corrections/profile.json` via alternation-OR for scalar fields and append-if-absent for list fields. The miner never writes profiles directly — reviewers approve patches before they go in.
**Why**: The corrections workflow needed a way to translate "the parser missed this revhist heading" into a profile regex change without per-MNO code changes (D-003). Options: (a) Cline playbook that drives a local LLM — chosen for the earlier `bootstrap.md` workflow but proved heavy for this narrower task; humans typed and pasted at every step. (b) Hardcoded heuristics — violates D-003 and rules out novel landmark forms. (c) **Chosen**: a small NORA CLI that batches the corrections, redacts proprietary tokens, and surfaces a structured patch the human reviews. Uses the same `LLMProvider` protocol as the rest of the pipeline so the work-PC Ollama deployment + dev-PC cloud-LLM both work unchanged. Keeps the LLM strictly inside `core/src/profile_miner/`; the parser remains LLM-free per the existing invariant.
**Consequences**:
- New module + two CLIs in the public surface; new `MODULE.md` curated contract.
- New persistent file shapes: `<env_dir>/reports/profile_patch_<doc>.json` (the patch) and `<env_dir>/corrections/profile.json` is now both human- and AI-touched.
- Cline `bootstrap.md` path stays viable for other rule classes (taxonomy, applicability) but for regex corrections it's superseded — `validate-rule.md` / `changelog.md` Cline playbooks marked as "may still apply for non-regex rules" in STATUS.
- The corrections-driven feedback loop now has tooling end-to-end: Review tab → save → mine → apply → re-parse → verify in Summary tab.
- 24 new tests across `test_profile_miner.py` + `test_apply_profile_patch.py`.

## D-069: New profile fields `revhist_table_header_pattern` + `definitions_table_header_pattern` — detect bare landmark tables with no introducing heading
**Status**: Active · **Date**: 2026-05-13.
**Decision**: Two new opt-in profile fields, both default empty (disabled). `revhist_table_header_pattern` lives at top level alongside `revision_history_label_pattern`; `definitions_table_header_pattern` lives nested under `heading_detection` alongside `definitions_section_pattern`. Both are matched against `" | ".join(h.strip() for h in table.headers)`. When a TABLE block's joined headers match, the parser drops the table (revhist case — and arms the same consume-until-next-paragraph state the label path uses) or marks it as the definitions section (glossary case — the table feeds `definitions_map` extraction).
**Why**: Real-world DOCX corpora ship revhist/glossary tables with no introducing heading paragraph — a bare table at the top of the doc, or a glossary table inside a generic section. Options: (a) detect by table-shape in code (heuristic: ≥3 cols, first col looks like version number, etc.) — rejected for violating D-003 (per-MNO behavior in code). (b) Extend `revision_history_label_pattern` to match the table's joined-header string when no paragraph precedes — rejected because it overloads one regex with two semantically different matching surfaces (paragraph text vs. joined headers) and makes profile authoring confusing. (c) **Chosen**: separate, explicit fields. Each has one job, the profile-author intent is unambiguous, and the miner can route table-shaped corrections to them without ambiguity.
**Consequences**:
- Schema additions cascade through `profile_schema.py` load/save paths.
- Parser gains two new tests of `_revhist_table_header_re` (body pass + pre-pass for the front-matter cutoff) and a table-header fallback branch inside `_extract_definitions` when no section title matched.
- `parse_summary.json` evidence-collection still credits "matched via section title" — table-header fallback isn't distinguished yet (could be added if reviewers find the ambiguity confusing).
- Miner reason-to-field map keyed by `(reason, is_table_block)`; routing per block-type is now a first-class concept in the miner.
- 7 new tests in `test_parse_table_header_fallback.py`.

## D-070: Corrections-file shape — `block_idx` + `block_text_preview` carried in every entry; restoration drops the page-only fallback
**Status**: Active · **Date**: 2026-05-13.
**Decision**: Every entry in `<env_dir>/corrections/<doc>_corrections.json` (both `missed_drops[]` and `false_positive_drops[]`) now carries `block_idx` (int — index into `<doc>_ir.json` `content_blocks[]`) and `block_text_preview` (string — first ~120 chars of block prose with badges/markup stripped) in addition to the existing `pages` / `expected_reason` / `comment` fields. The Review tab's restore-on-reload resolves saved corrections to DOM blocks via `block_idx` only — the previous page-number fallback (return the first block on the page) has been removed because it silently placed badges on the wrong block (typically FRONT_MATTER on page 1). Entries without `block_idx` are skipped with a console warning; the user re-saves to heal.
**Why**: The original schema keyed entries by page number alone. Multiple corrections on the same page collapsed onto whichever block was first on that page → wrong block annotated. The Phase 3 regex-miner also needed unambiguous IR lookup to pull the actual block text + neighbours for the LLM prompt; page alone was too coarse. Options: (a) Keep page-only and add tiebreakers (offset within page, first-N-chars hash) — fragile; offsets shift as the IR regenerates. (b) Use bbox — PDFs only; doesn't translate to DOCX. (c) **Chosen**: the IR block index. Stable across re-parses as long as extraction doesn't change (which is the same dependency every other parser stage already has). `block_text_preview` is added alongside for human-readability of the corrections file on its own (the miner re-fetches full block text from the IR; the preview is reviewer-facing only).
**Consequences**:
- Corrections files saved before this commit lack `block_idx`; the Review tab refuses to restore them (with a console-warning saying "Re-save the review to heal"). User-impact: re-save once per legacy corrections file.
- The miner's `loader.load_corrections()` is `block_idx`-first; legacy files (without `block_idx`) are skipped, not silently mis-joined.
- The Phase 3 regex-miner can ask the LLM to generalize from the actual block content; without `block_idx` this would be impossible.
- The corrections-file shape is now a public surface contract; future Web-UI edits must preserve both fields.

## D-071: Miner safety-net regex composition — `(?i)(?:<llm_body>|<re.escape(example)>|...)` guarantees the proposed regex matches its own examples
**Status**: Active · **Date**: 2026-05-13.
**Decision**: Every regex the profile_miner emits is composed as `(?i)(?:<llm_body>|<re.escape(ex1)>|<re.escape(ex2)>|...)` where `<llm_body>` is the LLM's proposed regex (with any leading `(?i)` inline flag stripped) and each `<re.escape(ex_i)>` is the literal-escaped version of an example that the LLM regex *didn't* match on test-compile. When the LLM regex covers every example, no literal fallbacks are added (compact `(?i)<body>` form). When the LLM regex doesn't compile, the safety net falls back to a literal-only pattern made of every example. One outer `(?i)` is applied to avoid Python 3.11+ mid-pattern inline-flag warnings.
**Why**: Observed regression — a user corrected 3 docs whose revhist tables had column headers `Rev. | Author | Description of Changes | Date` (one doc) and `Rev | Author | Description | Date` (the other two). The LLM emitted `Rev\.` (literal dot required) for all three, matching only the first doc. The merged regex was *deader* than the inputs that produced it. LLM output is not a reliable contract on its own. Options: (a) Trust the LLM, validate manually — what we had; fails silently. (b) Force the LLM to retry until its regex matches every example — adds LLM round-trips, still no formal guarantee. (c) **Chosen**: belt-and-suspenders. The LLM contributes generalisation; literal `re.escape` contributes a guaranteed match for the actual examples the human marked. Bloat is bounded (~80 chars per example for table headers); only fires when the LLM actually missed an example.
**Consequences**:
- The proposed_pattern field in `profile_patch_<doc>.json` is structurally more complex (always wrapped in `(?i)(?:...)`).
- Reviewers reading the patch see a longer regex; rationale field still carries the LLM's plain-English justification, so the "why" stays surfaced.
- 3 new tests cover the three branches: LLM covers everything (no-op safety net), LLM misses one example (literal added), LLM regex uncompilable (literal-only fallback).
- Sets a quality-floor pattern that could be lifted to other LLM-emit-regex paths in the codebase if any appear.

## D-072: Extractor surfaces merged-cell metadata on DOCX TABLE blocks
**Status**: Active · **Date**: 2026-05-14.
**Decision**: New `MergedCell` dataclass (`row`, `col`, `rowspan`, `colspan`, `text`) and `ContentBlock.merged_cells: list[MergedCell]` field. The DOCX extractor detects merges via `CT_Tc` element identity in python-docx's `row.cells` iteration (a merge anchor's `_tc` element is returned at every grid position it occupies). One `MergedCell` per anchor; continuation positions in the rectangular `headers`/`rows` matrices now hold `""` instead of the duplicated anchor text. Empty for non-table blocks, for extractors that don't surface merges (PDF, XLSX), and for tables without merges.
**Why**: python-docx's `row.cells` API returns the *same* Cell object for every grid position inside a merged region. The previous extractor wrote the merge anchor's text at each of those positions, so a 4-column header row with a 4-wide merge produced `headers = ["Revision History"] * 4` and lost the merge structure entirely. Detection logic looking for "label-inside-merged-cell" layouts (revhist tables shipped with a merged top row labelled "Revision History", glossary tables shipped with a merged bottom row labelled "Acronyms") couldn't distinguish "one merged label" from "four identical cell values". Options considered: (a) keep duplicated text and hope downstream detectors recognise four-identical-cells as a merge signal — fragile, false-positive prone; (b) drop continuation cells entirely and shrink the matrix — breaks column alignment with non-merged tables; (c) **chosen**: preserve the rectangular matrix shape (continuation positions are `""`) AND surface structured `merged_cells` metadata for any code that needs the merge structure. The matrix stays consumable by code that doesn't care; merge-aware code reads `merged_cells`.
**Consequences**:
- `ContentBlock` schema extension. Persistent IR shape change — every IR JSON now has a `merged_cells` array on TABLE blocks (empty in most cases).
- `DocumentIR.load_json` deserialises the field. Existing IR JSONs without the field deserialise with `merged_cells=[]` (back-compat preserved).
- Behavioural change for the DOCX extractor: continuation positions in `headers`/`rows` are `""` now (was duplicated anchor text). Downstream consumers that read these matrices and expect duplicates (none discovered in audit) would need updates.
- Pre-`9ee028c` IRs need re-extraction to populate `merged_cells`. Code paths that use it (revhist score path's vocab signal) gracefully no-op on legacy IRs.
- Same pattern available for PDF and XLSX extractors when they're extended to detect cell merges — schema is format-agnostic.

## D-073: Signal-based revhist table detection (`RevhistDetection` profile field)
**Status**: Active · **Date**: 2026-05-14.
**Decision**: New `RevhistDetection` profile dataclass with `enabled` toggle plus three orthogonal scoring signals — **position** (`block_index / total_blocks <= max_position_fraction` → 1.0 else 0.0), **vocabulary** (case-insensitive set-based token count across joined headers + merged-cell anchor text + every body-row cell), and **cell fingerprint** (count of columns where ≥`cell_min_match_fraction` of body cells match a version/date regex). Each signal normalises to `[0, 1]`; the weighted sum is compared against `threshold` (default 0.55). When the score path is enabled, it runs as the **third** revhist detection path — after `revision_history_label_pattern` (paragraph/heading text) and `revhist_table_header_pattern` (joined-header regex). All three paths arm the same consume-until-next-paragraph state, so continuation table slices drop regardless of which path fired.
**Why**: The user surfaced six structural variants of revhist tables in real corpora (heading+table, paragraph+table, bare table, label in merged top row, label in merged bottom row, varying column count/order/vocabulary). Regex-based detection (`revhist_table_header_pattern`) could enumerate some combinations but produced combinatorial blow-up: 6 variants × 4 column orders × N vocabulary variants × header-vs-merged-cell location. Real-corpus runs after multiple miner iterations still missed ~20% of docs. Multiple options considered: (a) keep regex-only and broaden the alternation each time a miss appears — empirically converging slowly and producing a deep alternation tree; (b) hardcode heuristics in parser code — violates D-003 (per-MNO behaviour in code); (c) **chosen**: per-signal scoring with the signal scores AND weights stored on the profile, so the corrections workflow tunes thresholds and vocab lists rather than authoring regexes. Robustness against vocabulary, order, count, and label position out of the box; defaults cover ~80% of the corpus without tuning.

Three signals chosen for independence: position is cheap and discriminates against body tables that share vocab; vocabulary is set-based (order-independent) and absorbs the long tail of header variants; cell fingerprint catches revhist-table shape (version + date column) even when headers are uninformative. Vocabulary scans body-row cells in addition to headers + merged-cell text to handle reversed-table layouts where the actual column headers live in a body row and the label lives in a footer-merged row.
**Consequences**:
- `DocumentProfile` gains the `revhist_detection` field. Bootstrap profile sets `enabled: true`; the schema default is `enabled: false` so existing profiles aren't surprised.
- Diagnostic surface: when scoring fires, `RevhistMatch.pattern_id="score"` and `RevhistMatch.score_breakdown` records the per-signal contribution. Surfaced in Summary tab + `parse_debug revhist` CLI for "why didn't this match" diagnosis.
- Bootstrap profile size grows by one block (and per-corpus profiles can override threshold, vocab list, weights).
- Scoring runs once per TABLE block — O(num_cells × num_tokens) per table. Negligible vs. other parse stages.
- Profile_miner CLI extended to emit signal-tuning recommendations rather than regex proposals when the user's corrections all share `block_type=table`. Smaller LLM search space; reviewer reviews threshold deltas, not regex correctness.

## D-074: Word's `toc N` paragraph style is a universal TOC marker — drop independent of profile config
**Status**: Active · **Date**: 2026-05-14.
**Decision**: New module-level `_WORD_TOC_STYLE_RE = re.compile(r"(?i)^toc\s+\d+$")` in `structural_parser.py`. The body pass drops every block whose `style` matches this regex, **before** heading classification, **regardless** of profile config. Sits alongside the existing TOC-page filter and `toc_detection_pattern` regex filter as a third TOC drop path.
**Why**: Word's `TOC 1` / `TOC 2` / … paragraph styles are the canonical TOC entry marker — applied automatically by Word's "Insert Table of Contents" command and inherited by every doc generated from that template. They're an unambiguous structural fact independent of any per-corpus profile choice. The previous behaviour gated TOC-style detection on `profile.toc_detection.style_pattern` being set; bootstrap profiles without it let `toc N`-styled paragraphs reach the heading classifier AND the glossary-section matcher, causing TOC entries like "Glossary ........ 5" to be misclassified as the real Glossary section heading. Options considered: (a) require every profile to set `style_pattern` correctly — adds friction and is forgettable; (b) auto-detect Word styles when `style_pattern` is empty — same goal; (c) **chosen**: bake the Word TOC style convention into the parser as an unconditional filter. Word styles are not corpus-specific — they're a doc-level convention shared across every Word-generated document.
**Consequences**:
- Profile knob `toc_detection.style_pattern` is still respected — its regex runs in the pre-pass to build the section-number index. The new universal filter is additive; it doesn't replace the pre-pass.
- Custom profiles that override `style_pattern` with a different regex (rare) still work — the universal filter only matches the canonical Word naming. Anything that doesn't match (e.g. a custom corpus style `"PartTOC 1"`) falls through to the profile-driven path.
- Behaviour change for any pre-existing profile that DID NOT set `style_pattern` AND had `toc N`-styled blocks reaching the body pass: those blocks now drop (previously, they reached classification and either became no-section "phantom" Requirements OR fell to body text of the current section). Either prior behaviour was wrong; new behaviour is correct.
- Counter: contributes to `ParseStats.toc_blocks_dropped`, surfaced in `parse_summary.toc_entries`.
- 1 regression test: `test_word_toc_style_paragraphs_skipped_before_glossary_match`.

## D-075: `find_mapping_file` falls back to `_provenance.bootstrap_id` when stem lookup fails
**Status**: Active · **Date**: 2026-05-14.
**Decision**: When `find_mapping_file` doesn't find a snapshot at `customizations/mappings/<profile_stem>.json`, it reads `_provenance.bootstrap_id` from the profile JSON content and retries the snapshot lookup with that name. Stem lookup still wins when both match (existing behaviour preserved for profiles where the file name and bootstrap_id are aligned).
**Why**: The pipeline's `profile` stage copies the active profile (whether `<env_dir>/corrections/profile.json` or an `--profile <path>` supplied file) to the standard location `<env_dir>/out/profile/profile.json`. After the copy, the file stem is the generic `"profile"`. The previous stem-based mapping lookup then looked for `customizations/mappings/profile.json` — which never exists — and substitution proceeded with an empty mapping. Specific placeholders like `<MNO0>` stayed literal in the substituted profile, downstream regex tests (notably `_req_id_anchored_re` in `_heading_title_text`) couldn't match the real IR content (which carried the substituted value `VZ_REQ_…`), and a cascading failure mode emerged: heading title not stripped → revhist label regex's `$` anchor missed → heading became SECTION_HEADING → revhist table never dropped. This was flagged in STATUS on 2026-05-10 (D-066-adjacent) with a known workaround (hand-edit `<MNO0>` in the corrections file).

Options considered: (a) preserve the original filename when copying (e.g. `out/profile/bs_d7a2c81f.json`) — requires every downstream consumer to discover the file by glob; (b) keep stem lookup and document the manual workaround — fragile; (c) **chosen**: read the bootstrap_id from the profile content. The bootstrap profile already carries `_provenance.bootstrap_id` precisely so the profile is self-identifying; using it as a fallback key makes the discovery content-anchored rather than path-anchored. Survives copies, renames, and the pipeline's normalising flow.
**Consequences**:
- `find_mapping_file` now reads the profile JSON when the stem lookup misses. Adds one disk read per parse-stage init when no stem match exists. Negligible.
- Profiles without a `_provenance.bootstrap_id` block fall through to the existing `<env_dir>/state/cline-mapping.json` path. No regression.
- The hand-edit-`<MNO0>` workaround is no longer required.
- 2 new tests: `test_falls_back_to_provenance_bootstrap_id` and `test_stem_lookup_wins_over_provenance`.

## D-076: `--profile <path>` short-circuits the pipeline profile stage
**Status**: Active · **Date**: 2026-05-14.
**Decision**: When the user supplies `--profile <path>` to `core.src.pipeline.run_cli`, `PipelineContext.standalone` sets `ctx.state["profile_path"]` before any stage runs. The `profile` stage now checks this state up-front: if it's set, points to an existing file, and differs from the default `out/profile/profile.json`, the stage **copies** the supplied file to `out/profile/profile.json`, sets `ctx.state["profile_path"]` to the destination, and returns OK without consulting `<env_dir>/corrections/profile.json` or running the profiler. The CLI flag is authoritative when present.
**Why**: STATUS-flagged 2026-05-10. The previous behaviour was that `--profile <path>` set `state["profile_path"]`, then the `profile` stage overwrote it: the stage's correction-override branch would copy `corrections/profile.json` if it existed, or run the profiler otherwise — silently ignoring the explicit `--profile`. Users had to copy their preferred profile to `<env_dir>/corrections/profile.json` first, then run the pipeline; the `--profile` flag was effectively non-functional. Options considered: (a) document `--profile` as "use with `--start parse` only" — adds usage friction; (b) deprecate `--profile` since the corrections-file workflow exists — but the flag is convenient for running pipelines against profiles in `customizations/profiles/` directly; (c) **chosen**: respect the flag. CLI flag is the most explicit signal of user intent and should win.
**Consequences**:
- `--profile <path>` now lets users run with profiles from `customizations/profiles/` directly without copying to `corrections/`. Useful for testing alternative profiles without polluting per-env state.
- Implicit precedence: explicit CLI flag > `corrections/profile.json` overlay > profiler-generated default. Each tier is consulted only if the higher tier wasn't supplied.
- The `profile` stage still copies the explicit profile into `out/profile/profile.json` so downstream stages find it where they expect — no special-casing in the parse stage.
- Stats report `source: "explicit --profile"` (vs `"correction"` or `"profiler"`) for transparency.
- No tests added — covered by existing run_cli integration but the path is exercised manually.

## D-077: `_project_root_from_profile` falls back to module self-root when parent-walker dead-ends
**Status**: Active · **Date**: 2026-05-15.
**Decision**: When `_project_root_from_profile` cannot locate a `customizations/`-bearing directory by walking up from the profile's parent, it falls back to `Path(__file__).resolve().parents[3]`. The fallback uses the running code's own location — `profile_substitute.py` lives at `<repo>/core/src/profiler/profile_substitute.py`, so `parents[3]` is the repo root. If the fallback root also lacks a `customizations/` directory, the function returns None as before.
**Why**: D-075 added the `_provenance.bootstrap_id` fallback for `find_mapping_file`, but both lookup mechanisms require a project root first. The parent-walker only finds one when the profile lives **inside** the project tree. The pipeline's standard layout puts the active profile at `<env_dir>/out/profile/profile.json`, and `env_dir` is per-environment runtime state (per D-022) — commonly outside the repo tree. When that happens, the walker traverses `<env_dir>/out/profile/` → `<env_dir>/out/` → `<env_dir>/` → typically a non-repo parent → root, never finding `customizations/`. Mapping lookup silently returns None. Specific placeholders like `<MNO0>` stay literal in the substituted profile. Downstream regex tests (notably `_req_id_anchored_re` inside `_heading_title_text`) can't match the real-value runs in the IR. Symptom: heading "Revision History `<MNO0>_REQ_fooBar_12345`" with `anchor=last_run` failed run-stripping, so the revhist regex's `$` anchor missed on the un-stripped title, so the heading was misclassified as SECTION_HEADING and the following revhist table was never dropped. Same SECTION_HEADING leak D-075 was supposed to close.

Options considered: (a) require callers to pass an explicit `project_root` argument — invasive, every call site needs updating; (b) walk up from the *current working directory* instead — flaky depending on where the CLI is invoked; (c) use a `NORA_PROJECT_ROOT` env var as override — adds configuration surface, easy to forget on the work PC; (d) **chosen**: derive from the running module's own file path. The running code unambiguously knows where it lives inside the repo, and the path math is stable: a project with the standard `core/src/<module>/<file>.py` layout always has `parents[3]` = repo root.
**Consequences**:
- `find_mapping_file`'s discovery chain now resolves reliably regardless of the user's `env_dir` placement.
- Hard dependency on the file's own layout — if `profile_substitute.py` is ever moved (e.g. `core/src/profiler/` → `src/profiler/`), `parents[3]` must change too.
- Defensive: when the walker DOES find a root, that root wins (no behaviour change for in-tree profiles).
- 1 new test (`test_falls_back_to_module_self_root_when_walker_dead_ends`) uses `monkeypatch.setattr(ps, "__file__", ...)` to simulate the env-dir-outside-repo layout in tmp_path.

## D-078: Generic `<PLAN>` placeholder widened to `[A-Za-z0-9_ ]+` to cover mixed-case + multi-word plan codes
**Status**: Active · **Date**: 2026-05-15.
**Decision**: The `<PLAN>` entry in `GENERIC_PLACEHOLDERS` (D-062's generic-substitution table) changes from `[A-Z0-9_]+` to `[A-Za-z0-9_ ]+`. Specific `<PLAN0>`, `<PLAN1>` etc. continue to substitute their mapped values verbatim (`re.escape`-protected) per D-062.
**Why**: Real corpora ship plan codes in three orthogonal shapes that the previous default rejected:
- All-caps (`FOOBAR`) — handled by the old class.
- Mixed-case / CamelCase (`fooBar`) — rejected; the heading run text didn't match the substituted req_id pattern, so `_heading_title_text` left the run un-stripped and downstream label matching missed.
- Multi-word with embedded space (`foo Bar`) — same failure mode; the space wasn't in the char class at all.

Closes the 2026-05-09 STATUS flag (`D-062 generic placeholder regex defaults are biased toward all-caps plan codes`) and the matching Next-list entry (`Cross-MNO <PLAN> regex breadth`). The flag captured the dilemma — broaden (looser, more matches) vs. keep strict (tighter, more misses). Real-corpus diagnostic this session showed the choice empirically: mixed-case and spaced plan codes were producing the SECTION_HEADING-leak cascade and were the dominant remaining cause of missed revhist detections after the 2026-05-14 anchor/method/bootstrap_id fixes.

Options considered: (a) keep strict, document the limitation, require corpus owners to override `<PLAN>` per-profile — every new corpus pays a setup tax. (b) Widen char class without space; require space-bearing codes to use an underscored form — fails when the source docs ship the original spelling. (c) **Chosen**: widen to `[A-Za-z0-9_ ]+`. The risk is greedier matching in body prose where multiple req_ids share a line — but the surrounding pattern (`<MNO>_REQ_<PLAN>_\d+`) still anchors with the prefix + `_\d+` suffix, so false positives remain bounded.
**Consequences**:
- D-062's generic substitution table changes shape. Every profile that uses `<PLAN>` (specifically: profiles without a more constrained per-corpus override) inherits the wider regex.
- Test assertions pinning the old `[A-Z0-9_]+` form were updated en masse (5 sites).
- Regression test `test_plan_generic_matches_mixed_case_and_spaced_plan_codes` covers all three shapes (all-caps, mixed-case, multi-word).
- Body-prose false-positive risk increases marginally but is bounded by the surrounding pattern anchors. No reports of false positives in audit data so far.
- Corpora that want stricter matching can override `<PLAN>` to `[A-Z0-9_]+` (or any more constrained class) in their per-corpus profile — generic substitution is the *default*, not a forced choice.

## D-079: Density gate (≥75% keyword density) layered on top of `definitions_section_pattern`
**Status**: Active · **Date**: 2026-05-15.
**Decision**: After `_extract_definitions` matches a section title via `definitions_section_pattern` (the legacy substring regex, default `(?i)acronym|definition|glossary`), apply a hardcoded density gate before accepting the match. Require ≥75% of the title's *meaningful* tokens to belong to a narrow keyword set (`glossary`, `definition[s]`, `acronym[s]`, `abbreviation[s]`, `term[s]`). Stopwords (`and`, `or`, `the`, `with`, `for`, `of`, `a`, `an`) and any embedded req_id token are stripped before counting. The helper (`_glossary_label_density` + the keyword/stopword sets + the `_GLOSSARY_LABEL_MIN_DENSITY = 0.75` threshold) lives in `structural_parser`; `parse_debug` imports it so the diagnostic preview and the production gate cannot drift apart.
**Why**: The legacy regex is a permissive substring match — it fires on any title containing one of the keywords, including non-glossary titles like "Section 2.3 Acronyms list and notes" (1/5 = 20% density) or "Performance Requirements: Acronyms used in this section" (1/7 = 14%). Real-corpus regression: these false positives surfaced as spurious glossary annotations on the Review tab. The strand's original goal was to build a full `GlossaryDetection` profile dataclass mirroring `RevhistDetection`'s three-signal scorer (heading-text + vocab + cell fingerprint), but in practice the missing piece causing user-visible bugs was just the vocab signal applied as a tightening gate, not a new detection path. The density rule is that signal in isolation.

Options considered:
- (a) Build the full `GlossaryDetection` schema field with three signals — bigger change, opens design decisions (defaults, weights, threshold-as-profile-knob); deferred.
- (b) Tighten `definitions_section_pattern` per-corpus — pushes complexity onto every profile owner; corpus drift makes tuning brittle.
- (c) **Chosen**: gate the existing regex match by density in the parser, with the threshold + keyword set hardcoded. Easy to add a profile knob later if a corpus needs different tuning.

**Consequences**:
- The density gate runs unconditionally — any corpus where the legacy regex was a true positive at <75% density now fails. So far no such cases observed; the 75% threshold tolerates connectives via stopword filtering.
- The full `GlossaryDetection` profile field remains deferred. If the corpus shows missed glossaries (regex misses entirely), a follow-up strand can build the three-signal scorer.
- The req_id stripping uses the *non-anchored* `_req_id_re` (substring strip), not `_req_id_anchored_re` (full-match) — embedded req_ids in titles get stripped correctly.
- Tests in `test_structural_parser_headings.py`: density rejects regex-matching FP titles; req_id strip preserves single-word glossary titles (e.g. `Glossary VZ_REQ_fooBar_12345` → 1/1 = 100%).
- Wire into the to-be-built `GlossaryDetection` field as the vocab signal when that strand opens.

_Promoted from strand: glossary-scoring on 2026-05-15._

## D-080: Drop docx + pdf "degenerate-table" filter that silently lost 1×1 and sparse content tables
**Status**: Active · **Date**: 2026-05-15.
**Decision**: In both `docx_extractor.py` and `pdf_extractor.py`, the table-shape filter changes from `len(non_empty_headers) <= 1 AND total_cells == 0` to `non_empty_headers == 0 AND non_empty_body == 0` — i.e. drop a table only when every cell across headers + body is empty. The PDF-specific 1×1-hallucination filter (pdfplumber fabricates these around small column-aligned text regions like VZW OA's small-font req_id markers) stays intact as a separate guard.
**Why**: The old filter shape accidentally dropped any table where ≤1 header cell had content AND the body was empty. That includes 1×1 content tables (Word commonly uses these as paragraph wrappers — a section's entire body in a single-cell table for layout purposes) and 1×N tables with one content cell. Real-corpus regression: a doc's next-section content (wrapped in a 1×1 table) was missing from `out/extract/<DOC>_ir.json`. The filter's stated intent — "single empty column" — never matched its implementation.

**Consequences**:
- More tables flow through to the parser. The parser already tolerates sparse and single-cell tables.
- PDF 1×1 hallucinations still get filtered by the dedicated guard immediately after.
- Three regression tests in `test_docx_extractor_merges`: single-cell content survives; truly empty 2×3 still dropped; 1×3 sparse-content survives.
- This fix is a prerequisite for D-081 (nested-table walk) — many of the nested tables that walk picks up live inside dropped wrappers, but some live inside wrappers that do carry surrounding text.

_Promoted from strand: glossary-scoring on 2026-05-15._

## D-081: Walk nested tables inside docx cells; emit each as its own TABLE block
**Status**: Active · **Date**: 2026-05-15.
**Decision**: In `docx_extractor.py`, after each top-level `<w:tbl>` block emits, recursively walk its cells' `cell.tables` (python-docx) and emit each nested table as its own TABLE block. Order: depth-first, parent-first-then-children, document order within each cell. Dedupe merged regions via `_tc` identity so a horizontally-merged cell isn't visited per-column. Run the nested walk even when the outer block was dropped as empty — a pure layout wrapper (1×1 with no surrounding text, only nested content inside) is empty by intent.
**Why**: `_table_block` reads only `cell.text` (paragraph text via python-docx), so tables nested inside cells were silently invisible. Real-corpus pattern: an outer 1×1 wrapper around a real 2-column glossary table (Acronym/Term | Definition) plus a trailing description paragraph. Before this fix the wrapper survived with `cell.text` = the trailing paragraph only — the actual acronym/definition rows never reached the IR, and the parser matched the glossary section by title but found zero entries. Word's natural way of placing content tables inside layout wrappers means nested tables aren't pathological; they're standard.

Options considered:
- (a) Flatten — treat the wrapper as a pass-through and emit only the nested table. Loses the wrapper's surrounding text (the trailing paragraph case).
- (b) Pre-walk cells to find nested tables; emit nested *before* parent — preserves OOXML document order strictly but inverts the natural recursion shape.
- (c) **Chosen**: emit parent first, then children. Slight document-order inversion within a single parent's scope, but trivially correct recursion, and the parser treats a section's tables as a bag for definitions extraction so the inversion is cosmetic.

**Consequences**:
- IR block count grows when wrappers are present; downstream consumers iterate tables by section so order-within-section is the main visible effect.
- Recursion is unbounded — any depth of nested tables is captured. No real-corpus case yet needs more than one level.
- Two regression tests in `test_docx_extractor_merges`: wrapper + trailing paragraph → two TABLE blocks (outer + nested); empty wrapper → outer dropped, nested survives.
- Does **not** address tables inside `<w:sdt>` content controls, `<w:txbx>` text boxes, or other non-`<w:tbl>` body containers — that remains a separate concern if a future corpus surfaces it.

_Promoted from strand: glossary-scoring on 2026-05-15._

## D-082: Verify SIRA on NORA corpus as a standalone sandbox, not via partial integration
**Status**: Active · **Date**: 2026-05-16.

**Decision**: Run SIRA's full published pipeline (`facebookresearch/sira`) as a standalone sandbox against NORA's MNO requirements corpus. Build two thin adapters only — (a) NORA parse output → BEIR-shape `corpus.jsonl` + `queries.jsonl` + `qrels.tsv` from the 18-Q eval; (b) SIRA's output ranks → NORA's req_id-level recall/MRR. Do *not* extract SIRA's primitives (corpus enrichment / query enrichment / LLM reranker) and bolt them into NORA's chunk-builder + query pipeline. The decision to integrate (or not) is deferred to Phase 2, conditional on Phase 1 results.

**Why**: Three reasons drive standalone over partial integration.

(1) **Faithful test of the paper's claim.** SIRA's contribution is end-to-end — DF-filtered dual-sided enrichment is co-designed across corpus and query stages, and the LLM reranker assumes the enriched candidates. Decomposing into "one primitive at a time" tests something the authors didn't design. If a hybrid wins 3%, attribution is ambiguous: was it SIRA's enrichment, NORA's retained structure, or interaction effects? Clean head-to-head answers the question NORA needs.

(2) **Cheaper engineering and reversibility.** Adapters are a few hours of plumbing in a `sandbox/` sibling dir. Integration would touch `vectorstore` (chunk_builder), `query` (rewrite stage), `eval` (comparison harness), `llm` (new caller path) — days of careful work in NORA core, with revert risk on every commit if SIRA flops. Sandbox loses → delete sandbox; integration loses → revert N commits with rot risk.

(3) **Attribution discipline.** NORA's weakest categories on the 18-Q eval (cross_doc 37.5%, standards_comparison 50%) are exactly the failure mode SIRA targets (concept queries where the right doc exists but doesn't rank in top-k). A standalone win/loss is the cleanest signal; the integration shape question becomes well-posed only after that signal lands.

Options considered:
- (a) Primitives-first decomposition (query expansion alone, then corpus enrichment, then reranker, each as opt-in NORA stages with per-phase eval). User rejected — SIRA's dual-sided enrichment loses its DF-filter pairing when split.
- (b) Dual-sided enrichment as a NORA-internal chunk-builder change, dense + graph + structure preserved. User rejected — tests a hybrid neither team designed; ambiguous attribution.
- (c) **Chosen**: full SIRA standalone, adapter-only, in a `sandbox/` sibling dir.

**Consequences**:
- Phase 1 work lives in `sandbox/sira/` (or sibling repo) — never under `core/src/`. Curated module surface untouched until Phase 2 decision fires.
- The 18-Q eval set + ground-truth req_id qrels become a shared artifact: NORA evals against its own pipeline, SIRA evals against its pipeline, same inputs. Eval harness adapter for SIRA-side metrics is new.
- Phase 2 decision tree: SIRA wins → integration shape discussion (adopt primitives vs run-as-service vs replace retrieval lane); SIRA loses → archive strand with finding entry to canonical DECISIONS noting "tested and rejected"; mixed → small targeted adoption per category.
- The proprietary LLM (100B+) becomes the only LLM in the loop for the first run. The 50-line FastAPI shim is operationally a new service surface that needs to stay alive while SIRA runs — Phase 0 finding will determine if `src/sira/llm.py`'s call shape requires a translation layer.
- Cost of running SIRA-as-published: per-doc + per-query LLM calls at ingestion; ~50 LLM calls per eval query at reranker top_n. The proprietary LLM's $0-marginal-cost makes this tractable; an OpenRouter run would be measurable $.

_Promoted from strand: sira on 2026-05-28._

## D-083: Pass-through shim with env-var-driven mode selection — bypasses `proprietary_provider.complete()` when the LLM is OpenAI-compatible
**Status**: Active · **Date**: 2026-05-17.

**Decision**: The FastAPI shim at `sandbox/shim/openai_shim.py` supports two modes selected at startup by env vars:

  * **Pass-through** (when `NORA_LLM_BASE_URL` is set): the shim forwards SIRA's request body verbatim to the upstream OpenAI-compatible endpoint (with `Authorization: Bearer ${NORA_LLM_API_KEY}` injected and the `model` field optionally overridden via `NORA_LLM_MODEL`). The proprietary LLM's existing OpenAI-compatible `/v1/chat/completions` endpoint is the only LLM in the loop. `customizations/llm/proprietary_provider.complete()` is **not invoked at all** — its stub-`NotImplementedError` body is irrelevant on this path.

  * **Adapter** (when `NORA_LLM_BASE_URL` is unset): the shim falls back to calling `customizations/llm/proprietary_provider.complete()`. SIRA's OpenAI messages collapse into the `(system, prompt)` pair the provider expects; the provider's string response is re-enveloped into the OpenAI shape.

Env-var names (`NORA_LLM_BASE_URL` / `NORA_LLM_API_KEY` / `NORA_LLM_MODEL` / `NORA_LLM_TIMEOUT` / `NORA_LLM_SKIP_PROXY` / `NORA_LLM_VERIFY_SSL`) deliberately mirror NORA's existing OpenAI-compatible provider env vars (D-044 / D-049), so any shell that already has NORA's regular LLM configured picks up the shim's pass-through mode for free.

**Why**: Real-corpus encounter on the work PC: the company's proprietary LLM exposes a fully OpenAI-compatible `/v1/chat/completions` endpoint. With the adapter-only design from D-082 / strand opening, the user would have had to:

  1. Implement `proprietary_provider.complete()` in NORA's `customizations/llm/`.
  2. Inside that, build an OpenAI request, parse its response.
  3. Have the shim re-collapse SIRA's messages → `(system, prompt)` → re-build OpenAI request inside `complete()`.

That's a triple-translation: OpenAI shape (SIRA) → flattened (`complete()` interface) → OpenAI shape (LLM endpoint) → flattened (provider return) → OpenAI shape (shim response). Pure waste when the endpoint and SIRA agree on the shape natively.

Options considered:
- (a) Fork SIRA's `src/sira/llm.py` and replace the hardcoded `127.0.0.1:{port}` URL with the proprietary endpoint. Rejected — modifies upstream source, breaks the "SIRA stays whole" principle from D-082, lost on every `git pull`.
- (b) Always-adapter shim (the original design). Rejected — forces every deployment to author `proprietary_provider.complete()` even when the underlying LLM is OpenAI-compatible.
- (c) **Chosen**: dual-mode shim. The mode is selected at startup by whether `NORA_LLM_BASE_URL` is set. Both code paths are kept; the adapter path remains for deployments whose proprietary LLM uses a non-OpenAI API.

**Consequences**:
- The shim becomes a thin proxy in the common case (Meta-style internal LLMs typically expose OpenAI shape). Operationally this is one `uvicorn` process with five env vars.
- Lazy import of `proprietary_provider` (loaded only when `NORA_LLM_BASE_URL` is unset). Side benefit: deployments that never use adapter mode don't even have to read `proprietary_provider`'s stub.
- The shim's surface grew during this session beyond pass-through: TLS knobs (`SSL_CERT_FILE` honored via httpx `verify=<path>`; `NORA_LLM_VERIFY_SSL=false` escape hatch), proxy bypass (`NORA_LLM_SKIP_PROXY=true` → httpx `trust_env=False`), `/v1/models` handler (so SIRA's auto-detect probe in `run_pipeline.py` finds the shim and doesn't fall through to spawning sglang). All of these are corporate-environment frictions that would exist for *any* SIRA-vs-internal-LLM bridge regardless of mode choice.
- `/healthz` surfaces the active mode + resolved TLS / proxy / model-override config — single-curl debugging.
- The shim now has ~250 LOC, two distinct code paths, and six env-var knobs. Beyond the original "50-line shim" framing in D-082 but the additions are all corporate-friction fixes; no new architectural commitments.
- If a future deployment needs to test multiple proprietary LLMs side-by-side, restart the shim with different env vars between runs. Single-instance limitation, fine for our use case.

_Promoted from strand: sira on 2026-05-28._

## D-084: Pinned-chunk synthesizer path + two-gate score filter
**Status**: Active · **Date**: 2026-05-22.

**Decision**: Add a `pinned_chunk_ids` early-return path to `QueryPipeline.query()` that skips Stages 3-5 (graph/retrieval/rerank) and feeds caller-provided chunks into `_context_builder.build()` + `LLMSynthesizer.synthesize()`. Gate which chunks reach the synthesizer with a two-floor filter: absolute (`NORA_SIRA_PIN_MIN_SCORE=30`) AND relative (`score ≥ NORA_SIRA_PIN_REL_THRESHOLD=0.5 × max_score`). Filtered chunks render dimmed in the UI, not hidden.

**Why**: External retrievers (SIRA) need NORA's synthesizer for apples-to-apples answers. Rank-K cutoff was the obvious alternative and rejected — K has no principled value. Score-based gating uses the reranker's own signal; two floors handle two failure modes (uniform-low confidence vs relative outliers in a high-scoring set).

**Consequences**: Any external retriever is now A/B-testable against NORA via the shared synthesizer. Pinned-chunk is the only `query()` path that synthesizes without running graph/retrieval. Thresholds are eyeballed; flagged for sweep. Filtered chunks visible (not silently dropped) is deliberate — debugging needs to see exclusions.

_Promoted from strand: sira on 2026-05-28._

## D-085: Per-query SIRA probing stays in sandbox boundary
**Status**: Active · **Date**: 2026-05-22.

**Decision**: Interactive per-query SIRA probing runs as a separate FastAPI service (`sandbox/sira_query/service.py`); NORA's Test page calls it over HTTP via `httpx`. SIRA primitives are *not* ported into `core/src/query/`.

**Why**: Extends D-082's "standalone sandbox" principle from bulk eval to interactive probing. Porting reranker/enrichment into NORA's retrieval lane was the alternative — rejected because Phase 1 verdict was adverse and porting would commit code to `core/` for a primitive we may archive. HTTP boundary keeps the strand archive-able by `rm -rf sandbox/sira_query/` if SIRA loses.

**Consequences**: One extra process to keep alive during Test-page use (shim:8030 + SIRA service:8040 + NORA web). Latency added vs in-process call, acceptable for probing. Service is independently shutdown-able when strand archives.

_Promoted from strand: sira on 2026-05-28._

## D-086: Pin `sira_query` service to specific offline run via per-stage env vars
**Status**: Active · **Date**: 2026-05-23.

**Decision**: Three independent env vars (`NORA_SIRA_DOC_ENRICH_RUN`, `NORA_SIRA_QUERY_ENRICH_RUN`, `NORA_SIRA_RERANK_RUN`) pin the service to a specific run per stage; `NORA_SIRA_USE_LATEST_RUNS=true` is a shortcut to auto-pick newest mtime. Fallback when none resolve: SIRA's `enrichments/doc/best.jsonl` pointer + `SIRA_CLONE_ROOT` prompts (historical pre-patch behavior). `/healthz` surfaces every resolved source path so deployments can record exact provenance in one curl. Doc-side enrichment is now applied at service startup via `_bm25.enrich_batch(items)` — was inert pre-patch.

**Why**: SIRA's stage timestamps are per-stage (`enrich-<T1>`, `query-enrich-<T2>`, `rerank-<T3>` with T1<T2<T3), so a single `NORA_SIRA_RUN_NAME` doesn't span all three stages. Pure `best/`-pointer reliance is non-deterministic — the pointer follows scoring, not recency, so a re-run that scored lower would silently not propagate to the service. Three explicit env vars allow ablation experiments (mix-and-match across runs) while making provenance auditable.

**Consequences**:
- Service deployments now have end-to-end provenance via `/healthz` — save alongside experimental results.
- Doc-side enrichment finally applies; the prior in-draft Phase 1 verdict was against an incomplete service and needs re-measurement (addressed in D-087).
- New env-var surface area needs SETUP.md docs (follow-up).
- Three independent env vars put consistency responsibility on the user; worth noting in launch-runbook docs.

_Promoted from strand: sira on 2026-05-28._

## D-087: SIRA is a retrieval lane, not a wholesale replacement (supersedes the prior in-draft Phase 1 verdict)
**Status**: Active · **Date**: 2026-05-23. Supersedes the prior in-draft Phase 1 verdict (preserved in `strands/_archive/sira/decisions-draft.md` as D-DRAFT-5, dropped at land-time per its own recorded instruction).

**Decision**: Supersede the prior in-draft Phase 1 verdict's "SIRA tested and rejected on this corpus." That measurement was taken against (a) LTE-EMM-biased v01 prompts that mis-pattern-matched non-LTE reqs, and (b) a `sira_query` service that silently dropped doc-side enrichment (fixed in D-086). With both corrected, observed behavior is consistent with SIRA's design: DF-filtered enrichment + pointwise LLM rerank correctly surfaces reqs whose enrichment phrases overlap the query, including related reqs from sibling plans; reqs without strong discriminative phrases drop out of rank by design. SIRA is structurally **a lookup retriever** — strong on specific-entity queries, weak on breadth/summarize queries (where the DF-filter prunes exactly the shared vocabulary breadth queries want). For NORA's mixed query workload, SIRA is a **candidate as one retrieval lane (the lookup lane) alongside other lanes**, not a wholesale replacement.

**Why**: "Tested and rejected" (the prior in-draft Phase 1 verdict, as-is) is wrong because the measurement conditions were broken. "Adopt wholesale" is wrong because the 3/25-VoWiFi result on a breadth query — even after fixes — is correct for SIRA's design but unsatisfactory user behavior. "Continue iterating SIRA prompts to fix breadth queries" doesn't solve it because the limitation is in the algorithm (DF-filter pruning shared vocabulary), not the prompts; prompts can move the line but not eliminate the structural mismatch.

**Consequences**:
- The breadth-query problem becomes an independent problem; natural home is `nora-retrieval-parent-displacement` strand or a new sibling, not `sira`.
- SIRA's sandbox infrastructure (offline pipeline, per-query service, debug CLI, prompts, eval adapter) survives and remains a candidate for the lookup lane.
- Phase 2's framing shifts from "decide integration shape if SIRA wins" → "decide how to integrate SIRA as one lane in a multi-lane retrieval architecture." Deferred to architect-driven work post-landing.

_Promoted from strand: sira on 2026-05-28._

## D-088: Per-stage LLM routing in SIRA service
**Status**: Active · **Date**: 2026-05-25.

**Decision**: Three env vars (`NORA_SIRA_RERANK_LLM_URL`, `NORA_SIRA_RERANK_LLM_MODEL`, `NORA_SIRA_RERANK_LLM_API_KEY`) route SIRA's rerank stage to a different OpenAI-compatible endpoint than query enrichment. Query enrichment continues through the standard shim. When unset, rerank falls back to the shim — preserves prior behavior.

**Why**: Query enrichment quality benefits from a strong LLM (proprietary 100B+ via shim); rerank just needs to output a 0-100 integer score — small local LLMs suffice. Alternatives: (a) repoint entire shim to a local LLM (rejected — sacrifices enrichment quality); (b) run two shim processes (rejected — extra ops); (c) **chosen** — per-stage env-var overrides on the SIRA service.

**Consequences**: New env-var surface on the SIRA service (3 knobs, surfaced via `/healthz`). Asymmetric routing introduces small consistency risk — operator can point rerank at a non-OpenAI-compat endpoint. Mitigated by graceful error logging per call (falls back to score=0). Establishes a reusable pattern for any future SIRA stage that wants its own LLM endpoint.

_Promoted from strand: sira on 2026-05-28._

## D-089: Batch rerank via `NORA_SIRA_RERANK_BATCH_SIZE`
**Status**: Active · **Date**: 2026-05-25.

**Decision**: Opt-in batch reranking via `NORA_SIRA_RERANK_BATCH_SIZE`. Default 0 = per-call (current behavior). N > 0 = batch in groups of N. New built-in batch prompt asks for JSON array `[{"id": N, "score": M}, ...]` mirroring the per-call rubric. Parser handles well-formed arrays, CoT preambles, per-object regex fallback for messy output, partial responses, and score clamping (0-100).

**Why**: Per-call dominated query latency on proxy-throttled environments (~5s/call × 25 candidates = 2 min). Batch eliminates 24/25 API round-trips. Alternatives: (a) `asyncio.gather` parallel (rejected — proxy throttling serializes anyway); (b) hardcoded mini-batches only (rejected — operator should pick the size). **Chosen** — fully configurable; default 0 preserves per-call back-compat.

**Consequences**: New failure mode is atomic per batch — one bad LLM call zeros N scores instead of 1. Quality risk on long-context LLMs (gemma3:12b can't fit 25-chunk JSON in 4096 output tokens; proprietary works but triggers aggressive pin filtering downstream). Recommendation captured: 5-10 is the practical batch-size sweet spot pending further measurement.

_Promoted from strand: sira on 2026-05-28._

## D-090: Plan-summarize gap → Tier-3 multi-granularity rows + fan-out (not taxonomy expansion)
**Status**: Active · **Date**: 2026-05-25.

**Decision**: Address SIRA's plan-summarize blind spot via Tier-3 multi-granularity rows in the BEIR adapter (doc-level + section-level rows alongside per-requirement rows), with a fan-out step in the SIRA service that expands doc/section matches into their constituent req-level chunks at retrieval time. Doc/section rows carry **`req_id` pointer lists** rather than full content — sidesteps BM25 length-normalization penalty while preserving req-level citation through the synthesizer. Implementation lives in new strand `plan-aware-sira`.

**Why**: SIRA's DF-filter is anti-breadth by design — prunes terms shared across many chunks, which is exactly what plan-summarize queries want. Alternatives: (a) **taxonomy-guided query expansion** (inject NORA's taxonomy features/phrases for the detected plan into SIRA's expansion) — rejected after user inspected taxonomy content and found it insufficient to drive retrieval; (b) **per-source-doc rows with full content** — rejected because Okapi BM25 length-norm penalizes long rows, defeating the purpose; (c) **chosen** — doc/section rows as pointer surfaces, fan-out at retrieval time. Best of both: strong plan-level matching AND req-level content for synthesis.

**Consequences**: Adapter gains an aggregation step; SIRA service gains a fan-out step. Per-req row format unchanged → backwards-compatible. Full SIRA pipeline rebuild required after adapter change. Two new row-id prefixes (`doc:`, `section:`) become a persistent shape — downstream code indexing by `_id` must tolerate. Per-req citation preserved through fan-out (synthesizer sees req-level chunks, cites correctly).

_Promoted from strand: sira on 2026-05-28._

## D-091: Multi-plan documents: promote plan to a per-requirement attribute (Option B), not one-tree-per-plan
**Status**: Active · **Date**: 2026-06-28.

**Context:** NORA models "plan" as one-per-document: `RequirementTree.plan_id`
/ `plan_name` are single scalars (set from a first-page metadata regex), the
parser emits one tree per source document (a load-bearing invariant — re-run
incrementality and per-document parse logs key off it), and the SIRA adapter's
multi-granularity rows group every requirement under the single `tree.plan_id`
(one `doc:<plan>` row per document). This fits MNO-A (one docx = one plan).
MNO-B's requirements arrive as a **single PDF whose sections each correspond to
a plan**, so under the current model all of MNO-B's plans would collapse into
one. Grounding showed the plan is recoverable per-requirement: req_ids carry a
plan prefix, and `_extract_plan_id_from_req` (driven by the profile's
`RequirementIdPattern.components` = separator + plan_id_position) already
derives it — it's currently computed transiently for cross-reference
classification, then discarded.

**Decision:** Adopt **Option B** — promote plan to a per-requirement
attribute, keeping one tree per document:
- Add a `plan_id` field to `Requirement`, populated during parse via the
  existing profile-configured req_id plan-extraction.
- Change the SIRA adapter to group `doc:<plan>` / `section:<plan>` rows by
  **per-requirement `plan_id`** instead of `tree.plan_id`, so one document
  yields N plans.
- Apply the same per-req plan treatment in the graph builder (FR-7 organizes
  the KG by plan).
- The "one tree per document" invariant is unchanged; plan becomes a
  *within-tree* dimension rather than a tree-level scalar.

**Why:** The plan is already encoded in req_ids and the extraction mechanism
already exists, so B is surgical (store what's already computed; group by it)
and stays profile-driven — MNO behavior lives in the profile, not code
(D-003 / FR-3). It preserves the load-bearing one-tree-per-document invariant.
Alternatives rejected: **Option A** (split a document into N trees at parse
time) breaks that invariant — bigger blast radius across re-run
incrementality and parse-log-per-doc, for no benefit B doesn't already give.
**Option C** (pre-split the PDF into N files before extraction) needs a
reliable PDF-splitter and loses the single-document reality — brittle
operational machinery outside the pipeline.

**Consequences:**
- `Requirement` schema gains `plan_id` (additive). Consumers that grouped by
  `tree.plan_id` (adapter, graph) move to per-req grouping.
- `tree.plan_id` becomes ambiguous for a genuinely multi-plan document — keep
  it as the document's primary/first plan for back-compat, but it is no longer
  the authority for plan grouping. (Define its exact semantics when wiring the
  parser change.)
- The **profile-extraction mechanism is config-only when the plan prefix is
  delimited** (e.g. `PLANB-123` -> set `separator` + `plan_id_position: 0`,
  exactly the MNO-A shape). If MNO-B's prefix is **run-together**
  (`PLANB123`, no delimiter), the split-on-separator model can't separate
  prefix from number and `RequirementIdPattern` needs a small **generic**
  extension — a regex-capture-group plan-extraction mode (still profile-driven,
  usable by any future corpus, not MNO-specific). Which shape MNO-B uses is the
  one open detail, resolved on inspecting the real document. The parser /
  adapter / graph changes are identical either way; only the profile's
  extraction config differs.
- Single-tree-per-document MNO-A corpora are unaffected: their requirements
  all share one plan prefix, so per-req grouping yields the same single
  `doc:<plan>` as today.

_Promoted from strand: multi-mno-nora on 2026-06-28._

## D-092: Add a generic "leading-id body-block" requirement-detection mode (MNO-B flat-requirement model)
**Status**: Active · **Date**: 2026-06-28.

**Context:** NORA's parser detects requirements **heading-first**. In
`_build_sections` (`core/src/parser/structural_parser.py`), a `Requirement` is
constructed in exactly one place in the paragraph pass — line ~1547, gated on
`_heading_depth(block) is not None` (the block is a heading). The only other
source is table-cell anchors (second pass, behind
`enable_table_anchored_extraction`). A plain body paragraph falls through to the
body-text path (~line 1601): its text is appended to the enclosing
heading-section, and if that section has no req_id yet, the body's inline req_id
is assigned **to the section** (first-occurrence-wins). The two documented
anchor sources are "paragraph anchors (heading or standalone-ID-in-small-font)
and table-cell anchors" (line 593-596) — there is no "body block whose text
starts with a req_id" primitive.

MNO-B's document model (confirmed by inspection) is the opposite shape:
sections/subsections are **non-requirement context** (no req_ids), and each
requirement is a **flat body paragraph beginning with its req_id**
(`<COMMON PREFIX>-<PLAN>-<DIGITS>`). Under the current parser, a subsection
containing N such requirements collapses into a **single** `Requirement`: only
the first req_id survives (as the subsection's id); the remaining N-1 are buried
as plain text in the section body and lost as structured requirements. So MNO-B
cannot be onboarded by a profile alone — the detection primitive it needs does
not exist in the parser.

**Decision:** Add a **generic, profile-selectable requirement-detection mode** —
"leading-id body-block" — alongside the existing heading-anchored mode:
- When active, the parser emits **one `Requirement` per body block whose text
  begins with the configured req_id pattern**. The req_id leads the requirement
  text; the rest of the block is the requirement body.
- Section/subsection **headings remain structural context**: they maintain the
  hierarchy stack so each leading-id requirement gets a `parent_section` /
  `hierarchy_path` from the enclosing headings — but headings themselves are
  **not emitted as requirements** in this mode (they carry no req_id).
- The mode is selected by a new **profile** field (working name
  `requirement_detection.mode: "heading" | "leading_id_body"`, default
  `"heading"`); MNO behavior stays in the profile, not in code (D-003 / FR-3).
- It **composes with D-DRAFT-1**: the per-requirement `plan_id` and the
  `RequirementIdPattern.components` plan-extraction apply unchanged on top of the
  new mode. MNO-B's `<PREFIX>-<PLAN>-<DIGITS>` is hyphen-delimited, so plan
  extraction is config-only (`separator: "-"`, `plan_id_position: 1`).

**Why:** A profile cannot express MNO-B because the underlying detection
primitive ("requirement = leading-id body block") is missing — this is a
genuine capability gap, not a config gap. A new **generic** mode (usable by any
future flat-requirement corpus, not MNO-specific) keeps the
behavior-lives-in-profile contract intact, reuses the existing req_id regex /
components / plan-extraction machinery, and leaves the heading-anchored path —
and therefore all MNO-A corpora — untouched (default mode stays `"heading"`).

Alternatives rejected:
- **Pre-process MNO-B into a heading-shaped document** (promote each leading-id
  paragraph to a synthetic heading before parse): brittle document-mangling,
  loses fidelity, and pushes corpus-specific logic into an out-of-pipeline
  pre-step — the same objection as D-DRAFT-1's rejected Option C.
- **Hack leading-id paragraphs through the existing heading path** (make the
  `numbering_pattern` match req_ids so each requirement looks like a heading):
  conflates structure with requirements — it pollutes `hierarchy_path` and
  `zone_type` classification, collides with the heading-continuation defenses
  (lines ~1497-1528), and makes genuine section headings and requirements
  indistinguishable downstream.

**Consequences:**
- New **profile** field (`requirement_detection.mode`, additive; default
  preserves today's behavior). `profile_schema.py` gains the field.
- Parser **`_build_sections` gains a mode branch**: in `leading_id_body` mode,
  body blocks matching the req_id pattern construct a `Requirement` (parent =
  current heading section, `hierarchy_path` from the heading stack) instead of
  appending to the section; headings build the hierarchy but are not emitted as
  requirements.
- **Open details, resolved when wiring + against the real document:** (1) final
  config field name/shape; (2) whether non-requirement headings are retained as
  structural-only nodes in the tree (for `hierarchy_path` / context / glossary +
  applicability passes) or elided after the hierarchy is built — the
  applicability (`_apply_applicability`), glossary, and reference-list passes all
  walk `sections`, so heading retention vs elision must be chosen so those passes
  still work; (3) interaction with the table-anchored second pass (likely
  mutually exclusive with this mode for MNO-B, but should not be hard-coded off).
- MNO-A corpora unaffected — default `"heading"` mode is the current code path
  byte-for-byte.
- This is **parser/profiler architecture work that must land before** an MNO-B
  profile can be authored — a profile written against the current parser would
  silently collapse MNO-B's requirements.

_Promoted from strand: multi-mno-nora on 2026-06-28._

## D-093: Preserve PDF source line boundaries additively (`ContentBlock.lines`), not by changing `block.text` or splitting on color
**Status**: Active · **Date**: 2026-06-28.

**Context:** The PDF extractor groups several pymupdf source lines into one
paragraph block and `_make_group` flattens them with `" ".join(...)`. For
MNO-B that merges a heading/title line and the body line beneath it into a
single **run-on sentence** (e.g. `5.1.2 Idle Mode The device shall…`, or
`ABC-PLAN-123 <title> The device shall…`), which **blurs the section hierarchy
for the LLM synthesizer** — it can't tell where the heading/title ends and the
body begins (MNO-B observation #5). The obvious signal — the title's blue
color — is **not usable for this corpus**: pymupdf reports the blue title text
as `color: 0` (the blue isn't a glyph fill color it surfaces), blue is *also*
used for section titles, and purple appears in *both* hyperlinked titles and
body hyperlinks. So color cannot delimit the title. What pymupdf *does* give us
reliably is the **line structure** (`block["lines"]`), which the extractor was
discarding.

**Decision:** Preserve the source line split **additively** on the IR. Add
`ContentBlock.lines: list[str]` (one entry per pymupdf source line);
`_extract_text_segments` tags each span with its line index and `_make_group`
reconstructs the per-line strings. Keep `block.text` exactly as before with the
invariant **`" ".join(lines) == text`**, so detection regexes (heading
numbering, req-id match) read the unchanged `text`; only consumers that need to
separate a heading/title line from the body read `lines`.

**Why:** Additive → **zero detection regression and a no-op for existing
corpora** (Verizon-OA never reads `lines`); robust to the color unreliability
(uses the line structure pymupdf actually provides, not the color it doesn't);
and it keeps the extractor **generic** — the *semantic* heading/body split stays
in the profile-driven parser, not hard-coded in extraction.

Alternatives rejected:
- **Change `block.text` line-join `" "` → `"\n"`** — global blast radius on the
  parser's `^`/`$`/`\s`/`.+` regexes; needs a full re-validation for a gain a
  side field delivers risk-free.
- **Split blocks on font color** — pymupdf doesn't surface this PDF's title
  color (`0` for blue), and blue/purple are ambiguous across section titles and
  hyperlinks; not a dependable signal here.
- **Emit a separate block per source line** — fragments multi-line body
  paragraphs and changes block granularity for all corpora.

**Consequences:**
- IR gains `ContentBlock.lines` (additive; empty for legacy IRs and non-PDF
  extractors — DOCX/XLSX can populate later if needed). `models` +
  `extraction` MODULE.md updated.
- The `leading_id_body` parser will **consume** `lines` to (a) separate a
  requirement's title line from its body and (b) build the ancestor-section
  "Context" with headings distinct from body — **not yet implemented**; lands
  with the MNO-B parser design.
- Verified end-to-end through real pymupdf and on the actual MNO-B PDF.
- A **multi-line requirement title** still can't be split from the body (the
  line boundary alone can't tell where a wrapped title ends without the
  unavailable color signal) — deferred nicety; the section hierarchy the
  synthesizer needs is delivered regardless.

_Promoted from strand: multi-mno-nora on 2026-06-28._

## D-094: Profile-driven content-start cutoff (skip front matter + intro chapters, anchored at a configurable Chapter N)
**Status**: Active · **Date**: 2026-06-28.

**Context:** MNO-B's single PDF is laid out as: front/title page → Table of
Contents → Chapter 1 (Preface) → Chapter 2 → Chapter 3 … Chapter N. From a
requirements perspective only **Chapter 3 onward** matters (each top-level
chapter from the start point is a plan; Ch.1/2 are general info with no
req_ids). The parsed tree must begin at the first requirements chapter. The
existing front-matter cutoff only drops TOC + revision-history
(`max(toc_end, revhist_end)`), **not real intro chapters**. And the front matter
is hard to detect *negatively* here: the TOC has **no `toc` style set**
(style-driven TOC detection is inert) and the leader-dot text pattern is
unreliable on this PDF (page numbers split into separate blocks); Chapters 1–2
have no clean drop marker.

**Decision:** Add a **profile-driven content-start cutoff** anchored at a
**configurable** top-level chapter number **N** (NOT hardcoded). New profile
field — working name `content_start_section` (string; empty = disabled). A
parser **pre-pass drops every block before the first *real heading*** (heading-
level font: bold + heading size) whose **top-level section number equals N**.
One positive anchor subsumes all the front material — front page, TOC, and
Chapters 1…(N-1) all precede Chapter N and fall away — with **no negative
TOC/intro detection required**.

**Font-gating (the one wrinkle):** a TOC *entry* for Chapter N (`N  Title … 45`)
also carries section number N, so the cutoff must distinguish the real heading
from its TOC line. It does so by **font** — the real chapter heading is bold +
chapter-size; the TOC entry is body-size. PyMuPDF captures size/bold reliably
(unlike color, which it does not surface for this corpus), so the cutoff fires
only on a heading-font block numbered N.

**Why:** A positive "content starts here" anchor is the single reliable signal —
chapters are numbered and Chapter N's heading is bold/sized/`N`. The negative
alternatives are fragile: TOC detection has no usable style field and a flaky
text pattern; per-chapter content drop has no marker. Keeping **N configurable**
(not hardcoded to 3) means a future release that adds/removes a front chapter is
a one-line profile edit, not code. Rejected: "start at the first top-level
chapter that *contains* a req_id" (fully automatic) — needs a look-ahead pass;
more machinery than warranted for a per-corpus constant.

**Consequences:**
- New profile field `content_start_section` (additive; empty default → **no-op**,
  so Verizon-OA and every existing corpus are unaffected — no gate beyond the
  empty default).
- Parser gains a pre-pass cutoff that runs **before** the existing TOC/front-
  matter logic and drops the contiguous front region in one shot.
- For MNO-B the profile sets `content_start_section: "3"`.
- Implementation detail: the font check reuses `FontInfo.size`/`bold` +
  `heading_detection.levels` hints to decide "real heading vs TOC entry"; exact
  threshold settled when wiring.

_Promoted from strand: multi-mno-nora on 2026-06-28._

## D-095: Consume `ContentBlock.lines` in the leading-id parser: split requirement title/body + populate a separate `Requirement.context` field
**Status**: Active · **Date**: 2026-06-28.

**Context:** In `leading_id_body` mode a requirement was built with everything
glued into `Requirement.text` — the req_id, the title, and the body were one
flattened run-on (the parser built `text` from `block.text`), and the enclosing
section/subsection chain (non-requirement *context* in this model) wasn't
attached to the requirement at all. So the LLM synthesizer couldn't tell the
requirement's title from its body, nor see where the requirement sits in the
`5 → 5.1 → 5.1.2` hierarchy. D-DRAFT-3 already preserved the per-line split on
`ContentBlock.lines` (`" ".join(lines) == text`), and sections carry their
heading + preamble text — the parser just wasn't using either.

**Decision:** Two parts — a parser split, and a generic **consumer-assembled**
context (not materialized in the tree, to avoid per-requirement duplication —
the bloat concern: one PDF holds all plans, and copying each requirement's full
ancestor-section content into the tree would multiply section text by the number
of requirements under it).
- **Title/body split (parser, `leading_id_body`):** a requirement's `title` is
  the header line (`lines[0]`) after the leading req_id; `text` is the body (the
  remaining lines), via the preserved `ContentBlock.lines` (D-DRAFT-3). Falls
  back to empty title + whole-block `text` when a block has no `lines`.
- **`build_context` profile knob (generic, all MNOs):** `"none" | "path" |
  "path_and_content"`. Stamped onto `RequirementTree.build_context`. Rendering
  (settled with the user):
  - `path` → a single-line breadcrumb wrapped in a label:
    `[Context: 5 Bands > 5.1 Frequency > 5.1.2 LTE]` (number + title per hop, no
    bodies).
  - `path_and_content` → one bracketed header per ancestor followed by its body,
    top-down: `[5 Bands]` / `<5 body>` / `[5.1 Frequency]` / `<5.1 body>` /
    `[5.1.2 LTE]` / `<5.1.2 body>`.
- **`build_context_string(parent_section, section_index, mode)`** — one shared
  pure helper (in `parser/structural_parser.py`), self-labeling per the formats
  above. Anchors on `parent_section` (works in both models: leading-id's is the
  enclosing section; heading-mode's is the parent, excluding the requirement's
  own section). The **SIRA adapter** and the **NORA chunk builder** call it at
  emit time from the section nodes already in the tree, baking context into
  corpus rows / chunks. **NORA suppresses `path`** (the existing `[Path: …]`
  breadcrumb already carries it; emitting the numbered block too would
  duplicate) and emits only for `path_and_content` — which adds ancestor section
  *content* nothing else in the chunk provides. SIRA (no `[Path: …]`) emits for
  both `path` and `path_and_content`.
- **`Requirement.context`** stays as a field (the materialized shape) but is
  **left empty in the parsed tree** — context lives in the *derived* indexes
  (BEIR corpus, chunks), where self-contained rows are expected, not in the
  source-of-truth tree.

**Why:** Duplication is fine in derived retrieval indexes (each row self-
contained) but not in the source tree, which is inspected and re-run from and
must stay compact. The `path` vs `path_and_content` knob lets heading-mode
corpora (e.g. `bs_d7a2c81f` → `path`) get a lightweight breadcrumb while
leading-id corpora (`bs_5114ac92` → `path_and_content`) get the full section
bodies they need — generic across MNOs. A **separate** `context` (not prepended
into `text`) keeps requirement body vs inherited context distinguishable.
Rejected: materializing context per requirement in the tree (bloat);
per-plan-split tree files (essentially D-DRAFT-1 Option A — separate decision,
and it doesn't fix the duplication); splitting the title by font color (this PDF
doesn't expose it — D-DRAFT-3 context).

**Consequences:**
- `Requirement` gains `context` (additive, empty in tree). New profile field
  `build_context` (default `"none"` → no-op for existing profiles).
  `RequirementTree.build_context` stamped from the profile.
- Title/body split is `leading_id_body`-only (no-op for heading mode → MNO-A
  unchanged). Context assembly is generic (driven by `build_context`).
- Both consumers wired (done): **SIRA adapter** (`_build_text` appends the
  helper output as a context block in each corpus row) and **NORA chunk builder**
  (`_build_chunk_text` appends the `path_and_content` block from a per-tree
  `{section_number: (title, body)}` index; coexists with the pre-existing
  `[Path: …]` and `[Parent context: …]` blocks).
- A **multi-line requirement title** still bleeds its overflow into `text` (no
  per-span signal) — deferred. If a **section heading merges with its intro** in
  one block, that run-on carries into the context heading label — a heading-path
  `lines` split is a possible follow-up.

_Promoted from strand: multi-mno-nora on 2026-06-28._

## D-096: Per-cell stage-output layout + universal `(MNO, MMMYYYY)` cell convention
**Status**: Active · **Date**: 2026-06-28.

**Context:** The pipeline is single-`--profile`, flat-output (`out/<stage>/*`).
The strand goal is multi-MNO / multi-release ingestion (full **and**
incremental). The `multi-mno-sira` strand already organizes multi-MNO data as
`(MNO, release)` **cells** (SIRA D-DRAFT-3..6); NORA should share that unit and
vocabulary. `infer_metadata_from_path` already derives `(mno, release)` from
`input/<mno>/<release>/`. The last free-form corpus (Verizon Open Access at
`input/VZW/OA-baseline/`) is being promoted to a real cell
(`input/VZW-OA/Feb2026/`), removing the only non-MMMYYYY holdout.

**Decision:** The **`(MNO, release)` cell** is NORA's unit of layout, keyed on the
input convention `input/<MNO>/<MMMYYYY>/` (mirrors SIRA D-DRAFT-5 — the dir name
is both label and sort key, `Feb2026 → 2026-02`). Stage outputs split into two
classes:
- **Per-cell** — `out/<stage>/<mno>/<rel>/`: **extract, profile, parse, resolve,
  vectorstore**.
- **Global** — `out/<stage>/`: **standards, taxonomy, graph, eval**.

MMMYYYY is **universal and validated unconditionally** (fail-loud at ingest) via a
shared **core** util — `release_key(name) -> (label, order_key)`, raising on
non-MMMYYYY — that `infer_metadata_from_path` calls. **Verizon OA is treated as
its own MNO** `VZW-OA`, release `Feb2026`.

**Why:** The cell is the consistent unit across NORA + SIRA (same vocabulary, same
ordering, same provenance). Directory-driven partitioning removes all
metadata-grouping logic from parse — the directory *is* the partition. Per-cell
for stages whose output is document/retrieval-scoped (extract/profile/parse/
resolve/vectorstore); global for the cross-cell KG layer (graph), its shared
inputs (taxonomy, standards), and eval. Promoting OA to a real cell makes MMMYYYY
**universal**, which is what lets validation be **unconditional in core** — no
cell-mode gate — because there is no longer any free-form corpus to protect.
**This supersedes SIRA D-DRAFT-12's placement:** that decision kept MMMYYYY
validation sandbox-side *solely* to avoid breaking the free-form `OA-baseline`;
once OA is migrated, the protection is obsolete, the convention is universal, and
the logic belongs in a shared **core** util (module boundary preserved — sandbox
→ core; SIRA's `sira_preflight` calls the same util). Rejected: flat dirs +
parse-time metadata grouping (more parse logic, no structural isolation); per-MNO
(not per-cell) directories (loses the release axis that release-diff needs — SIRA
D-DRAFT-3); cell-mode-gated validation (unnecessary once OA migrates to MMMYYYY).

**Consequences:**
- New per-cell directory tree under `out/`; graph / taxonomy / standards / eval
  stay flat (global).
- Shared **core** util for MMMYYYY parse/validate/order; `infer_metadata_from_path`
  enforces it. **Amends SIRA D-DRAFT-12** (sandbox-side placement → core util) —
  flag for the `multi-mno-sira` strand to reconcile at its land time.
- **One-time migration (work PC):** `input/VZW/OA-baseline/` →
  `input/VZW-OA/Feb2026/`, then re-extract → re-ingest. Cell key
  `(VZW, OA-baseline)` → `(VZW-OA, 2026-02)`; req_ids are unchanged (`VZ_REQ_…`),
  so eval ground-truth + the integration test hold, but mno/release-keyed chunks +
  graph nodes re-key (graph/vectorstore rebuild).
- `VZW-OA` cleanly separates the public OA corpus from a future *proprietary* VZW
  corpus (its own MNO) and keeps the Verizon name confined to the OA context per
  the redaction rule.
- Single-MNO is just a **one-cell** env (`--profile` still works); no free-form
  path remains anywhere.

_Promoted from strand: multi-mno-nora on 2026-06-28._

## D-097: Per-cell profile binding: `<env_dir>/profiles.json` → `out/profile/<mno>/<rel>/profile.json`
**Status**: Active · **Date**: 2026-06-28.

**Context:** With per-cell parse (D-DRAFT-6), each cell needs its own profile
(`VZW-OA` → `bs_d7a2c81f` heading model; MNO-B → `bs_5114ac92` leading-id model).
A single `--profile` per run can't express that.

**Decision:** A binding manifest `<env_dir>/profiles.json` maps
`(mno, release) → profile`. The **profile stage resolves bindings and
materializes each cell's resolved + substituted profile to
`out/profile/<mno>/<rel>/profile.json`**; parse reads each cell's profile from its
own directory. Resolution precedence per cell: `--profile` (one-off global
override) → exact `(mno, release)` → `(mno, "*")` → `default` → **fail loud**
(`PIP-E0xx`). `load_substituted_profile` runs per cell so each MNO's placeholder
mapping applies. A bare `--profile` synthesizes a one-cell wildcard binding.

```jsonc
{ "bindings": [ { "mno": "<mno>", "release": "*", "profile": "customizations/profiles/<id>.json" } ],
  "default": null }
```

**Why:** The binding lives with the env (reproducible, works in `--env` and
`--env-dir` modes) and mirrors the existing per-profile mapping-file pattern.
Materializing the resolved profile **per cell** keeps parse purely
directory-driven (read `out/profile/<cell>/profile.json`, parse
`out/extract/<cell>/`) and gives transparency — Parse-Review can show each cell's
effective profile. Fail-loud on an uncovered cell (vs. auto-profiling) because
these corpora use hand-authored profiles. Rejected: CLI-only `--profile-map` (not
reproducible); per-input-dir sidecars (scatters config across the runtime tree);
auto-`DocumentProfiler` per cell (wrong for hand-authored corpora).

**Consequences:**
- New `<env_dir>/profiles.json`; `ProfileBindings` loader/resolver (`env`);
  `EnvironmentConfig.profile_bindings` for `--env` mode.
- `run_profile` → resolve + validate + materialize per cell; `run_parse` reads the
  per-cell profile (no in-memory grouping).
- Back-compat preserved via wildcard synthesis (single-MNO `--profile` unchanged).

_Promoted from strand: multi-mno-nora on 2026-06-28._

## D-098: Incremental cell ingestion: per-cell stages skip/scope; global stages rebuild over all cells
**Status**: Active · **Date**: 2026-06-28.

**Context:** Beyond initial full ingestion, the strand must support **incremental**
adds — a new cell (new MNO or new release) dropped in later, ingested without
redoing existing cells. The per-cell layout (D-DRAFT-6) makes a cell's on-disk
outputs the natural state.

**Decision:** Per-cell stages (extract/profile/parse/resolve/vectorstore) are
**idempotent + scopable**:
- **Skip-if-present-and-unchanged** by default: a cell's per-cell outputs are
  reused when its inputs are unchanged. Parse stamps a **`profile_fingerprint`**
  (hash of the substituted profile) onto each tree, so a profile/mapping edit
  invalidates exactly that cell (mtime alone can't see a mapping edit).
- **`--mno` / `--release`** flags (comma-separable) scope the per-cell stages to
  specific cells; **`--force` / `--no-skip`** reprocesses regardless.
- **Global stages (taxonomy/graph/eval) always rebuild over all cells** — they
  must, to merge. Taxonomy cost is bounded by its fingerprint cache (D-DRAFT-9);
  graph is cheap; the vectorstore is per-cell, so only new cells embed.

Full and incremental are the **same command** — `run_cli --start extract --end
graph` (or `--end vectorstore`): full builds all cells; incremental skips
unchanged cells, builds only the new one, and rebuilds the global graph/taxonomy
over the union.

**Why:** Cell-presence + fingerprint is the state (no separate DB). One command
for both cases replaces the scratch-env-and-copy workaround in
`mno-b-spec.md`. Fingerprinting the **substituted** profile (not file mtime)
catches profile/mapping edits safely. Per-cell vectorstore means incremental
**embedding** falls out for free (only the new cell's store builds). Rejected: a
processed-docs state DB (redundant with on-disk cells); mtime-only skip (misses
mapping edits); incremental global graph (correctness risk; rebuild is cheap).

**Consequences:**
- `RequirementTree` gains `profile_fingerprint` (additive, serialized).
- Per-cell stages gain skip + `--mno` / `--release` / `--force`; global stages
  always run full.
- Supersedes the scratch-env workaround (the `mno-b-spec.md` runbook is updated at
  implementation time).

_Promoted from strand: multi-mno-nora on 2026-06-28._

## D-099: Global taxonomy + corpus-fingerprint cache + temperature=0
**Status**: Active · **Date**: 2026-06-28.

**Context:** `taxonomy` is the one global stage that is LLM-driven, expensive, and
**non-deterministic** (standing STATUS flag — a 3-run accuracy spread traced to
taxonomy producing different feature mappings → graph-topology shifts). With
incremental ingestion (D-DRAFT-8) every new-cell add would otherwise re-derive
over all cells. Yet a **single union** taxonomy is wanted: shared cross-MNO
features are what make comparison queries answerable (chosen over per-cell
taxonomy, which would need fuzzy cross-cell feature alignment).

**Decision:** Keep **one global** taxonomy (`out/taxonomy/taxonomy.json`) derived
over **all** cells, gated on a **corpus fingerprint** (hash of the contributing
tree set): reuse the cached taxonomy when the set is unchanged; re-derive over the
union only when it changes; run the LLM at **temperature=0**.

**Why:** A union taxonomy gives the shared feature space the global graph links
every cell's reqs to — so "compare VZW-OA vs TMO on IMS registration" works
without feature alignment. The fingerprint cache makes incremental adds cheap and
stops silent feature drift on unrelated adds; temp=0 makes a forced re-derivation
reproducible. Rejected: per-cell taxonomy + merge (loses shared features, adds a
fuzzy alignment problem); always re-derive (cost + non-determinism); dropping
union taxonomy (breaks comparison queries).

**Consequences:**
- `run_taxonomy` gains a corpus-fingerprint check + cache; the taxonomy LLM call
  is temp=0.
- Incremental runs that don't change the tree set skip the taxonomy LLM entirely;
  `--skip-taxonomy` / `--rag-only` remain valid escapes.
- A deliberate re-derivation (prompt change) needs a cache-bust flag rather than
  manual file deletion.
- Resolves the standing taxonomy-non-determinism STATUS flag for the multi-MNO
  path.

_Promoted from strand: multi-mno-nora on 2026-06-28._

## D-100: MNO-scoping is structural via per-cell resolve; cross-cell relations live in the global graph
**Status**: Active · **Date**: 2026-06-28.

**Context:** D-DRAFT-6 runs `resolve` per cell (`out/resolve/<mno>/<rel>/`) over
only that cell's trees. Cross-plan references are id-shaped
(`<PREFIX>-<PLAN>-<NUM>` / `<MNO>_REQ_<PLAN>_<NUM>`) and plan codes / numbers are
**not** globally unique across MNOs or releases — two cells can carry the same
plan or number.

**Decision:** Cross-reference resolution is **structurally cell-scoped** — the
resolver runs per cell over that cell's trees, so it can never match a reference
across MNOs or releases. No explicit mno-filter in resolver code is needed; the
per-cell layout enforces it. **Cross-cell relationships** (release-diff, shared
features, cross-MNO comparison) are **not** resolver concerns — they live in the
**global graph**. **Assumption:** cross-references stay within a `(mno, release)`
cell; if a release ever cites a *prior* release of the same MNO, resolve would
widen to per-MNO-across-release (flagged here, not built).

**Why:** Per-cell resolve makes the multi-MNO no-leak property a **layout
invariant** rather than resolver code that could regress. Clean separation:
intra-cell cross-refs = `resolve`; inter-cell relations = `graph`. Replaces the
earlier "add an mno filter to the resolver candidate set" decision (now
unnecessary). Rejected: global resolve + mno filter (more code, regressable);
assuming globally-unique ids (false across cells).

**Consequences:**
- `resolve` becomes a per-cell loop (no resolver-internal change beyond running
  per directory).
- New test: same plan/number present in two cells resolve independently (no leak).
- The **cross-release-reference** assumption is a watch item — revisit if a corpus
  is found to cite across releases of one MNO.

_Promoted from strand: multi-mno-nora on 2026-06-28._

## D-101: SIRA adapter reads nested `out/parse/<mno>/<rel>/`
**Status**: Active · **Date**: 2026-06-28.

**Context:** The SIRA adapter (`sandbox/adapter/nora_to_beir.py`) discovers NORA
parse output via `_load_trees` globbing `<env>/out/parse/*_tree.json` (flat).
D-DRAFT-6 nests parse output to `out/parse/<mno>/<rel>/*_tree.json`.

**Decision:** Update the adapter's `_load_trees` to walk the **nested**
`out/parse/<mno>/<rel>/*_tree.json` layout. The adapter's downstream `(mno,
release)` partitioning + `--multi-cell` cell emission are unchanged (trees still
carry mno/release) — only the discovery glob changes.

**Why:** The layout change is a NORA-side decision (D-DRAFT-6) that the SIRA
adapter consumes; they must move in lockstep or the adapter silently reads zero
trees. Walking the nested dirs is also more direct (the cell *is* the directory).
Rejected: keeping parse output flat just for the adapter (defeats D-DRAFT-6's
per-cell layout); a shim globbing both layouts (carries the old layout forward
needlessly once NORA migrates).

**Consequences:**
- Cross-strand lockstep change — landing D-DRAFT-6 requires this adapter update in
  the same migration.
- `sandbox/adapter` stays informal (no MODULE.md, SIRA D-DRAFT-8); track via the
  SIRA journal. The `multi-mno-sira` strand should note the coupling.

_Promoted from strand: multi-mno-nora on 2026-06-28._

## D-102: Profile-driven exclusion of non-normative sections + trailing appendices (REFERENCES, traceability matrices)
**Status**: Active · **Date**: 2026-06-28.

**Context:** The MNO-A (heading model) corpus carries content that is
structurally a "requirement" to the parser but is **not normative** and badly
bloats RAG chunks (driving them past the embedder's 8000-char input limit):
(1) **REFERENCES / bibliography** sections — a titled heading (with a trailing
req_id) whose body is a citation list; (2) a **requirement→test-case
traceability appendix** — NOT a titled section: a marker line followed by a
section→req_id matrix and test-case tables (`Test Case Name | Test Plan Id | …`)
that the parser glued onto the **last real requirement's** `text` + `tables`
(no new req_id or heading breaks it). Both surfaced as oversize chunks during
multi-MNO work-PC verification. The existing per-doc `kind=remove` annotation
(D-061) is manual; these are systematic for the corpus and want a profile rule.

**Decision:** Two complementary profile-driven mechanisms (additive, empty =
no-op):
- **`exclude_section_pattern`** — regex matched on a section **title**. Matching
  sections + descendants are dropped from the parsed tree (never become
  Requirements or chunks). Runs **after** `reference_list_section_pattern`
  extraction, so a REFERENCES section still populates `reference_list_map`
  (citation resolution preserved) before being dropped from RAG. Generalizes the
  glossary drop into a shared `_drop_section_subtree` helper.
- **`content_end_marker`** — regex matched per **body line**, for a trailing
  appendix glued onto a requirement (no heading to match). For a requirement
  whose text has a matching line, the parser truncates the text **before** that
  line and clears that requirement's `tables`/`images`. Symmetric to
  `content_start_section` (D-DRAFT-4, front cut).
- Both accept placeholders (`<TRACEABILITY>`) resolved from the work-PC mapping
  file. `bs_d7a2c81f`: `exclude_section_pattern` = `(?i)^\s*references\b`;
  `content_end_marker` = `<TRACEABILITY>`.

**Why:** Title-based exclusion is right for *sectioned* non-requirements
(REFERENCES has a heading); a *content marker* is required for the traceability
appendix because it has no heading — it's trailing text+tables on the last req,
so nothing title-based can reach it. Splitting into two knobs keeps each
mechanism simple and each matches what it targets. Extracting references to
`reference_list_map` *before* dropping keeps future indirect-citation resolution
free. Rejected: manual `kind=remove` per doc (not systematic); a single combined
knob (the two cases are structurally different — title vs body line); hard
char-cap truncation of all chunks (would silently drop *legitimate* long
requirements — kept those, see Consequences).

**Consequences:**
- New profile fields `exclude_section_pattern`, `content_end_marker` (both
  empty-default no-ops; substituted like other regex fields). New parser passes
  `_drop_excluded_sections` + `_apply_content_end_marker`; shared
  `_drop_section_subtree` (also backs the glossary drop). MODULE.md updated
  (parser, profiler).
- `content_end_marker` clears **all** of a marked requirement's tables/images on
  the assumption the marker begins a trailing appendix — over-drops if a legit
  table ever *precedes* the marker in the same req. Safe for this corpus (marker
  is a document-end delimiter); flagged if another corpus differs.
- **Legitimately long requirements** (category 1 — table-heavy normative reqs)
  are deliberately NOT truncated; they still exceed the embedder limit (vector
  on the prefix, full text stored). Chunk-splitting remains deferred (standing
  token-dense-chunks STATUS flag).
- Generic + reusable: any corpus can name its non-normative sections / trailing
  appendices via profile, no code change.

<!-- D-DRAFT-15 intentionally unused: code comments + multi-mno-sira cross-refs
     pegged NORA's balanced pin at D-DRAFT-16 before this strand's drafts caught
     up (they ran to 13). Path-B took 14; balanced pin took 16 to match the
     existing references. All renumber to canonical D-XXX at land. -->

_Promoted from strand: multi-mno-nora on 2026-06-28._

## D-103: Path-B: LLM-select synthesis (drop the reranker; the LLM picks relevant chunks)
**Status**: Active · **Date**: 2026-06-28.

**Context:** Even with the rerank-413 and balance fixes, a cross-MNO band query
still missed the source-of-truth MNO-A chunk. Root cause is fundamental to the
cross-encoder reranker: it scores surface query↔passage similarity and does not
bridge telecom term variants — the chunk says "SA NR", the query says "5G", and
the reranker scores it low and drops it (while picking keyword-matching but
irrelevant chunks). Telecom-pretrained LLMs (Qwen3) handle that association
natively. The DGX was provisioned to 128K context to make a stuff-the-context
approach feasible.

**Decision:** Add `NORA_SIRA_SYNTH_MODE=llm-select` (default `rerank-pin` =
unchanged). Path-B drops the cross-encoder entirely: fetch all BM25 candidates
with full text (SIRA `text_chars`, rerank off so the top_k cut is BM25), then on
the NORA side round-robin-pack them across cells under a token budget
(`NORA_SIRA_SYNTH_TOKEN_BUDGET`, default 120K), group the context by
(MNO, release) with headers, and feed everything to the LLM in ONE call that
both SELECTS the relevant chunks (instructed that "SA NR" ≡ "5G NR standalone",
band aliases, etc.) and SYNTHESIZES. Citations are extracted corpus-agnostically
by matching the packed candidates' actual req_ids against the answer (the
synthesizer's regex only matched `VZ_REQ_*`). Implemented as a dedicated lane in
`playground.py`, bypassing the graph-heavy `pipeline.query`.

**Why:** The reranker's term-variant blindness is a hard limit, not a tuning
knob — no fusion/pin change fixes a chunk dropped before fusion. An LLM is robust
to granularity + terminology, and at 128K we can give it a bounded, balanced
candidate set and let it do relevance from content. Rejected: more reranker
tuning (can't bridge ontology); pure per-cell balance (the right chunk was never
scored); a smaller-context stuff (band tables don't fit). Kept rerank-pin as the
default + fallback behind the flag.

**Consequences:**
- New Path-B helpers (`_pack_pathb`, `_build_pathb_context`,
  `_pathb_synthesize`, `_pathb_extract_citations`, `_run_pathb_lane`) + the
  `_PATHB_*` / `_SYNTH_*` env knobs; SIRA service gained `text_chars`.
- Cost/latency shifts from cheap rerank to one large-context LLM call — eval-grade,
  not production throughput; `synth_ms` surfaces it.
- Citations depend on the LLM writing req_ids verbatim; a paraphrased id is missed.
- Requires the SIRA service run with `NORA_SIRA_RERANK_ENABLED=false` so the
  returned top_k is BM25 (else the reranker re-introduces the drop).
- Open: whether Path-B replaces the rerank lane or stays opt-in (decide after eval).

_Promoted from strand: multi-mno-nora on 2026-06-28._

## D-104: Per-model reasoning sentinel for select-synth (untagged chain-of-thought)
**Status**: Active · **Date**: 2026-06-28.

**Context:** select-synth makes one LLM call that selects + synthesizes. A
proprietary "thinking" LLM emitted its chain-of-thought *into* the answer
content; Qwen3/Gemma did not (they skip thinking natively or split it into a
`reasoning_content` field we don't read). An opt-in raw dump
(`NORA_LLM_DEBUG_RAW`) proved the proprietary model's CoT is **untagged** — no
`<think>` tags, empty `reasoning_content` — so tag/pattern stripping has nothing
to match.

**Decision:** A final-answer **sentinel**, gated per model by
`NORA_LLM_REASONING_SENTINEL` (default off). When on: (a) the select-synth
system prompt instructs the model to print a line containing exactly
`===FINAL_ANSWER===` before its answer, and (b)
`OpenAICompatibleProvider._strip_reasoning` drops everything up to the *last*
marker occurrence. The marker constant and the flag live in the provider and are
imported by the prompt builder, so the instruction and the strip cannot drift.
`<think>`-tag stripping stays always-on (harmless when absent). The toggle is
read per process → naturally per-stack; `run_stack.sh` exposes
`--reasoning-sentinel`.

**Why:** Untagged CoT can't be split structurally and the boundary isn't
otherwise discoverable, so we *make* it explicit via the prompt. Per-model
opt-in (not global) because most models skip thinking natively and shouldn't
have output reshaped — a stray marker inside reasoning is mitigated by taking
the LAST occurrence. Chosen over: native thinking-disable
(`chat_template_kwargs={"enable_thinking": false}` / `/no_think`) — cleanest but
depends on the proprietary server's unknown API and needs provider `extra_body`
support; and over relying on `reasoning_content` — the endpoint leaves it empty.
A brief configurable-marker-*text* design was reverted once the user clarified
they wanted an on/off switch, not a configurable string.

**Consequences:**
- Provider gains `FINAL_ANSWER_MARKER` (fixed) + `REASONING_SENTINEL_ENABLED`
  (env) + sentinel logic in `_strip_reasoning`; the select-synth prompt appends
  the instruction only when enabled; startup log shows `reasoning_sentinel=<bool>`.
- **Token-waste / truncation risk:** the model still *generates* the thinking
  (counts against `max_tokens`); long reasoning could truncate the answer. The
  cleaner native-disable fix is deferred (needs provider `extra_body`).
- A/B integrity: enabling the sentinel only on the stack that needs it keeps
  retrieval+synthesis otherwise identical across the Qwen3-vs-proprietary
  comparison.
- Renumbers to a canonical D-XXX at land.

_Promoted from strand: multi-mno-nora on 2026-06-28._

## D-105: Multi-MNO SIRA retrieval: per-MNO BM25 indexes + LLM-rerank fusion (design C)
**Status**: Active · **Date**: 2026-07-03.

**Context:** SIRA's batch pipeline and runtime service are single-corpus —
one BM25 index, one dataset loaded at service startup. Extending to multiple
MNOs (each with multiple releases) and supporting cross-MNO comparison
queries ("compare VoWiFi of A and B") forces a corpus-slicing decision.
BM25's IDF is corpus-wide, and SIRA's doc-enrichment DF filter (the
discriminative-term invariant, plan-aware-sira D-DRAFT-1) is also
corpus-wide — so how the corpus is partitioned changes the retrieval
statistics. Three options were weighed:

- **A — Union index** (one BM25 over all MNO×release, MNO as metadata):
  cross-MNO is natural, but IDF/DF blend across MNOs (a term discriminative
  within MNO A but common across the union gets the wrong DF → enrichment
  mis-fires, single-MNO precision degrades), and adding an MNO perturbs DF
  for every existing doc → forces whole-union re-enrichment.
- **B — Per-MNO indexes**: clean per-MNO IDF/DF, single-MNO precision
  preserved, MNO-add doesn't perturb others — but cross-MNO comparison must
  merge BM25 scores that aren't comparable across indexes (different IDF
  scales).
- **C — Per-MNO indexes + LLM-rerank fusion**: B's retrieval isolation,
  with SIRA's existing LLM reranker (absolute 0-100 relevance scoring,
  corpus-independent) as the cross-MNO merge layer.

**Decision:** Adopt **design C**. Retrieval is per-MNO (clean stats,
isolation); cross-MNO queries retrieve top-K per MNO, merge the candidate
pools, and LLM-rerank the union to produce comparably-scored, balanced
material for the synthesizer.

**Why:** C isolates the BM25-statistics problem (B's win — no shared IDF/DF
to blend or perturb) while solving B's score-incomparability problem with a
mechanism SIRA already has. The LLM reranker scores `(query, doc)` relevance
absolutely, not relative to a corpus, so its scores merge cleanly across
MNOs where raw BM25 scores cannot. C also explains the "balanced retrieval"
requirement (FR-multi-3) mechanically: retrieving top-K *per MNO before* the
union guarantees neither MNO is starved by vocabulary skew. A and B both
rejected — A for statistics-blending + re-enrichment cascade on MNO-add; B
for unsolved cross-MNO score fusion.

**Consequences:**
- The LLM reranker becomes **load-bearing** for cross-MNO queries — it can
  no longer be disabled (`NORA_SIRA_RERANK_ENABLED=false`) for that query
  class, because rerank *is* the fusion mechanism. This raises the priority
  of the parked dedicated-`/rerank` backend TODO (rerank latency was already
  the bottleneck; now mandatory for a whole query class).
- Score-fusion quality depends on rerank scores being genuinely
  MNO-independent — ties directly to the score-normalization concern in the
  dedicated-`/rerank` TODO.
- Per-MNO indexes mean per-MNO enrichment runs (more orchestration in
  `sandbox/sira_configs` + the adapter).
- Open sub-questions deferred to architecture phase: per-MNO-index
  granularity (per MNO, or per MNO×release?); whether query-scope extraction
  reuses NORA's analyzer or is SIRA-local; the concrete merge-then-rerank
  flow in `sandbox/sira_query`.
- This is an architecture decision made during requirements phase —
  re-confirmed at landing (2026-07-03).

_Promoted from strand: multi-mno-sira on 2026-07-03._

## D-106: Release resolution for multi-MNO queries: independent latest-of-each-MNO when unspecified
**Status**: Active · **Date**: 2026-07-03.

**Context:** Multi-MNO queries can name a release or not. NORA's FR-10
already resolves "latest → newest release in scope" for the single-corpus
case, but cross-MNO comparison introduces a wrinkle: "compare A and B" with
no release named could mean (i) global-latest release label across both,
(ii) matching/aligned releases (both Q3-2025), or (iii) each MNO's own latest
independently. Different MNOs publish on different cadences, so their
"latest" release labels often differ.

**Decision:** When no release is named, **each MNO in scope resolves
independently to its own latest release** (so "compare A and B" → A-latest vs
B-latest, which may be different release labels). When releases are named
explicitly, use exactly those. FR-multi-5 additionally requires the resolved
`(mno, release)` per lane to be **surfaced** in the /test response so the
user can see when a comparison spans a release gap.

**Why:** Comparing each MNO's *current* state is the most common analyst
intent ("how does A's current spec compare to B's current spec?").
Global-latest (option i) is incoherent across independent release-numbering
schemes. Matching-release (option ii) is often impossible — B may have no
release in the same quarter as A — and over-constrains the common case.
Per-MNO-latest is the natural default; the surfacing requirement (FR-multi-5)
mitigates the one real risk of this choice — that the user silently compares
across a release gap (A's Q4-2025 vs B's Q1-2024) without realizing it.

**Consequences:**
- A comparison can span mismatched release vintages; correctness depends on
  the user reading the surfaced `(mno, release)` labels. Accepted because
  the alternative (forcing release-alignment) breaks the common case.
- The runtime service must track each corpus's release ordering to compute
  "latest" per MNO — a small metadata requirement on the adapter output
  (release must be orderable, not just a free-form string).
- If a future use case needs release-aligned comparison ("compare A and B as
  of the same quarter"), it's an additive query mode, not a change to this
  default.

_Promoted from strand: multi-mno-sira on 2026-07-03._

## D-107: Index granularity: per-(MNO, release) cell, not per-MNO
**Status**: Active · **Date**: 2026-07-03.

**Context:** Design C (D-105) settled on per-MNO BM25 indexes + LLM-rerank
fusion. But "per-MNO" left the release axis unspecified — one index per MNO
(all releases mixed) or one per (MNO, release) cell? BM25 IDF and the
doc-enrichment DF filter are corpus-wide, so the release axis has the same
statistics-blending exposure the MNO axis did.

**Decision:** The unit of indexing is the **(MNO, release) cell** — one BM25
index per cell, not per MNO.

**Why:** FR-9 lists "release diff" as one of the 8 query types ("how did MNO
A's VoWiFi change from R2 to R3?"). That is the same shape as cross-MNO
comparison — balanced retrieval from each side, fused at rerank — but on the
release axis. Per-MNO indexing (releases mixed in one index) would hit the
exact vocabulary-skew-starvation problem design C was built to avoid, now
between releases of one MNO; worse, R2 and R3 of the same spec are
near-duplicates, so mixed IDF buries the low-frequency *diff* signal that a
release-diff query is asking for. Per-(MNO, release) makes release-diff fall
out of the same isolate-then-fuse machinery as cross-MNO — one mechanism,
both query types. Per-MNO would be design C on the MNO axis but union-index
on the release axis: internally inconsistent. Cost objection (N_MNO ×
N_release indexes, each needing a ~13h doc-enrichment pass) is mitigated by
the existing incremental-enrichment machinery (`sira_incremental.py`
content-hash resume): releases are incremental (R3 ⊃ mostly-unchanged R2), so
per-cell enrichment costs "enrich the delta," not full re-enrich per release.

**Consequences:**
- More indexes + enrichment runs than per-MNO, but cost scales with actual
  change (incremental), not cell count.
- The (MNO, release) cell becomes the consistent unit of layout, indexing,
  enrichment, ordering (D-109), provenance (D-108), and citation.
- "Latest release" resolution becomes structural (pick the max-ordered cell
  per MNO), not a within-index metadata filter — see D-109.
- Per-MNO-only queries that intentionally span all releases ("what has MNO B
  ever supported?") must query all of B's cells and merge — same fusion
  machinery, rare query, handled.

_Promoted from strand: multi-mno-sira on 2026-07-03._

## D-108: Cross-cell chunk identity: composite `(MNO, release, doc_id)` with structural provenance
**Status**: Active · **Date**: 2026-07-03.

**Context:** With per-(MNO, release) cells (D-107), cross-cell queries
(cross-MNO comparison, release-diff) retrieve from multiple cells and merge
into one candidate pool for LLM-rerank fusion. The corpus rows are
`{_id, title, text}` with no metadata field, and BM25 doesn't read metadata —
so provenance can't live in the row. Meanwhile the same `req_id` legitimately
exists in multiple cells (VZW Feb2026 and a future VZW Aug2026 both have
`req:LTEAT:5.1` — the same spec evolving across releases).

**Decision:** The cross-cell chunk identity is the **composite
`(MNO, release, doc_id)`**, not `doc_id` alone. Provenance is **structural** —
a chunk's origin cell IS its provenance, attached to the chunk at retrieval
time (the chunk came out of that cell's index). No `doc_id` prefixing; within
a cell, `doc_id`s stay exactly as they are (`req:...`, `doc:<plan>`,
`section:<plan>:<num>`). The composite identity only matters at and above the
merge layer.

**Why:** Merging on `doc_id` alone would collapse two genuinely distinct
chunks (R2's vs R3's `req:LTEAT:5.1`), break citation resolution (which
release did this answer come from?), and defeat release-diff at the identity
layer — a release-diff query's entire point is comparing the same req_id
across releases, which is impossible if they share one identity. Structural
provenance (vs doc_id prefixing) keeps within-cell doc_ids untouched, so the
existing doc:/section: fan-out composes unchanged: pointers are cell-local
req_ids, fan-out happens within-cell, and fanned-out chunks inherit the cell
provenance. The composite is also exactly what FR-multi-5 surfaces in the UI
(`(mno, release)` per lane) and what cross-comparison citations need.

**Consequences:**
- The merge layer in `sandbox/sira_query` must carry `(mno, release)` on every
  retrieved chunk and key dedup/citation on the composite.
- Provenance is added at retrieval, not baked into persisted doc_ids — so a
  cell can be re-indexed without rewriting ids, and a doc_id is only globally
  meaningful when paired with its cell.
- Any cross-cell merge structure (the candidate pool, the rerank input, the
  returned results) must thread the composite through; a code path that drops
  the cell tag silently corrupts cross-comparison answers.

_Promoted from strand: multi-mno-sira on 2026-07-03._

## D-109: Release identity & ordering from the input directory convention `<MNO>/<MMMYYYY>`
**Status**: Active · **Date**: 2026-07-03.

**Context:** "Latest release" resolution (D-106) requires release labels
to be orderable. The parse tree carries three candidate fields: `release`
(the input dir name, currently `OA-baseline`), and `release_date` (a
free-form profile-regex capture of whatever the document author typed after
"Release Date:", currently `"February 2026"`). Grounding showed
`release_date` is unbounded — the capture group is `(.+?)`, normalized by no
pipeline stage, so different MNOs/documents could write any date format.
`infer_metadata_from_path` already derives `mno`/`release` from the input
PATH, not document content.

**Decision:** Release identity and ordering come from the **input directory
name convention**: `<env_dir>/input/<MNO>/<MMMYYYY>/`, where MMM is a
3-letter title-case month (Jan..Dec) and YYYY a 4-digit year (e.g. `Feb2026`).
The directory name IS both the release identity (label) and the sort key:
`Feb2026` → order key `(2026, 02)`, stored ISO as `2026-02`; "latest per MNO"
= max order key. Non-matching directories are rejected **fail-loud at ingest**
(in/beside `infer_metadata_from_path`). The document `release_date` field is
**demoted to display-only** — it may appear as a human label in FR-multi-5's
UI alongside the structured `Feb2026`, but it never drives ordering or
resolution.

**Why:** Three options were weighed. (a) Parse `release_date` → ISO: rejected
— bets on robustly parsing an unbounded free-form input, the exact
silent-mis-order failure mode we most want to avoid. (b) Explicit
human-supplied order key in the per-MNO profile: workable (fits the existing
profile→tree→adapter flow) but adds a profile field and parsing burden. (c)
Input directory convention: chosen — the directory name constrains the format
at the filesystem level (validated at ingest, earliest/clearest place),
requires no new code (`infer_metadata_from_path` already reads the path), no
profile change, and is orderable by construction. The operator who places the
files knows the release date; encoding it in the dir name is the natural,
already-required act. Resolves the open consequence left in D-106
("release must be orderable, not just a free-form string").

**Consequences:**
- `release_date` is a trap for future code — the tree carries both it and the
  dir-derived release, and the free-form one is the tempting-but-wrong sort
  key. Ordering must strictly use the `MMMYYYY` directory name.
- Operator burden: input dirs must follow `MMMYYYY` exactly; a typo
  (`Feb-2026`, `February2026`) is rejected at ingest rather than silently
  mis-sorted. Existing `input/VZW/OA-baseline/` must be renamed +
  re-extracted on the work PC to become a valid cell (a one-time migration,
  not design work).
- Month-granularity ordering assumes ≤1 release per (MNO, month) — true for
  these quarterly-cadence corpora. If an MNO ever ships twice in one month,
  the convention needs a discriminator; YAGNI now.

_Promoted from strand: multi-mno-sira on 2026-07-03._

## D-110: Orchestration: NORA-side cell-loop (batch) + service cell-dict (runtime), SIRA unchanged
**Status**: Active · **Date**: 2026-07-03.

**Context:** Two consumers must iterate cells. The batch pipeline
(`run_pipeline.py`) runs one dataset per invocation. The runtime service
(`sandbox/sira_query/service.py`) loads a single module-global `_bm25`. Both
need to become cell-aware.

**Decision:** **Batch** — a thin NORA-side orchestrator enumerates cells under
`db_root` and invokes `run_pipeline.py` once per cell with `data.name=<cell>`;
`run_pipeline.py` itself is unchanged. **Runtime** — the service's `_bm25`
global becomes a `dict[cell_key → CellState]`; the service enumerates cells
from `db_root` at startup (dirs matching `<mno>__<MMMYYYY>`) and loads each
cell's BM25 index + doc enrichments. Query-time scope resolution (FR-multi-6)
selects the target cell set; retrieve→tag→merge→rerank (D-108) operates
over them.

**Why:** Keeping `run_pipeline.py` unchanged is consistent with the
patch-don't-fork posture established for SIRA (the per-stage-routing patch) —
a NORA-side loop shelling out per cell adds no upstream divergence and
composes with incremental enrichment per cell. Patching `run_pipeline.py` to
accept a cell list was the alternative — rejected as more invasive for no
gain, since per-cell invocation already does exactly what's needed. The
service cell-dict is the minimal change that makes multi-cell retrieval
possible while keeping each cell's BM25 statistics isolated (the whole point
of D-107).

**Consequences:**
- A new NORA-side orchestrator script (location TBD —
  `sandbox/sira_multi.py`, or extend `sira_incremental.py`) becomes the batch
  entry point for multi-cell ingestion.
- The service's startup cost and memory scale with cell count (N indexes
  loaded). Acceptable at expected cell counts (a few MNOs × a few releases);
  revisit lazy-loading if it ever grows large.
- The service's per-query path gains scope-resolution + multi-cell retrieval +
  merge before the existing rerank — the concrete shape (and whether it leans
  on the dedicated-/rerank backend TODO) is the next architecture question.

_Promoted from strand: multi-mno-sira on 2026-07-03._

## D-111: SIRA sandbox modules stay informal (no MODULE.md) for now — Option 1
**Status**: Active · **Date**: 2026-07-03.

**Context:** Three of this strand's four target modules
(`sandbox/adapter`, `sandbox/sira_configs`, `sandbox/sira_query`) live under
`sandbox/`, which `structure-conventions.md` does not cover — only
`core/src/` and `customizations/` directories get MODULE.md contracts. The
architecture-phase persona assumes doc-first MODULE.md curation with
requirements traceability. So most of this strand's design work lands outside
the formalism COMPACT's architecture rigor expects.

**Decision (Option 1):** Keep the SIRA sandbox modules **informal** — capture
their architecture as strand journal entries + draft decisions, not as
MODULE.md contracts. Only `web` (a real `core/src/` module) gets MODULE.md
treatment when its turn comes. Defer Option 2 (promote the SIRA sandbox
modules to first-class with their own MODULE.md, extending
`structure-conventions.md` to cover a `sandbox/` module class) until
multi-MNO/multi-release SIRA ships and proves durable.

**Why:** The SIRA sandbox is research/integration code tracking an upstream
clone; it has deliberately avoided MODULE.md formalism so far. Forcing
doc-first contracts onto a still-moving design (the granularity, fusion, and
orchestration shapes are mid-spike) is premature — the contracts would churn
faster than they'd stabilize. Designing in the journal/decisions-draft keeps
the reasoning captured and auditable without paying contract-maintenance cost
on a moving target. Promotion (Option 2) is the right graduation step once the
subsystem is durable, not before.

**Consequences:**
- This strand's `sandbox/` design lives in the strand journal + decisions-draft,
  not in MODULE.md — `drift-check design` won't audit it (acceptable; it's
  not yet a contract).
- Extending `structure-conventions.md` for a `sandbox/` module class is
  deferred work, tracked as the eventual Option 2.
- When Option 2 lands, the journal/decisions-draft become the source material
  for the new MODULE.md contracts — so capturing them richly now pays off
  later.

_Promoted from strand: multi-mno-sira on 2026-07-03._

## D-112: Query-scope extraction: reuse NORA's analyzer with standard LLM-or-fallback selection
**Status**: Active · **Date**: 2026-07-03.

**Context:** Multi-MNO SIRA needs to extract MNO + release scope + query type
from the natural-language query (FR-9/FR-10) to drive cell resolution. NORA's
`core/src/query/analyzer.py` already does this in two forms —
`LLMQueryAnalyzer` (prompts an LLM, returns `mnos`/`releases` lists +
`query_type` incl. `release_diff`, self-falls-back to Mock on parse failure,
more accurate) and `MockQueryAnalyzer` (keyword/regex, no LLM; `_MNO_ALIASES`
maps verizon/vzw/vz→VZW etc., matching our cell MNO identity). The SIRA query
service currently has no NORA `LLMProvider` (rerank goes via raw httpx), and
there is no central selector — `pipeline.py` defaults to `MockQueryAnalyzer()`
and accepts injection.

**Decision:** Reuse NORA's analyzer rather than building a SIRA-local parser,
and select it by the standard rule: **LLM configured → `LLMQueryAnalyzer`;
not configured → `MockQueryAnalyzer` fallback** — the same configured-LLM-or-
mock posture as the rest of NORA, not a rule-based carve-out for query
analysis. This requires (a) a small selection helper
(`make_query_analyzer(llm_provider | None)`) in `core/src/query` — none exists
today; (b) the SIRA service constructing a NORA `LLMProvider` from its
**primary** LLM config (the shim / `NORA_LLM_*` endpoint used for enrichment,
NOT the rerank-override LLM) to feed `LLMQueryAnalyzer`. Cell *resolution*
(`_resolve_cells`, D-113) stays SIRA-local; only *extraction* is reused.

**Why:** Single source of truth (the canonical MNO alias map → VZW/TMO/ATT
matches cell identity; a 4th MNO is one edit benefiting both NORA and SIRA).
Consistency + accuracy: when an LLM is available it should do analysis (the
user's standing rule), and `LLMQueryAnalyzer` handles oblique MNO references
and multi-release release-diff queries ("Oct 2025 to Feb 2026") that the Mock
regex cannot. The earlier "keep the LLM out of the scope front to save
latency" rationale was explicitly rejected by the user — analysis is one cheap
call on the primary (enrich-quality) LLM, not the high-volume rerank path, so
quality/consistency outweighs the saved call. A SIRA-local parser was rejected
as duplicate-and-drift.

**Consequences:**
- New selection helper in `core/src/query` (a curated MODULE.md module) —
  small, and benefits NORA's native path too (which currently defaults to Mock
  even when an LLM is configured — a latent NORA gap this surfaces; flagged for
  the NORA side to address separately, not fixed in this strand).
- The SIRA service now uses **both** abstractions: NORA's `LLMProvider`
  Protocol (analysis) and raw httpx (rerank). Minor inconsistency; a future
  cleanup could route rerank through the Protocol, but out of scope here.
- `.finditer` fix on `_extract_releases` is now **fallback-only** (Mock path),
  no longer release-diff-blocking when an LLM is configured — lower urgency,
  still a core change for fallback consistency.
- Analysis quality now depends on the configured LLM's extraction reliability;
  `LLMQueryAnalyzer`'s built-in Mock fallback on parse failure means a bad LLM
  response degrades rather than breaks.

_Promoted from strand: multi-mno-sira on 2026-07-03._

## D-113: Fusion code shape: cell-loop generalization with `(cell_key, idx)` identity + `_resolve_cells`
**Status**: Active · **Date**: 2026-07-03.

**Context:** With per-(MNO, release) cells (D-107) each holding its own
BM25 index, a cross-cell query (cross-MNO comparison, release-diff) must
retrieve from multiple cells and combine the results into one ranking
("fusion"). The current `/sira-query` flow is single-index: expand →
`_bm25.search_with_expansion` → `hits[(idx, bm25)]` → LLM rerank → sort by
rerank score → top_k, where `idx` is a corpus index into the one `_bm25`.
BM25 scores from different cells aren't comparable (per-corpus IDF scales).

**Decision:** Fusion is the **generalization of the single-index flow to a
cell loop**, with the composite `(cell_key, idx)` threaded through as identity
(D-108). `_resolve_cells(intent, available)` (FR-multi-6 cross-product)
produces the target cell set; the handler retrieves per cell, tags each
candidate with its `cell_key`, merges into one pool, LLM-reranks the pool, and
sorts by rerank score. **Single-cell is N=1** — no separate cross-cell branch;
the three query shapes (scoped / cross-MNO / release-diff) collapse to one code
path differing only in what `_resolve_cells` returns. `_resolve_cells` returns
`(resolved, unresolved)` so requested-but-unavailable cells are surfaced
(fail-VISIBLE at query, mirroring D-109's fail-LOUD at ingest); the caller
errors only if `resolved` is empty. It lives in `sandbox/sira_query`
(cell concept is SIRA's), consuming the reused-from-core `QueryIntent`.

**Why (five embedded calls):**
1. **Fusion method = sort by rerank score, not RRF** — valid only because the
   LLM-as-judge reranker emits absolute 0-100 relevance (corpus-independent).
   RRF (rank-position fusion) was the obvious score-free alternative; rejected
   because the reranker's absolute scores are higher-quality for comparison.
   Coupling note: swapping to the dedicated cross-encoder `/rerank` backend
   would require score normalization (those scores aren't 0-100 absolute).
2. **Balanced retrieval = `per_cell_top_n` per cell, rerank the union**
   (FR-multi-3) — each cell gets full representation so neither MNO is starved
   by vocabulary skew. Cost: N_cells × per_cell_top_n rerank calls; make
   `per_cell_top_n` a knob for latency tuning.
3. **No cross-cell dedup** — the same `req_id` legitimately exists in two cells
   (release-diff: R2's vs R3's `req:LTEAT:5.1`) and BOTH must reach top_k. The
   composite `(cell_key, doc_id)` keeps them distinct; a naive doc_id dedup
   would silently break release-diff. Dedup only within a cell (fan-out
   handles it).
4. **Expand once, DF-filter per cell** — query enrichment is one query-level
   LLM call; the DF-filter of those phrases uses per-cell corpus statistics
   (a phrase discriminative in VZW may be common in TMO), so filtering runs
   per cell (cheap DF lookup, no extra LLM call).
5. **`_rerank_pool` reuses the existing batch/per-call rerank verbatim,
   re-keyed** on the `Candidate` (carrying `cell_key`) instead of a bare `idx`.
   The reranker never knows about cells — it scores `(query, text)` pairs and
   the pool threads provenance around it.

**Consequences:**
- The service's `_bm25` global becomes `dict[cell_key → CellState]`
  (D-110); retrieval, fan-out, rerank, and results all thread the
  composite identity. A code path that drops the `cell_key` silently corrupts
  cross-comparison answers (wrong-release citations, collapsed release-diff).
- Cross-cell rerank cost compounds (per-cell granularity × balanced retrieval)
  — the highest-leverage perf item is the dedicated-`/rerank` backend TODO.
- `_resolve_cells` ordering uses `_order_key` on the cell label (`MMMYYYY`),
  never the tree's free-form `release_date` (D-109 trap).
- Results carry `(mno, release, doc_id)` provenance for citation +
  FR-multi-5 UI surfacing; `_resolve_cells`'s `unresolved` list feeds the
  symmetric "requested but unavailable" surfacing.

_Promoted from strand: multi-mno-sira on 2026-07-03._

## D-114: Multi-cell query routing: preserve the legacy single-dataset handler, add multi-cell as a separate path
**Status**: Active · **Date**: 2026-07-03.

**Context:** D-110 specified the runtime `_bm25` global ->
`dict[cell_key -> CellState]`. Implementation faced a choice: rewrite the
235-line `/sira-query` handler so single-cell is just N=1 of the fusion
path (the design's "single-cell = N=1" principle), or keep the existing
single-dataset handler and add the multi-cell path beside it. The
existing `nora` dataset has no valid `(mno, release)` — its release is
the free-form `OA-baseline` — so it cannot be expressed as a cell, and
the handler couldn't be exercised against real bm25x on this machine.

**Decision:** Keep the legacy single-dataset handler **unchanged**; route
to a new `_multi_cell_query` path only when `<db_root>` contains
`<mno>__<MMMYYYY>` cells (`if _cells:`). The two paths coexist; a dataset
without a valid cell key stays on the legacy path.

**Why:** Zero regression risk for the current single-MNO setup — the
working handler (with its fan-out, instrumentation, pinned-chunks logic)
is untouched. A blind unified rewrite of a critical async handler that
couldn't be run here (no bm25x) was exactly the "large untestable block"
the dev persona warns against. The new path is built on the
standalone-tested `resolve_cells`/`fuse` and exercised via FastAPI
TestClient with fakes. The legacy dataset genuinely can't join the cell
model (no MMMYYYY release), so a unified path would have needed a
synthetic-cell-key hack for it anyway.

**Consequences:** Two retrieval code paths coexist — the multi-cell path
duplicates the retrieve/rerank shape rather than reusing the legacy
inline logic, and the legacy path lacks the multi-cell follow-ups (it has
fan-out; the multi-cell path doesn't yet — Follow-up 2). When the legacy
single-MNO `nora` dataset is retired (everything migrated to cells), the
legacy path becomes dead code and can be removed, leaving the unified
N=1 path as the design intended. Until then, both must be maintained.

_Promoted from strand: multi-mno-sira on 2026-07-03._

## D-115: Per-cell SIRA data config is generated, not a reused config with `data.name` override
**Status**: Active · **Date**: 2026-07-03.

**Context:** An earlier draft (D-DRAFT-6, left unpromoted at landing — superseded by this entry; see the strand archive) assumed SIRA's pipeline could run each cell off **one
reused `data/nora.yaml`** with `data.name=<cell>` overridden per cell — "no
per-cell config files." The first work-PC multi-cell run falsified this: SIRA's
`scripts/run_pipeline.py._with_dataset(cfg, ds_name)` **re-reads
`configs/data/<ds_name>.yaml` from disk** for each dataset (the `data=` hydra
group default is discarded), so every cell aborted with
`FileNotFoundError: configs/data/<cell>.yaml`. The reused-config-with-override
model is fundamentally incompatible with how `_with_dataset` resolves the
dataset by name.

**Decision:** The batch orchestrator **generates a per-cell data config** before
invoking each cell. `sira_multi.ensure_cell_data_config(clone, cell)` reads the
installed `configs/data/nora.yaml` template, sets only the `name:` field to the
cell, and writes `configs/data/<cell>.yaml` into the clone — then
`run_pipeline.py data.name=<cell>` resolves cleanly. Generated each run
(idempotent), so a changed template propagates. All other fields (`split`,
`k_values`, …) are cell-independent and copied verbatim. Keeps `run_pipeline.py`
itself unchanged (patch-don't-fork posture, SIRA D-110).

**Why:** Generation is the minimal fix that respects `_with_dataset`'s
file-by-name contract without forking SIRA or hand-maintaining N YAMLs. The
template-with-`name:`-substituted approach keeps cells identical apart from the
one field that must differ (the dataset identity that routes outputs to
`<db_root>/<cell>/`). Rejected: patching `run_pipeline._with_dataset` to accept
an in-memory override (forks the upstream clone — violates patch-don't-fork);
pre-generating all cell YAMLs in `install_configs.sh` (not automatic; drifts as
cells are added); symlinking each cell YAML to `nora.yaml` (the `name:` field
would be wrong, colliding all cells onto one dataset dir).

**Consequences:**
- New `sira_multi.ensure_cell_data_config`; `run_cells` calls it per cell before
  the subprocess. Requires the `nora.yaml` template installed in the clone
  (SETUP.md §4) — fail-loud if absent.
- `build_pipeline_cmd` docstring corrected; runbook §B documents the
  auto-generation. Supersedes the "no per-cell config files" clause of the
  earlier draft (D-DRAFT-6, left unpromoted at landing — this entry is the
  surviving record).
- The generated `configs/data/<cell>.yaml` files live in the gitignored clone
  (transient build artifacts), not the repo.

_Promoted from strand: multi-mno-sira on 2026-07-03._

## D-116: Pluggable `/rerank` backend (chat | tei | openai-dedicated), bulk-call protocol
**Status**: Active · **Date**: 2026-07-03.

**Context:** The cell-aware service reranks the merged cross-cell candidate pool
live. The original path was `chat` — pointwise LLM-as-judge, one chat call per
candidate, which is slow (N round-trips) and ties reranking to a generative LLM.
The work-PC has dedicated cross-encoder rerankers available (TEI on the HP z620,
vLLM on the DGX) that score a whole batch in one call. Different servers speak
different wire shapes and emit different response envelopes.

**Decision:** Add a `NORA_SIRA_RERANK_BACKEND` dispatch with three backends:
- `chat` (default) — pointwise LLM-as-judge via the rerank prompt; one call per
  candidate; failure scores that candidate 0 (graceful, per-candidate).
- `tei` — one bulk `POST {RERANK_LLM_URL}/rerank` `{query, texts, raw_scores}`
  → `[{index, score}]` (Hugging Face TEI cross-encoder).
- `openai-dedicated` — one bulk `POST {RERANK_LLM_URL}/v1/rerank`
  `{model, query, documents}` → `{results:[{index, relevance_score}]}` (vLLM
  Cohere-style).
Scores are scaled to 0-100 and are treated as absolute (cross-cell comparable
for the fusion sort). Base URLs must NOT include `/v1` (the service appends the
backend path). A **tolerant parser** reads bare-list / `results` / `data` /
`scores` / positional-float shapes and reads `relevance_score` or `score`; an
unrecognized shape logs the top-level keys and scores 0 rather than crashing.
Upstream `{error:{message}}` envelopes are surfaced to the log; HTTP calls
follow redirects (the rerank endpoint 308s).

**Why:** A dedicated cross-encoder is far cheaper than N LLM calls and keeps
reranking independent of the synthesis LLM. Dispatch (not a hard swap) keeps
`chat` as a zero-dependency default and lets operators point at whichever
endpoint their hardware serves. Tolerant parsing was forced by reality — vLLM
returned an `{error}` envelope (model didn't support the Score API), TEI and
Cohere-style differ in both request and response shape; a strict parser turned
every shape mismatch into a 500. Rejected: committing to one server's schema
(brittle across the two boxes); keeping `chat`-only (too slow at pool scale).

**Consequences:**
- New `_rerank_bulk` / `_rerank_candidates` dispatch, `_parse_rerank_response`,
  `_candidate_doc_texts`; healthz surfaces `rerank_backend`, `rerank_llm_url`.
- Operators must set base URLs WITHOUT `/v1` — documented in the runbook §C;
  a `/v1`-suffixed URL double-paths and 404s.
- Bulk backends score atomically per call — which created the 413 failure mode
  that **D-117** hardens.
- `openai-dedicated` requires a server that actually implements the rerank/score
  API; a chat-only vLLM returns an error envelope (now surfaced, not swallowed).

_Promoted from strand: multi-mno-sira on 2026-07-03._

## D-117: Bulk rerank resilience: sub-batch + truncate + degenerate-score round-robin
**Status**: Active · **Date**: 2026-07-03.

**Context:** With the D-116 `tei` backend, cross-MNO queries returned
**only MNO-B** chunks. Root cause chain: the merged pool is `n_cells × top_n`
(easily 40-100), sent to TEI in ONE request; TEI rejects it with **413** (pool >
`--max-client-batch-size`, default 32, and/or over-long dense chunks past the
model's max sequence length); `_rerank_bulk`'s `except` then scored the **whole
pool 0**; with all scores equal, `rank_candidates`' stable score-sort preserved
**pool order**, which is `sorted(target_cells)` (cell-alphabetical), so the
first cell took every `top_k` slot and the other MNO silently vanished. Lowering
`top_k`/batch size only narrowed which batch failed — the symptom persisted.

**Decision:** Three coupled changes so a rerank hiccup degrades to *balanced*,
never to *single-cell*:
1. **HTTP sub-batching** — `_rerank_bulk` chunks the pool into sub-batches
   honoring `NORA_RERANK_BATCH_SIZE` (else `NORA_SIRA_RERANK_BULK_BATCH_SIZE`,
   default 16) via `_rerank_one_batch`, and merges; a failed sub-batch scores
   ONLY its own candidates 0 (no longer poisons the whole pool).
2. **`truncate: true`** on the TEI payload — over-long chunks (MNO-A's dense
   band-tables run past the model window) are clipped server-side instead of
   413-ing; sub-batching alone can't help a single over-long doc (it 413s at
   batch size 1).
3. **Degenerate-score round-robin fallback** in `fusion.rank_candidates` — when
   rerank scores are all-equal / all-zero / none-present, fall back to
   `_round_robin(per_cell)` instead of the cell-collapsing stable sort.

**Why:** The failure is structural, not incidental — cross-cell fusion must not
let a transient rerank failure erase an entire MNO from a multi-MNO query.
Sub-batching addresses the count/payload limit, truncation the per-doc length
limit, and the round-robin fallback the *consequence* if scoring still fails.
Each is necessary: batch tuning alone still 413'd on dense single docs;
truncate alone still overflows the batch count at scale; the fallback alone
masks but doesn't fix the 413. Rejected: raising TEI server limits (per-box,
brittle, breaks as cells/`top_k` grow); auto-bisecting a 413'd batch (a single
over-long doc still 413s at size 1 — confirmed before building, so not built).

**Consequences:**
- A rerank-backend outage now yields balanced (round-robin) results with
  `rerank_score` 0/None, not a single-MNO result — visible degradation, not
  silent erasure.
- `truncate: true` clips over-long chunks to the model window — rows past it are
  invisible to the reranker, so table-heavy cells still need the upstream
  chunking fix (tracked on multi-mno-nora). Truncation is a floor, not a cure.
- The degenerate fallback does NOT fire on *partial* failure (one cell scored,
  one cell 413'd-to-zero) — that still sinks the zeroed cell; the durable answer
  is balanced packing (Path B / D-118 on multi-mno-nora), not this fallback.

_Promoted from strand: multi-mno-sira on 2026-07-03._

## D-118: Balanced cross-cell fusion: round-robin the top_k for multi-MNO queries
**Status**: Active · **Date**: 2026-07-03.

**Context:** A cross-MNO query (`"5G NR bands for both MNO-A and MNO-B"`)
returned ~21 MNO-B / 4 MNO-A in the top 25. `rank_candidates` sorts the merged
pool by the reranker's absolute 0-100 score and cuts at `top_k`. The premise
(D-113 call 1) was that absolute scores are cross-cell comparable — but
**chunk-granularity asymmetry breaks that**: MNO-B's sharper, smaller chunks
systematically out-score MNO-A's large/diluted/truncated band tables, so the
global sort + cut starves the lower-scoring MNO before it ever leaves SIRA (the
downstream NORA pin can't balance what it never receives).

**Decision:** Add `NORA_SIRA_FUSION_BALANCED` (default **off** = the global
score-sort, correct for single-MNO and when scores are genuinely comparable).
When **on and >1 cell**, `rank_candidates` sorts WITHIN each cell by rerank
score then `_round_robin`-interleaves the per-cell lists before the `top_k` cut.
Within-cell ordering stays score-ranked (reliable); cross-cell representation is
enforced structurally rather than trusted to absolute scores. `fusion.py` stays
I/O-free — the flag is a parameter; `service.py` reads the env var and passes
`balanced=`. Pairs with NORA's balanced pin (multi-mno-nora D-DRAFT-16) for
end-to-end balance: this fixes *what SIRA returns*, the pin fixes *what survives
to synth*.

**Why:** Cross-cell absolute-score comparability is the assumption that fails
when corpora are chunked differently — and re-chunking is a larger, separate
fix. Round-robin is the minimal structural guarantee that "show me both MNOs"
returns both. Default-off preserves the single-MNO/ comparable-score path.
Rejected: a per-cell *floor* + global fill (more knobs, less predictable);
trusting rerank scores (the very thing that's skewed); fixing only at the NORA
pin (starved by the SIRA cut, as observed).

**Consequences:**
- New `_rerank_sorted` helper + `balanced` param on `rank_candidates`/`fuse`;
  `_FUSION_BALANCED` env in the service; `fusion_balanced` in `/healthz`.
- Multi-cell results are no longer globally score-ordered when the flag is on —
  they interleave by cell. Acceptable (and desired) for comparison queries.
- Two strands now carry a **D-118** (this fusion balance + NORA's pin
  balance) — a coordinated pair; reconcile the canonical IDs at land time.
- Does not fix *answer quality* for table-heavy MNO-A chunks — that's the
  upstream chunking/table work (D-119 + re-chunking).

_Promoted from strand: multi-mno-sira on 2026-07-03._

## D-119: Tables inlined into req.text at document position (faithful order), consumers read text
**Status**: Active · **Date**: 2026-07-03.

**Context:** Band/frequency requirements keep their source-of-truth data in
tables, but the parser stored tables in a SEPARATE `Requirement.tables` field
and every consumer built searchable text from `req.text` alone — so tables were
**silently dropped** from the BEIR corpus (and the band chunks looked empty,
ranked low, and weren't selected by Path-B). A first fix appended tables in the
SIRA adapter (`a220722`), but appending after the body **loses document order**:
a requirement shaped intro → table → note became intro → note → table, which
breaks the LLM synthesizer's reading of the table against its surrounding text.

**Decision:** Inline each table's markdown into `req.text` **at its document
position**, at parse time — the block loop already runs in order, so the table
is appended to the section's text exactly where it appears (after preceding
prose, before following prose). `req.text` becomes the faithful, self-contained
content. A shared `render_table_markdown` is the single renderer:
`ChunkBuilder._table_to_markdown` delegates to it (vectorstore→parser, correct
layering) and **stops appending separately**; the SIRA adapter stops
re-serializing `req.tables`. `Requirement.tables` is kept as structured metadata
but is no longer the rendering source. Trailing traceability tables are already
cleared at parse time by `content_end_marker` (D-115), so this only inlines
legitimate content tables.

**Why:** The prose/table split was the actual defect — `req.text` *should* be
the full requirement content, in order. Inlining at the source makes BOTH lanes
(NORA RAG, SIRA corpus) faithful for free and removes the duplication risk of
two consumers each appending. Rejected: position markers in text (leak to
context/eval consumers that don't render them); per-consumer interleaving from a
new ordered-segments field (more model surface, req.text stays lossy for
context/eval); keeping the adapter-append (loses order — the bug we hit).

**Consequences:**
- `req.text` now contains tables → NORA RAG chunks, `build_context`, and eval
  text all see them (more faithful; larger context — capped by
  `build_context_max_chars` / synth budgets).
- `include_tables` config is now **vestigial** (tables are intrinsic to text;
  the flag no longer suppresses them) — documented, kept for back-compat.
- **Requires a re-parse** (cheap) → re-build NORA vectorstore / re-emit SIRA
  corpus + rebuild BM25; re-enrich only the changed table-bearing docs.
- Cross-cutting: the parser/chunk_builder mechanism is core (multi-mno-nora);
  this strand owns the SIRA-corpus facet (adapter passthrough + the band-query
  problem that drove it). `verify_tables` guards the invariant (no req with a
  `tables` field may lack the inline table).

_Promoted from strand: multi-mno-sira on 2026-07-03._

## D-120: run_stack.sh: isolated parallel stacks with a pooled feedback DB for attributable LLM A/B
**Status**: Active · **Date**: 2026-07-03.

**Context:** Comparing two synthesis LLMs (Qwen3 vs a proprietary model) under
select-synth means running two full NORA stacks (SIRA service + web) at once
without cross-talk, while keeping their outputs comparable. Each process reads
its config at import time, so a stack is pinned to its launch env — but a naive
two-stack launch would share ports/state/DBs and clobber each other, and a
per-stack feedback DB would force a manual merge to compare results.

**Decision:** `run_stack.sh` launches ONE isolated SIRA-service + web stack from
positional args (label, db_root, ports, llm_base, model[, api_key]) plus flags;
two invocations run in parallel. **Isolated per stack:** ports, state dir,
`--config-db`/`--jobs-db`/`--metrics-db` (precedence flag > env >
`<state>/<name>.db`), logs, per-process venv/python, and a
`~/.nora-stacks/<label>` registry backing `--stop <label>`. **Shared
deliberately:** the feedback DB — because `test_feedback` carries an `llm_model`
column, both stacks write to one DB and the A/B becomes a single
`GROUP BY llm_model` query instead of a cross-DB merge.

**Why:** Parallel stacks need hard isolation (ports/state/DBs/venvs) or they
corrupt each other's runtime; per-process env-at-import pinning is desirable here
(one model per stack, fixed). Pooling ONLY the feedback DB is the deliberate
exception: it is the comparison surface, and `llm_model` makes shared rows
attributable, so analysis needs no join. Config/jobs/metrics stay per-stack —
they're operational state, not comparison data. The operational tooling
(`--stop` registry, per-process venvs, preflight error surfacing,
options-anywhere parsing, NO_PROXY bypass) was hardened iteratively against real
work-PC failures rather than designed up front.

**Consequences:**
- The feedback DB is a shared write target across two processes; `FeedbackStore`
  uses per-op `aiosqlite.connect` (no WAL / busy_timeout) — fine for human-paced
  eval, NOT high concurrency. Revisit if the A/B is ever automated/parallel-load.
- `run_stack.sh` lives in `sandbox/` (not a core module) and encodes select-synth
  A/B assumptions (rerank off, synth-mode select-synth). It is eval scaffolding,
  not a production launcher.
- Per-stack config-DB + env-at-import means a running stack can't be reconfigured
  live — restart to change model/flags (acceptable for eval).
- Renumbers to a canonical D-XXX at land.

_Promoted from strand: multi-mno-sira on 2026-07-03._

## D-121: Per-cell top_k: scale the balanced cut by cell count (3-MNO readiness)
**Status**: Active · **Date**: 2026-07-03.

**Context:** A 3-MNO readiness audit found the stack is otherwise N-cell-general,
but representation budgets are **global**: in balanced multi-cell mode the final
fusion cut takes `[:top_k]` after round-robin, so each cell gets `top_k / N`.
The per-cell retrieve pool (`top_n`) is already per-cell, so the dilution is
entirely at that final cut. Going 2→3 cells shrinks every MNO's share ~33%,
which can push a borderline-ranked chunk (the FR2 band case is exactly this
shape) below the per-cell cut — structurally reproducing the "cross-MNO drops
one MNO" symptom we'd already fought.

**Decision:** Treat `top_k` as a **per-cell budget** in balanced multi-cell mode.
`_multi_cell_query` computes `cut = top_k * n_cells` (n_cells = cells with
candidates) and passes that to `rank_candidates`; round-robin order means the
first `top_k * n_cells` items are exactly the top `top_k` from each cell, and
unequal cells self-balance. Gated by `NORA_SIRA_SCALE_TOPK_BY_CELLS` (default on;
off = legacy global cut, for A/B). The response surfaces `n_cells` +
`effective_top_k`; `/healthz` reports the flag. `rank_candidates` stays a pure
mechanism — the per-cell policy lives in its one caller.

**Why:** The dilution is a budget-allocation problem, not a ranking problem, so
the fix belongs at the cut, not in retrieval or rerank. Per-cell scaling makes
adding an MNO *add* budget. Rejected alternatives: a **per-cell floor** with a
global ceiling (more complex, and the ceiling is the same dilution deferred);
**scaling the select-synth token budget** by N (it's bounded by the model's 128K
window — you physically can't grow per-MNO context, and the round-robin packer is
already fair, so retrieval quality is the right lever); **scaling `PIN_MAX`**
(that's the rerank-pin lane, now owned by `nora-retrieval-quality`, not this
strand). Default-on so MNO-C benefits without a remembered per-deploy bump.

**Consequences:**
- The service returns more candidates at higher cell counts (`top_k * N`); the
  select-synth packer trims to the token budget round-robin, so each cell's
  top-`top_k` are *available* even though not all are packed. Larger localhost
  payloads at 3+ cells (acceptable for eval).
- The per-MNO **context** share still shrinks with cell count (token budget is
  context-bound) — this fix guarantees the right candidates *reach* the packer,
  not that all fit. Retrieval/ranking quality is the complementary lever.
- New flag + two response fields + healthz field are a small persistent surface.
  Tests: per-cell budget preserved across 3 cells + the starvation counterfactual.
- Renumbers to a canonical D-XXX at land.

_Promoted from strand: multi-mno-sira on 2026-07-03._

## D-122: Adopt a LayoutProvider abstraction; use Docling for table + figure structure
**Status**: Active · **Date**: 2026-07-03.

**Context.** MNO-C tables (bordered and borderless, with merged cells and
multi-line/ragged content) were not reliably extracted by the geometric
pdfplumber + custom borderless detection, and downstream RAG/compliance needs
faithful tables; deferred work also needs figures / API-spec parsing.

**Decision.** Introduce a `LayoutProvider` protocol (mirrors `LLMProvider` /
`EmbeddingProvider` / `VectorStoreProvider`) that returns a document's tables +
figures; select it per-corpus via `DocumentProfile.layout_provider` (default `""`
= built-in geometric path). Implement `DoclingProvider`. Keep the pymupdf text
layer + the profile-driven structural parser for requirement text / headings /
req-ids. Fuse provider tables/figures into the existing `DocumentIR` by position:
bboxes normalized to top-left points, the underlying pymupdf paragraphs
suppressed, blocks sorted into reading order.

**Why.** Docling's layout model + TableFormer handle bordered and borderless
tables and figures far better than geometric heuristics (bake-off: HTML tables
matched source exactly; figures captured). A protocol keeps it vendor-neutral,
optional, and profile-gated. Rejected full Docling / VLM-OCR document parsing
(loses born-digital text exactness, the font metadata the profiler's heading
detection needs, and determinism/auditability). Rejected Hiro-Smart-Doc as the
engine (immature — 4 commits / 13 stars / no release; patent-domain-tuned; heavy
GPU vLLM service).

**Consequences.** Docling is an optional heavy dependency (not in
`requirements.txt`); a profile selecting it fails loud without Docling installed +
models provisioned offline (`DOCLING_ARTIFACTS` / `HF_HUB_OFFLINE`). No per-table
geometric fallback when a provider is set — Docling recall is trusted (hybrid
Docling + pdfplumber-lines dedup is the documented fallback if misses appear). New
public surface: `extraction.layout_provider` / `docling_provider`, `PDFExtractor`
layout params + fusion, `models.ContentBlock.html`/`.caption`,
`DocumentProfile.layout_provider`, `TableData.html` — MODULE.md curation pending.
Design detail in `docling-integration-design.md`.

_Promoted from strand: mno-c-ingestion on 2026-07-03._

## D-123: Borderless tables: preserve as demarcated text, not a reconstructed grid
**Status**: Active · **Date**: 2026-07-03.

**Context.** MNO-C borderless tables (no ruling lines, wrapped multi-line cells,
ragged columns) could not be reconstructed into a clean row/column grid. Three
attempts each mangled real tables: whole-page pdfplumber text strategy (mis-read
prose as a grid), numbered-row region detection (too narrow), and explicit
column-line grids (row explosion, separators inside words, header split off).

**Decision.** In the geometric path, detect a borderless-table region via
persistent column gutters and emit it as a layout-preserved `[TABLE]…[/TABLE]`
TEXT block at its document position — no forced row/column grid.

**Why.** A mangled grid reads worse than clean text for RAG / LLM synthesis; the
original problem (table content merging into the requirement's prose and losing
its identity) is solved by demarcation, which doesn't require a correct grid.
Grid reconstruction of wrapped/ragged borderless tables proved unreliable and
un-validatable without the source PDF.

**Consequences.** Borderless tables in the geometric path have no formal column
structure (acceptable for RAG). Superseded for MNO-C by the Docling path
(D-122) but retained as the built-in behavior for corpora without a layout
provider. The font-gate (`require_heading_font_for_numbering`) remains the safety
net against an undetected borderless table becoming false sections.

**Landing note (2026-07-03):** the mechanism shipped and stays profile-gated (`detect_text_tables`), but MNO-C itself later opted into the Docling layout provider (D-122) and runs with `detect_text_tables: false` — this remains the built-in path for corpora without a layout provider.

_Promoted from strand: mno-c-ingestion on 2026-07-03._

## D-124: Profile-gated header/footer margin handling (`pattern_only`)
**Status**: Active · **Date**: 2026-07-03.

**Context.** The PDF extractor blanket-drops all text in the top ~65pt /
bottom ~50pt as header/footer. On MNO-C, requirement headings that begin at the
very top of a page were silently discarded (lost requirements across many pages).

**Decision.** Add `DocumentProfile.header_footer_margin_mode`: `"blanket"`
(default, unchanged) vs `"pattern_only"` (margin text is kept and dropped only if
it matches a repeating-header / "Page N of M" / confidential pattern). MNO-C set
to `pattern_only`.

**Why.** A blanket margin drop is too aggressive for corpora where requirements
start high on the page; per-corpus gating avoids regressing OA, which relies on
the blanket drop. Rejected simply shrinking the margin — position alone can't
distinguish a header at y≈40 from a requirement at y≈36 on the same page.

**Consequences.** In `pattern_only`, a non-repeating margin artifact (e.g. a
unique top-right date) may leak into the IR as a stray block (extend the patterns
as needed). OA and other corpora are unaffected on the default `blanket`.

_Promoted from strand: mno-c-ingestion on 2026-07-03._

## D-125: Broadened req-id capture + `is_requirement` type discriminator
**Status**: Active · **Date**: 2026-07-03.

**Context.** MNO-C structural sections carry their own ids (a section-type tag,
`<PREFIX2>`) alongside actual requirements (`<PREFIX3>`). The original pattern was
scoped to the requirement type only, so sections got empty `req_id` — breaking
parent linking and hiding section identity. But simply broadening the pattern
would make every downstream consumer (`if c.req_id` sites, SIRA per-req corpus)
treat sections as requirements.

**Decision.** Broaden `requirement_id.pattern` to a non-capturing alternation
over both type tags, and add `requirement_id.requirement_type_pattern` (narrow,
requirement tag only): a post-link parser pass sets
`Requirement.is_requirement = bool(req_id matches the narrow pattern)`. The flag
is serialized to tree JSON (back-compat load default `bool(req_id)`), carried
into chunk metadata by the vectorstore, and honored by the SIRA adapter (nodes
with `is_requirement=False` are excluded from per-req corpus rows and the
multigranularity pointer lists; they still feed Context via `section_index`).

**Why.** Sections need ids for linking/context; requirements need to stay the
only retrievable/citable unit. A single broadened pattern + a narrow
discriminator keeps both, profile-only (D-003), with legacy corpora unchanged
via the empty-pattern/back-compat default. Alternative — separate section-id
field — rejected: every consumer of `req_id` would need a parallel field.

**Consequences.** Every new profile regex field MUST be added to
`substitute_placeholders` (this one initially wasn't → every node
`is_requirement=False`, 0 SIRA corpus rows) and re-runs must start from the
`profile` stage (substituted profile is cached). Query-side `is_requirement`
gating (synthesizer/query_cli/context_builder `if c.req_id` sites) is deferred
until the NORA-native lane is exercised.

_Promoted from strand: mno-c-ingestion on 2026-07-03._

## D-126: Definitions are `ID:`-labeled; bare req-ids are references
**Status**: Active · **Date**: 2026-07-03.

**Context.** MNO-C requirement headings end with `(PRIORITY) ID: <REQ-ID>`, but
release-notes/change-log sections cite the same req-ids in headings and body
text. The parser's three req-id paths (heading inline, standalone small-font
block, body-text scavenge) all used broad `findall`-style matching, so citations
became duplicate requirement nodes (90 real-requirement duplicates in the MNO-C
corpus).

**Decision.** New `requirement_id.id_label_pattern`: when set, a node's OWN
req_id is captured ONLY from the `ID:`-labeled marker (group 1) — overriding
`anchor` and gating all three id paths; a bare req-id anywhere is a reference.
For the residual case a citation carries the `ID:` label too (indistinguishable
by pattern), `exclude_section_pattern` gains `release notes`: the section is
title-matched (its number varies per doc) and its whole subtree is dropped
pre-serialization.

**Why.** The label is the corpus's own definition marker — position-based
alternatives failed in testing (an end-of-line anchor was tried and dropped:
citations are bare, so label presence is the sole reliable discriminator, and
the anchor only risked missing definitions). Whole-section exclusion beats
pattern tweaks for `ID:`-cited citations because no lexical signal separates
them from definitions. Result: requirement-duplicates 90 → 0, verified on the
full MNO-C corpus.

**Consequences.** Corpora with `id_label_pattern` set get NO req_id on unlabeled
headings (strict by design). The Release Notes content is absent from the tree
and all retrieval surfaces. Empty pattern = legacy anchor behavior, so other
corpora are untouched.

_Promoted from strand: mno-c-ingestion on 2026-07-03._

## D-127: One image set, two views: four CPU-only images + compose profiles
**Status**: Active · **Date**: 2026-07-18.

**Context.** The distro must serve two personas: developers (feature work,
model experiments, pipeline runs) and operators (run services, ingest new
releases seamlessly, observe). Separate dev/ops image sets would drift; and
SIRA's upstream dependency stack is nominally GPU-pinned, which would make
images host-specific.

**Decision.** Build exactly four CPU-only images — `nora-pipeline` (batch +
adapter + Docling, also the dev toolbox), `sira-batch` (trimmed SIRA venv +
bm25x), `sira-query` (same base, service entrypoint), `nora-web` — and express
the two views as compose *profiles* over the same images: `serve` (immutable
images + volumes + healthchecks), `ingest` (job-style `compose run`), `dev`
(host source bind-mounted, `--reload`, `dev-shell`). GPU is externalized
entirely: every LLM/vision call goes to an OpenAI-compatible endpoint
(proprietary cloud or on-prem DGX/RTX boxes), per SETUP §2a's trimmed install
(no torch/sglang in any image).

**Why.** One image set cannot drift between personas; CPU-only images run on
every host (dev PC, work PC, servers) with the GPU question reduced to "what
the endpoint needs" — which is already how the bare-metal stack works.
Bind-mount dev over the same images gives "edit on host, run in container with
all deps present" without maintaining dev images. Rejected: separate dev
images (drift), a single monolith image (couples web/service/pipeline rebuild
cadences; huge), self-hosted sglang in-image (contradicts the external-endpoint
reality and re-introduces GPU pinning).

**Consequences.** nora images carry CPU torch (~2.5 GB) for
sentence-transformers/Docling — size is the accepted cost of one-image-set.
Compose is the only supported orchestrator for now. The dev-view guarantee
depends on images tracking requirements.txt — dependency changes require an
image rebuild (bind-mounts cover code only).

_Promoted from strand: docker-distro on 2026-07-18._

## D-128: Distribution via internal-GitHub Release assets (Packages disabled)
**Status**: Active · **Date**: 2026-07-18.

**Context.** Images must move dev PC → work PC/servers through the internal
network. The internal GHES has GitHub Packages (and thus its container
registry) disabled — the `read:packages`/`write:packages` scopes are absent
from the PAT UI. Committing tarballs into git history was rejected outright
(permanent multi-GB bloat; we have force-push scars).

**Decision.** Ship images as `docker save | gzip` tarballs attached to
Releases on the internal `<team>/nora` repo: `push.sh` (create release by tag,
upload assets) and `pull.sh` (download by tag + `docker load`), both curl-only
against the GHES API so target hosts need no gh CLI. Release tag = image
version (`images-vN-<git-sha>`). Verified by a 300 MB probe (upload +
download) on the work PC.

**Amended 2026-07-07 — build locus is the work PC.** The dev PC cannot reach
the internal GHES and no file-transfer channel exists between the PCs, so
images are BUILT on the work PC and pushed from there. Two proxy accommodations
make work-PC builds viable: base images arrive via skopeo
(`skopeo-pull-bases.sh` — `docker pull` is proxy-broken but `skopeo copy →
docker load` is the proven pattern from the dgx/spark stacks), and torch
installs from PyPI (`--build-arg TORCH_INDEX_URL=https://pypi.org/simple`;
download.pytorch.org is allowlist-blocked). Dev-PC builds remain a fast
verification loop only — never the release. Rejected at this step: publishing
tarballs through the public github.com mirror (work PC could pull them, but
binary artifacts on the public repo were not worth it when work-PC builds
work).

**Why.** Releases are core GHES (can't be disabled), asset storage is outside
git history, and the flow mirrors the team's existing internal-GitHub habits.
Rejected: container registry (disabled; becomes a drop-in upgrade if admins
ever enable it — layering is already registry-optimal), git-committed
tarballs (history bloat), ad-hoc scp (no versioning/audit).

**Consequences.** No layer dedup — every update ships full tarballs; mitigated
by the designed base/app image split (rarely-rebuilt torch+Docling base vs a
~100-300 MB app layer) — split deferred until push cadence hurts. GHES asset
size limit (2 GB default, TBD with admins) may force split archives for the
base tarball. `pull.sh` is the only distribution dependency on target hosts.

_Promoted from strand: docker-distro on 2026-07-18._

## D-129: SIRA upstream baked into the image at a pinned commit
**Status**: Active · **Date**: 2026-07-18.

**Context.** Bare-metal installs clone upstream SIRA at `main` into the
gitignored `sandbox/sira/` and patch it via `install_configs.sh`. During the
phase-1 image build, a fresh clone at today's `main` FAILED the
per-stage-routing patch — upstream has drifted since our working clones were
made. The bare-metal instruction is silently broken for any new machine.

**Decision.** `sira.Dockerfile` clones upstream at build time pinned to
`SIRA_REF` (default `62ec59c…` — the commit our patches and configs are
verified against), applies `install_configs.sh` at build, and bakes the result
into `sira-base`. Upgrading upstream = deliberately bumping `SIRA_REF`,
re-verifying the patches apply, and rebuilding — never an in-place `git pull`.

**Why.** Reproducibility: an image tag now freezes a known-good (upstream ×
patches × configs) combination, and target hosts need no github.com access.
The build failure proved `main` is not a stable base. Rejected: cloning at
container start (network dependency at runtime, unpinned), vendoring the
patched clone into our repo (license/size, loses upstream provenance).

**Consequences.** SETUP.md's bare-metal clone instruction must pin the same
ref (flagged). Patch maintenance is now explicit: bump ref → re-verify →
rebuild. The runtime image carries git (install_configs needs `git apply`;
its swallowed stderr masked exactly this — flagged for a script fix).

_Promoted from strand: docker-distro on 2026-07-18._

## D-130: State/config boundary: volumes + per-host env files (D-062 in containers)
**Status**: Active · **Date**: 2026-07-18.

**Context.** Images will be shared through the internal repo and must carry
zero proprietary or host-specific material; meanwhile the stack needs heavy
per-host state (env_dir, SIRA db_root, Docling/HF models, web DBs) and
per-stack config (ports, LLM endpoints) — including two simultaneous stacks
on different LLMs.

**Decision.** Images bake NO endpoints, keys, mappings, models, or corpus
data. All state lives in host-mounted volumes (`/data/env`, `/data/db`,
`/data/models`, per-stack `/data/web-state`, pooled `/data/feedback`); all
config comes from gitignored per-host env files (`.env`, `.env.<stack>`) with
a committed placeholder `env.example`. Multi-stack A/B = compose project name
+ env-file (per-stack ports/LLM/web-state; shared read-only-ish db_root;
pooled feedback DB for attributable comparison — run_stack.sh/D-120 parity).
Models are provisioned once per host into the models volume
(`DOCLING_ARTIFACTS` + `HF_HUB_OFFLINE=1`), never baked.

**Why.** Carries the established D-062 redaction boundary (placeholders in
committed files, real values per-host) unchanged into the container world —
the public mirror never sees an internal hostname even in compose files.
Volumes keep images host-portable and data lifecycle independent of image
lifecycle. Rejected: baking models (images become huge and host-coupled),
named docker volumes for corpus data (harder to inspect/back up than the
existing on-disk layouts operators already know).

**Consequences.** First run on a new host requires filling `.env` and
provisioning the models volume (documented in docker/README + env.example).
Snap-docker hosts must keep env files and volume paths under $HOME (confinement
can't read /tmp — hit on the dev PC). The same env-var names carry the known
/v1-vs-no-/v1 rerank URL convention split; env.example documents it.

_Promoted from strand: docker-distro on 2026-07-18._

## D-131: Lane model: standards after resolve; ingestion/nora/sira lanes
**Status**: Active · **Date**: 2026-07-18.

**Context.** The 9-stage pipeline mixed source acquisition with retrieval-stack
construction, and SIRA's ingest (adapter → index/enrich) sat outside the runner
entirely — awkward for docker job orchestration and for the planned hybrid
retrieval (SIRA's enriched BM25 replacing rank_bm25, fused with taxonomy/
graph). `standards` sat after `taxonomy` by historical accident: run_standards
reads only resolve manifests + parse trees (never taxonomy), and skip-resolve
already implied skip-standards.

**Decision.** Reorder `standards` to stage 5 (after resolve), closing the
INGESTION lane (extract, profile, parse, resolve, standards — common source
acquisition/structuring for both retrieval lanes). Stages 6-9 (taxonomy, graph,
vectorstore, eval) are the NORA lane; `run_cli --lane ingestion|nora` is sugar
over --start/--end (PIPELINE_LANES). The SIRA lane stays sandbox-side (D-111
boundary — core never imports sandbox), with `sandbox/sira_lane.py` as its
single entrypoint (adapter + sira_multi, --only/wipe threaded through both) and
a SOURCE.json provenance stamp in every db_root. Lanes are a RUNNER concept:
the env_dir/out/<stage> filesystem layout (D-096) is unchanged.

**Why.** The three lanes map 1:1 onto the docker job types (nora-pipeline
ingestion job, nora-pipeline nora-lane job, sira-batch job) and can run
independently/in parallel after ingestion. Rejected: encoding lanes in the
directory layout (churns every consumer for zero gain); adding SIRA stages to
the core runner (inverts the D-111 boundary).

**Consequences.** Numeric --start/--end shifted (5=standards, 6=taxonomy);
stage NAMES unchanged, docs use names. The hybrid-retrieval future (promote
sira_query/fusion into core behind a retriever Protocol, superseding D-111's
informal scope for those modules) is explicitly deferred to its own strand —
this re-org is what makes it cheap later.

_Promoted from strand: docker-distro on 2026-07-18._

## D-132: Build/serve data topology: requirements store, build dirs, hardlink serve snapshots
**Status**: Active · **Date**: 2026-07-18.

**Context.** Data lived ad hoc: inputs copied per env, SIRA db_roots INSIDE the
repo tree (a `git clean -dx` would have wiped the ~13h enrichment cache), web
state DBs mixed into env_dir/state with per-stack FILENAME suffixes, and
rebuilds mutated what running services were reading. User's design goal: build
dirs hold only build artifacts; serving reads a dedicated, stable surface.

**Decision.** One `nora-data/` root: `requirements/<MNO>/<MMMYYYY>/` = THE
canonical source store, shared by all builds (new EnvironmentConfig
`requirements_dir` override + NORA_REQUIREMENTS_DIR + --requirements-dir;
default stays <env_dir>/input for back-compat); `nora-builds/<build>/` and
`sira-builds/<build>/` = build artifacts only, never inside the repo tree;
`serve/<label>/` = HARDLINK snapshots (`docker/promote.sh`, cp -al) of the
serve-set — nora/out/{vectorstore,graph,taxonomy} env-SHAPED (mounts as
/data/env with zero web change) + sira cells + MANIFEST.json. Labels are
immutable; rollback = repoint the stack .env; per-stack dirs (web-state-<x>)
replace filename suffixes; feedback stays ONE pooled dir (D-120).

**Why.** Hardlinks make promotion instant, ~zero-disk, container-safe (real
files, unlike symlinks), and WIPE-IMMUNE — rebuilding or wiping a build never
mutates a promoted snapshot (verified by deleting the source build). The
serve-set became small and well-defined once Parse Review/corrections were
declared phase-out (query lane needs no out/parse). Rejected: serve-as-copies
(GBs per promotion), serve-as-symlinks (break in containers), serve-as-
pointers-only (leaves serving exposed to in-place rebuilds).

**Consequences.** Requires one filesystem for builds+serve (hardlinks);
promote only when no build is mid-write; old labels GC'd manually. SOURCE.json
in db_roots + MANIFEST.json in labels give provenance both ways. Blue/green
and ingestion-A/B fall out free (two stacks on two labels/builds).

_Promoted from strand: docker-distro on 2026-07-18._

## D-133: Per-service runtime env files; compose environment: as the precedence guard
**Status**: Active · **Date**: 2026-07-18.

**Context.** A single env.example mixed four services' knobs with build/stack
wiring. An audit found the services read ~65 env vars while compose forwarded
~30 — the rest (incl. the D-104 reasoning sentinel, the select-synth pin/synth
group, the D-116/117 rerank-backend group) silently defaulted in containers.
Also found: endpoint vars interpolated as ${VAR:-} rendered EMPTY STRINGS that
would override any env file, and one documented var was a phantom
(NORA_LLM_SHIM_MODEL — nothing reads it; the service reads NORA_LLM_MODEL).

**Decision.** Split config into: `.env` (compose interpolation only — build
args, ports, volume paths, per-service file POINTERS ${*_ENV_FILE:-…}) and one
runtime env file per image (`env.<service>.example` → `.env.<service>`),
injected via per-service compose `env_file:` (optional, per-stack overridable).
Each example documents exactly the knobs its service reads, including per-file
/v1 conventions and default-on knobs. Compose `environment:` blocks carry ONLY
fixed container paths + inter-service URLs — they always win, so runtime files
can never break the container layout. (A shared .env.runtime passthrough was
tried first and superseded the same day by this split.)

**Why.** Containers only receive explicitly-mapped env; per-service files make
the full tunable surface visible where the operator edits, keep unrelated
services' knobs apart, and make A/B stacks a pointer swap. Empty-interpolation
override and phantom-var classes are structurally eliminated (endpoint vars no
longer appear in compose at all).

**Consequences.** A typo'd pointer silently loads nothing (env_file is
required:false) — `compose config` / `/healthz` is the check. Unset var ==
code default == bare-metal behavior; the minimal per-stack file is ~4 lines
(endpoints + explicit toggles like RERANK_ENABLED=false and the per-LLM
sentinel).

_Promoted from strand: docker-distro on 2026-07-18._

## D-134: Offline image builds (host-vendored dependencies) — optional mode
**Status**: Active · **Date**: 2026-07-18.

**Context.** At capture time, every in-container network fetch on the work PC
failed with connection resets (pip/apt/rustup/clone; direct, proxy, and even
--network=host) while HOST processes worked — so builds were reworked to fetch
nothing in-container. The root cause was later proven to be the perimeter DPI
resetting large post-quantum TLS ClientHellos, and the classical-TLS image fix
(D-135, promoted with this landing) restored ONLINE builds on that host —
demoting this machinery from required to OPTIONAL. This supersedes the
proxy-accommodation framing in D-128's amendment (TORCH_INDEX_URL=PyPI +
proxy build args — kept for merely proxy-restricted hosts).

**Decision.** `docker/prep-offline.sh` fetches/builds everything HOST-side into
gitignored `docker/vendor/`: pip-wheels for torch/requirements/docling/the
trimmed SIRA set (pip wheel builds sdists like pytrec_eval), the bm25x wheel
via host maturin (SETUP §2/3 toolchain; SKIP_BM25X escape hatch), and the SIRA
clone at SIRA_REF pre-patched on the host (install_configs.sh gained a
SIRA_CLONE override), plus PREP.json. `OFFLINE=1` builds consume vendor with
ZERO in-container network: wheels are BIND-MOUNTED during RUN (never COPY'd —
nothing added to layers), the SIRA source stage is ARG-selected
(sira-src-${OFFLINE}), and offline mode needs no apt/git/gcc in any stage.
Compose wires vendor as an additional_context and OFFLINE from .env.

**Why.** Host processes are the only allowed network path, so the host is
where fetching must happen; bind-mounts keep multi-GB wheels out of image
layers; stage selection keeps one Dockerfile serving both worlds (dev PC
builds ONLINE by default). Rejected: proxy tricks (later shown irrelevant —
the block was at the TLS layer, D-135), committing vendor (multi-GB git
history), separate offline Dockerfiles (drift).

**Consequences.** Dependency bumps on the work PC require a prep re-run;
vendor is per-host disk (~2.6GB+; PyPI torch drags nvidia wheels — the
base/app image split gains urgency). Validation: the OFFLINE=1 path was fully
proven under --network=none (build + serve smoke). With D-135 restoring online
builds — including runtime external fetches like the standards stage —
OFFLINE=1 is RETAINED as an option for truly egress-less hosts and for
deterministic fully-vendored builds, not as the default work-PC path.

_Promoted from strand: docker-distro on 2026-07-18._

## D-135: Classical TLS key-exchange groups baked into all images
**Status**: Active · **Date**: 2026-07-18.

**Context.** Work-PC container egress failed with connection resets at the TLS
ClientHello (bridge AND --network=host), while host tools passed. Packet capture
+ an E1/E2 discriminator proved the perimeter DPI resets large post-quantum
hellos: the python image's OpenSSL 3.5 sends ~1545B ML-KEM hellos; host OpenSSL
3.0 sends ~315B classical ones. Earlier theories (endpoint agent: ours;
IP whitelist: IT's) were both refuted by the capture — TCP connected from the
bridge, and host-net failed despite sharing the host IP.

**Decision.** Bake /etc/openssl-classical.cnf (Groups = X25519:P-256:P-384) +
ENV OPENSSL_CONF into nora-base and a shared py-classical stage (sira-src-0,
sira-base). Applies at build time (pip) and run time (e.g. standards fetches).

**Why.** Alternatives: (a) IT reconfigures the DPI — correct long-term, out of
our control, timeline unknown; (b) offline-only builds — worked but froze
dependency updates behind a manual vendor pipeline and produced 3x-larger
images (PyPI torch). Classical groups are universally supported; the only cost
is forgoing PQ key exchange for image-originated TLS, acceptable for package
fetches and internal endpoints.

**Consequences.** ONLINE builds work on DPI-guarded hosts; OFFLINE=1 demoted
from required to optional (amends D-134's premise — reconcile at land
time). Image TLS is non-PQ until the DPI vendor fix lands (revisit trigger:
IT confirms PQ-hello compatibility). docker pull (Go TLS in the daemon) is NOT
covered — skopeo-pull-bases.sh remains the base-image path.

_Promoted from strand: docker-distro on 2026-07-18._

## D-136: Extraction image artifacts belong to the build output, never the corpus
**Status**: Active · **Date**: 2026-07-18.

**Context.** Extractors wrote extracted_images/ next to the source document —
silently polluting writable input trees for months, and failing per-image
(Errno 30) against the container's read-only /data/requirements mount.

**Decision.** New images_root parameter on the extractor public surface
(BaseExtractor.extract, extract_document, all three extractors); the pipeline
extract stage passes the cell's out/extract/<mno>/<rel>/images. Recorded
image_path/images_dir values are relative to the cell out dir. Legacy
next-to-source layout retained only for the ad-hoc extract CLI. Corpus mounts
stay :ro.

**Why.** Matches the build/serve topology rule (D-132: build dirs hold all
build artifacts); keeps the corpus a clean, shared, read-only input; hardlink
promotion picks images up with the rest of the cell.

**Consequences.** IRs produced before this change carry stale relative paths;
image-ingestion strand tooling must resolve paths against the cell out dir.
Public-surface addition → extraction MODULE.md update pending (flagged).

_Promoted from strand: docker-distro on 2026-07-18._

## D-137: Layout-provider failures fail loud, never degrade silently
**Status**: Active · **Date**: 2026-07-18.

**Context.** When a profile names a layout provider, the geometric table path
is skipped by design. DoclingProvider caught convert failures and returned
empty structures — so missing models (DOCLING_ARTIFACTS mispointed +
HF_HUB_OFFLINE) produced parses with NO tables and empty req ids, with only a
log warning. Discovered when the first containerized parse diverged wildly
from the bare-metal reference.

**Decision.** Convert/iterate failures raise (with a DOCLING_ARTIFACTS hint);
the extract stage surfaces them as per-doc EXT-E001 failures.

**Why.** A tableless parse of a table-heavy requirements doc is corrupt data
that flows silently into graph/vectorstore/SIRA; a skipped doc with a loud
error is honest and diagnosable. Consistent with D-122's fail-loud intent for
explicitly-requested providers.

**Consequences.** Environment problems (models, deps) now abort docs instead
of degrading them — deployments must provision docling artifacts correctly
(README documents the MODELS_DIR/docling convention).

_Promoted from strand: docker-distro on 2026-07-18._

## D-138: SECRETS_ENV_FILE overlay: committable service envs, uncommitted creds
**Status**: Active · **Date**: 2026-07-18.

**Context.** Team wants per-service env files committed to the internal repo;
credentials must not ride along. compose does not expand ${VAR} inside
env_file contents, so shell-var references in committed files cannot work.

**Decision.** Every service loads an optional second env file
(${SECRETS_ENV_FILE:-.env.secrets}, required:false) AFTER its own env file;
later file wins on overlap. Credentials live only there; the file is
gitignored everywhere.

**Why.** Alternatives: bare `environment: - VAR` passthrough (depends on the
invoking shell — fragile for services/cron); docker secrets (swarm-oriented,
heavier). The overlay is declarative, per-stack overridable, and inert when
absent. Verified: override precedence, absence harmless, fixed container
paths still win.

**Consequences.** Compose-level convention all future services must follow;
stale keys left in committed files are silently overridden by the overlay
(by design — the overlay is authoritative for creds).

_Promoted from strand: docker-distro on 2026-07-18._

## D-139: Ingest jobs run as the host user; serve services stay root
**Status**: Active · **Date**: 2026-07-18.

**Context.** Containers default to root; lane runs left build artifacts
root-owned on the bind mounts — host cleanup/GC needed sudo, and the
root-owned reports dir broke non-root redirects.

**Decision.** nora-pipeline and sira-batch honor user:
${JOB_UID:-0}:${JOB_GID:-0} (id -u / id -g in the wiring env; unset = root)
with HOME=/tmp. Serve services stay root (they write only their own state
dirs; embedding-model cache behavior under non-root is unvalidated there).

**Why.** Alternatives: post-run chown (manual, forgettable); userns-remap
(daemon-global, invasive on snap docker). Per-service user: is scoped and
declarative.

**Consequences.** Wiring envs must set JOB_UID/JOB_GID; one-time chown of
pre-existing root-owned build dirs; non-root exposed the /data/reports bug
(good) — any future in-container write path outside the mounts will fail
loudly rather than silently landing in the overlay.

_Promoted from strand: docker-distro on 2026-07-18._

## D-140: Two-lane corpus inventory: sira-query /cells + merged web table
**Status**: Active · **Date**: 2026-07-18.

**Context.** Deployments serve some MNOs SIRA-only (ingestion lane to parse +
sira lane; no nora vectorstore). The test-page corpus table read only
out/vectorstore, so those cells were invisible — and for cells present in
both lanes, showing one lane's counts hid real divergence (a stale/planless
vectorstore next to a fresh SIRA corpus: 0 vs 87 plans).

**Decision.** sira-query exposes GET /cells (per-cell requirements = corpus
rows — one per requirement by adapter contract; plans = distinct **plan**
stamps; ingested = dataset dir mtime; cached for process life). The web
inventory merges it over NORA_SIRA_QUERY_URL: SIRA-only cells appear from
service data, a Lane column (NORA/SIRA badges) names the serving stack per
cell, and both-lane cells with diverging counts render BOTH numbers as
lane-colored badges — no lane is silently preferred.

**Why.** nora-web cannot read the sira db (it doesn't mount /data/db);
the service that owns the mount reports on it. Showing both diverging counts
was chosen over preferring either lane because the divergence is
information — independently-built lanes drift, and the mismatch badge is a
staleness indicator that points at rebuild candidates.

**Consequences.** New public surface on sira_query and a new runtime
dependency web → sira-query (/cells) — MODULE.md / MAP edges must record it
(flagged; pre-land refresh). Inventory cache needs a TTL besides the mtime
fingerprint since sira-query redeploys independently. Table degrades
gracefully to nora-only rows when the service is unreachable.

_Promoted from strand: docker-distro on 2026-07-18._

## D-141: Baked SIRA clone: PYTHONPATH import, read-only tree via SIRA_EXTRA_CONFIG_DIR
**Status**: Active · **Date**: 2026-07-18.

> **Amended 2026-07-18 pre-landing:** the world-writable-clone mechanism
> below was replaced the same week by `extra-config-dir.patch` +
> `SIRA_EXTRA_CONFIG_DIR`: per-cell data YAMLs are generated under
> `<db_root>/.hydra-configs/` (writable mount) and SIRA's `_with_dataset`
> consults that dir FIRST (its read is a literal path, NOT Hydra compose —
> so Hydra's `--config-dir` could not work). The clone is copied with
> default (read-only for non-root) permissions; SIRA's own Hydra outputs
> were already disabled by its run_pipeline.yaml. PYTHONPATH import
> mechanism unchanged. Historical context below records the interim
> writable-clone step and the alternatives weighed.

**Context.** First containerized sira-batch run: SIRA's run_pipeline imports
the `sira` package (bare metal: `pip install --no-deps -e .` + PYTHONPATH,
SETUP.md §2) and writes per-cell data configs INTO its own clone
(scripts/configs/data/<cell>.yaml — SIRA re-reads them by name and does not
honor data.name overrides). The image did neither: ModuleNotFoundError, then
Permission denied under JOB_UID.

**Decision.** sira-base sets ENV PYTHONPATH=/app/sandbox/sira/src (no
editable install), and the clone is copied with --chmod=0777 (plus a
trailing chmod on scripts/configs for files install_configs creates in
online builds).

**Why.** An editable install triggers a PEP-660 build-backend fetch — not
OFFLINE-safe; PYTHONPATH is equivalent for a src-layout clone and identical
in both build modes. For the writes: patching SIRA to accept a config path
adds upstream-drift patch burden; hydra --config-dir needs unverified SIRA
wiring; a tmpfs overlay is compose complexity for 1KB files. The clone is
container-ephemeral content, so wide permissions carry no risk, and the
generated YAMLs are disposable derived shims.

**Consequences.** The clone tree is writable scratch by contract — nothing
in it may be treated as immutable at run time. If a strictly read-only
image ever becomes a requirement, the hydra --config-dir route is the first
alternative to explore (noted, not planned). Future SIRA version bumps must
re-verify both mechanisms (src layout; config-by-name behavior).

_Promoted from strand: docker-distro on 2026-07-18._

## D-142: Extend `feedback_db.py` rather than build a parallel `team_pilot/` module
**Status**: Active · **Date**: 2026-07-18.

**Context:** The merged /test page needed structured per-(question x lane)
event logging with score + categories + comment. A `core/src/web/feedback_db.py`
already existed (the legacy thumbs-up/down log) with an aiosqlite async pattern
matching the rest of the web layer, an `/api/test/feedback` endpoint already
wired, and a schema overlap of ~60% with the new fields needed. A first attempt
in this session built a parallel `core/src/team_pilot/` module (sync stdlib
`sqlite3`, separate file, separate tests) before the existing infrastructure
was discovered.

**Decision:** Delete the just-built `team_pilot/` scaffold; extend the
existing `feedback_db.py` (additive columns + new `record_user_feedback`
method) and update `playground.py` in place.

**Why:** Single source of truth for /test feedback; no parallel near-duplicate
schema for analysts to disambiguate later. Async-consistent with the rest of
`core/src/web/` — the sync stdlib `sqlite3` path the parallel module used
would have introduced an inconsistent pattern. The existing
`/api/test/feedback` endpoint pipeline is reused rather than parallelled.
Alternatives considered: (a) keep team_pilot/ parallel with its own DB file —
rejected as two-DB drift over time; (b) migrate `feedback_db.py` *into*
`core/src/team_pilot/` — rejected as a heavier change that touches the
working legacy feedback flow.

**Consequences:** The merged-tab fields live alongside the legacy
vote/free_form_feedback fields in one table. Module boundary for "test-page
feedback" stays in `core/src/web/`, not extracted. Future analyses query a
single table. If the pilot's needs ever diverge sharply from the legacy
feedback flow (e.g. different retention policy), the consolidation might be
revisited.

---

_Promoted from strand: team-eval-pilot on 2026-07-18._

## D-143: Additive schema migration (8 new nullable columns + PRAGMA-driven ADD COLUMN) over schema rewrite
**Status**: Active · **Date**: 2026-07-18.

**Context:** The legacy `test_feedback` schema (id, timestamp, section,
question, answer, citations_json, vote, free_form_feedback,
query_elapsed_ms, llm_model, metadata_json) was missing fields the merged
tab needs (lane, user_name, retrieved_ids, reranked_ids, cited_ids,
user_score, user_categories, lane_config). A redesign could have cleaned up
overlaps (e.g. citations_json vs cited_ids; metadata_json vs lane_config) at
the cost of migrating existing rows; the additive path keeps all legacy
data interpretable.

**Decision:** Add the 8 new columns to the existing `test_feedback` table,
all nullable. CREATE TABLE includes them for fresh DBs. For DBs created
before the merged tab existed, `FeedbackStore.initialize()` reads
`PRAGMA table_info` and runs ADD COLUMN for any missing column (idempotent
on re-run). The `lane` index is created after `_ensure_columns` because
SQLite can't index a column that doesn't exist yet — learned during testing.

**Why:** Back-compat: legacy rows stay queryable unchanged; the existing
flows (requirement_bot / sira_retrieval section URLs) keep working. No
data migration step required — DB upgrades transparently on the next
service start. The overlaps (citations_json + cited_ids; metadata_json +
lane_config) are accepted as the price of additive migration; analysis SQL
treats the new columns as authoritative for the merged tab and falls back
to the legacy columns for legacy rows.

**Consequences:** Two near-duplicate views of citations live in one table
(rich dicts in citations_json; flat req_id list in cited_ids). Schema is
slightly more sprawling. The "fresh DB has all columns; legacy DB
upgrades" path is exercised by tests, so the upgrade behavior is
reproducible. Future schema changes follow the same pattern: column
additions go to `_MERGED_TAB_COLUMNS` (or a successor list) +
`_ensure_columns` picks them up.

---

_Promoted from strand: team-eval-pilot on 2026-07-18._

## D-144: Two methods on FeedbackStore (`record_user_feedback` new, `record_feedback` unchanged) over overloading the legacy method
**Status**: Active · **Date**: 2026-07-18.

**Context:** The legacy `record_feedback(row_id, vote, free_form_feedback)`
always updates both columns; passing `vote=None` deliberately clears a
prior vote. The merged tab needs to set user_score + user_categories +
comment + user_name without touching `vote` (the merged tab doesn't expose
up/down). A unified method would have to distinguish "field not passed" from
"field passed as None" — typically via a sentinel object — to preserve the
legacy clear-vote semantics while letting the merged path leave vote alone.

**Decision:** Add a new `record_user_feedback(row_id, *, user_score,
user_categories, comment, user_name)` method for the merged tab. Leave
`record_feedback` exactly as it was. The merged method's SQL UPDATE
explicitly excludes the `vote` column (only touches user_score,
user_categories, free_form_feedback for the comment, and user_name via
COALESCE so a re-submit without a name doesn't blank it).

**Why:** Two methods read cleaner than a sentinel-based dispatch. The
legacy method's tests + its existing call site in `playground_feedback`
keep working with zero changes. Re-submits on the merged tab cleanly
overwrite the merged fields without ever clobbering a legacy vote that
might be on the row (defensive invariant tested explicitly).

**Consequences:** Two methods with overlapping concerns ("user feedback on
test-page row") will need parallel updates if either flow gains a new
common field. The `free_form_feedback` column is shared between flows
(legacy free-form comment + merged-tab comment) — accepted because the
semantic is the same.

---

_Promoted from strand: team-eval-pilot on 2026-07-18._

## D-145: `lane` as a new column distinct from `section`
**Status**: Active · **Date**: 2026-07-18.

**Context:** The existing `section` column identifies the user-facing
tab/route (`'requirement_bot'`, `'sira_retrieval'`). The merged tab needs
to record *which retrieval pipeline answered* for a given (question,
section='merged') pair. Overloading `section` with values like
`'merged_nora'`/`'merged_sira'` would have collapsed the two concepts into
one column.

**Decision:** Keep `section` as the tab/route identifier (now also accepts
`'merged'`) and add `lane` ('nora'|'sira', NULL for legacy rows) as a new
column. The merged tab inserts `(section='merged', lane='nora')` and/or
`(section='merged', lane='sira')` rows. The row-creation invariant is
**one row per checked lane** — both checked → two rows; one checked → one
row; none checked → form-level rejection.

**Why:** `section` is the *where the question was asked from*; `lane` is
the *which retrieval pipeline answered*. Mixing them would lose the
ability to run cross-lane comparisons cleanly (the headline analysis "where
did NORA outperform SIRA on the same question?" becomes a JOIN on `question`
filtered by `lane`, not a string-prefix decomposition).

**Consequences:** Two fields to set/check rather than one. Legacy rows
have `lane=NULL`, which is meaningful (the legacy tabs predate the merged
view, and didn't distinguish a "pipeline" from the tab itself). Adding a
third pipeline later (e.g. a `'nora-rerank-v2'` lane) needs a CHECK
constraint update in both schema and the Python validator — currently
hardcoded to `('nora', 'sira')`.

---

_Promoted from strand: team-eval-pilot on 2026-07-18._

## D-146: Template architecture: conditional include in `_answer.html` + per-lane pre-render to string + outer container
**Status**: Active · **Date**: 2026-07-18.

**Context:** `_answer.html` is 503 lines and rendered from nine code paths
across `playground.py`. The body is largely lane-agnostic (a small SIRA
preamble is conditioned on `sira_results is defined`); the bottom feedback
widget was lane-specific (vote up/down only). Building the merged tab
required two things: per-column rendering of the existing answer body (to
preserve display fidelity) AND a different feedback widget per column
(score + categories vs vote).

**Decision:** Two-part template structure:
- The feedback widget at the bottom of `_answer.html` was replaced with a
  conditional include (`{% if feedback_mode == 'merged' %}
  {% include "test/_feedback_merged.html" %}{% else %}
  {% include "test/_feedback_legacy.html" %}{% endif %}`). The legacy
  widget moved verbatim into `_feedback_legacy.html`. New
  `_feedback_merged.html` carries the score + categories + comment form.
- The merged container (`_merged_answer.html`) is a thin two-column
  Bootstrap wrapper that receives **pre-rendered HTML strings** per lane
  (computed in Python via a new `_render_template_to_string` helper that
  re-uses the shared Jinja2Templates env + root_path injection). Each
  lane's `_answer.html` is rendered with `feedback_mode='merged'` plus
  the lane-specific context, then composed into the container.

**Why:** The conditional include is a one-line change to `_answer.html` that
preserves display fidelity (none of the 500-line body is touched). Per-lane
pre-render-to-string was picked over Jinja `{% with %}` variable
enumeration because `_answer.html` consumes ~20 context variables — listing
them all in a `with` block per lane would be brittle and easy to drift. The
render-to-string path lets the endpoint own the per-lane context shape
without templates needing to know.

**Consequences:** Two feedback widget files instead of one. The merged tab
accepts some visual nesting (border-in-border, duplicate Q heading per
column) since `_answer.html` keeps its own wrapper — accepted for the pilot
as the strictest preservation of "current display content"; can be polished
later if team flags clutter. The render-to-string helper is small but
introduces a new pattern: per-lane fragments built in Python, composed in
Jinja. Used here, may be reused if other multi-pipeline comparisons land.

---

_Promoted from strand: team-eval-pilot on 2026-07-18._

## D-147: Merged-tab UX posture: responsive layout + fault-isolated lanes + replace tab nav
**Status**: Active · **Date**: 2026-07-18.

**Context:** Three small UX/reliability shape-decisions on the merged tab
that were each load-bearing for the pilot's usefulness:

- *Layout:* both lanes checked vs one checked is a meaningful UX state.
- *Reliability:* SIRA's pipeline can fail (service down, LLM timeout) while
  NORA still works (or vice versa). The Ask should still return useful
  output for the lane that succeeded.
- *Navigation:* the existing /test page had a section-tab nav
  (`requirement_bot`, `sira_retrieval`) that the merged tab would render
  obsolete from the team's perspective.

**Decision:**
- **Responsive layout** — `_merged_answer.html` sizes each column at
  `col-md-6` when both lanes are rendered, `col-md-12` when only one.
  Single-lane queries get full width rather than half-empty space.
- **Fault-isolated lane runners** — `asyncio.gather(return_exceptions=False)`
  (each runner catches its own exceptions and returns `{"error": "..."}`).
  In the merged branch, lanes with an error render a Bootstrap alert in
  their column; lanes that succeeded render normally + still insert their
  `test_feedback` row. One lane's failure cannot block the other.
- **Replace tab nav** — `index.html` no longer renders the section-tab UI.
  The merged form is the sole entry point from the UI. The legacy section
  values are still accepted by `/api/test/ask` (back-compat for direct
  POSTs / bookmarked URLs), but the page no longer generates them.

**Why:** Per the user's instruction ("I just want one tab"), the team's
mental model is one form with two lanes, not "pick a tab then ask." A
responsive layout makes the single-lane case feel intentional, not
half-broken. Fault isolation is the right posture for an evaluation tool
specifically — comparing whatever did come back is more useful than
nothing, and the lane error itself is data worth seeing.

**Consequences:** Legacy section URLs are unreachable from the UI; they
linger as endpoints only. Eventual cleanup pending if no bookmarks depend
on them (carried in journal Next). The fault-isolation pattern means a
silent partial failure is possible — a team member could read a half-empty
two-column result without noticing the error alert. Accepted: the alert is
visually loud (Bootstrap `alert-danger`), and analyses can spot rows where
one lane succeeded without the other.

---

_Promoted from strand: team-eval-pilot on 2026-07-18._

## D-148: Two distinct OpenAI-style rerank classes (chat + dedicated) over one parameterized class
**Status**: Active · **Date**: 2026-07-18.

**Context:** vLLM can serve cross-encoder reranking two different ways. With
`--task=reranker`, it exposes `/v1/rerank` (Cohere-shape: `documents` →
wrapped `{"results": [...]}`). With the default `--task=generate`, only
`/v1/chat/completions` is available, and reranking has to be encoded as a
chat-completion prompt that asks the model to return per-pair scores. Most
production deploys run chat-mode vLLM (for synthesis) and would have to spin
up a second vLLM instance to get the dedicated endpoint. NORA needed to
support both deployment shapes.

**Decision:** Two distinct provider classes in `core/src/query/reranker.py`:
`OpenAIRerankChat` (issues chat-completion requests with a structured
scoring prompt; supports `batch_size` to pack N pairs per LLM call) and
`OpenAIRerankDedicated` (calls `/v1/rerank` directly in one HTTP call).
Both selected via the existing `reranker_provider` enum dispatch in
`core/src/web/routes/query.py`. Wire formats, error modes, and test
surfaces are entirely separate.

**Why:** The two backends have fundamentally different latency profiles
(chat-completion is per-batch LLM inference; `/v1/rerank` is one bulk
HTTP call), different prompt/payload contracts, and different failure
modes (chat parses LLM text output and handles per-pair-id score
extraction; dedicated parses structured JSON). A single parameterized
class would conflate these behind a mode flag and force every test path
to know which mode is active. Alternative considered: one provider with
`mode="chat"|"dedicated"` flag → rejected because the implementations
share almost nothing operationally; the abstraction would be ornamental.

**Consequences:** Two enum entries for what users may perceive as
"OpenAI-style reranking." Operators must know which their vLLM exposes
(chat works against anything OpenAI-compatible; dedicated requires
`--task=reranker`). The naming convention (provider-style hyphenated)
was extended to `tei` (the fifth entry); a sixth single-call backend in
the same family would mean another distinct class unless a shared base
emerges naturally by then.

---

_Promoted from strand: team-eval-pilot on 2026-07-18._

## D-149: Unified `NORA_RERANK_BATCH_SIZE` knob across all rerank providers
**Status**: Active · **Date**: 2026-07-18.

**Context:** `OpenAIRerankChat` needs a batch-size knob to control how many
(query, doc) pairs are packed into one chat-completion call (LLM context
window + cost tradeoffs). The SIRA codebase had previously used
`NORA_SIRA_RERANK_BATCH_SIZE` — a SIRA-specific name from before unified
rerank infrastructure existed. With three new rerank providers landing in
close succession (chat, dedicated, then TEI), the choice was: each provider
gets its own env var, or one cross-provider knob.

**Decision:** Single `NORA_RERANK_BATCH_SIZE` env var, resolved through the
standard 4-tier precedence (env > config-page DB > `config/llm.json` >
built-in default 1). Wired into `OpenAIRerankChat`. Deprecated alias
`NORA_SIRA_RERANK_BATCH_SIZE` accepted with a deprecation warning at
startup so existing deploys' configs keep working without a hard cutover.

**Why:** The semantic of "how many chunks to score per HTTP call" is
identical across providers. Users care about latency/throughput
tradeoffs, not which provider is configured. One knob means switching
provider doesn't silently change behavior. The deprecation alias
preserves operational continuity. Alternative considered: per-provider
env vars (`NORA_OPENAI_RERANK_BATCH_SIZE`, `NORA_TEI_BATCH_SIZE`) →
rejected as needless surface area; provider-switching would force
re-tuning.

**Consequences:** `TEIReranker`'s `max_batch_size=32` constructor default
(matching TEI's `--max-client-batch-size` server default; see D-152)
is a separate concept from the user knob — user knob is "request batch
size NORA wants"; TEI's cap is "server's per-request limit." Currently
`NORA_RERANK_BATCH_SIZE` is NOT plumbed into `TEIReranker.max_batch_size`
(TEI's internal split runs independently), and the user-knob default of
1 wouldn't make sense for TEI. **TODO:** future cleanup — decide whether
`NORA_RERANK_BATCH_SIZE=N` should override TEI's `max_batch_size` (and
document the divergence in resolution semantics) or remain TEI-internal.

---

_Promoted from strand: team-eval-pilot on 2026-07-18._

## D-150: SSE-streamed per-lane progress via `fetch` + `ReadableStream` (POST-based SSE) over EventSource or polling
**Status**: Active · **Date**: 2026-07-18.

**Context:** The merged /test tab runs NORA and SIRA lanes in parallel;
each can take 30s–5min (synthesis is slow on the spark). Users sitting at
"Asking..." with no feedback for minutes couldn't distinguish broken from
slow. We needed real-time per-lane progress (which stage running, which
finished, where time is going).

**Decision:** Replace the form's `hx-post` with a JS submit handler that
calls `fetch` and reads the streaming response via `ReadableStream`. The
endpoint emits Server-Sent Events (`event:` / `data:` lines) as lanes
progress through retrieval / rerank / synthesis. A new `#progress-display`
card renders per-lane rows with a spinner, status badge, and rolling stage
message.

**Why:** Three options were on the table. (a) **Polling**: client polls a
status endpoint every 1-2s. Rejected — adds 1-2s latency on every state
change and doubles HTTP load. (b) **EventSource** (browser-native SSE
client): GET-only by spec. Our query is a POST with substantial body
(question + lane config + flags); EventSource would force splitting into
two requests (POST to register, GET to subscribe) or jamming the body
into query params. Rejected — splits state across two requests, makes
the "submit form" pattern leaky. (c) **`fetch` + `ReadableStream`**
(SSE-over-POST): one request carries body up and event stream down.
Works in all modern browsers; trivial to parse `event:` / `data:` lines
from the response stream.

**Consequences:** The web layer now has one place doing manual SSE
parsing on the client side. A second streaming use case should trigger
factoring this out into a reusable helper. Server-side streaming over
POST is non-idiomatic in some HTTP intermediaries — some proxies buffer
POST responses fully before forwarding. Fine on direct localhost
connections (current pilot deploy); **flag for any future load-balancer
or CDN scenario between user and NORA.** The `#progress-display` card is
currently merged-tab-only.

---

_Promoted from strand: team-eval-pilot on 2026-07-18._

## D-151: `TEIReranker` as a distinct fifth provider class, not a TEI-shape variant of `OpenAIRerankDedicated`
**Status**: Active · **Date**: 2026-07-18.

**Context:** TEI's `/rerank` and vLLM's `/v1/rerank` are both "single HTTP
call, query + documents, returns sorted indices+scores" at a 30,000-foot
view. They could have been collapsed into one parameterized class with a
`wire="cohere-tei"|"vllm-openai"` flag. But the differences are pervasive:
URL (`/rerank` vs `/v1/rerank`), request field (`texts` vs `documents`),
response shape (flat array vs wrapped `{"results": [...]}`), score field
(`score` vs `relevance_score`), and `model` field presence (TEI is
single-model per instance and doesn't accept the field).

**Decision:** Add `TEIReranker` as the fifth provider class in
`core/src/query/reranker.py` (after `huggingface`, `ollama`,
`openai-rerank-chat`, `openai-rerank-dedicated`). Independent payload
construction, response parsing, and error handling. Shares the
`Reranker` Protocol and graceful-passthrough contract but no
implementation inheritance.

**Why:** Adding TEI-shape support to `OpenAIRerankDedicated` would have
meant five distinct conditional branches threaded through one method
(payload-key naming, response-shape detection, score-field selection,
model-field omission, URL suffix), each exercised by only one backend. A
separate class isolates each backend's wire idiosyncrasies. Boilerplate
duplication (truncate, Bearer-token header, urllib error handling)
accepted because the boilerplate is short, well-tested, and divergent
enough across providers that sharing it would itself need conditionals.
Alternative considered: factor a `_BaseSingleCallReranker` mixin →
rejected as premature; revisit if a third single-call provider lands and
the abstraction becomes evident from concrete code.

**Consequences:** Some patterns (truncate, Bearer header, urllib error
handling) live in both `OpenAIRerankDedicated` and `TEIReranker`. If
they drift in non-trivial ways, both should be reviewed together. Only
the `Reranker` Protocol is enforced; any future shared helpers would be
opt-in mixins, not enforced inheritance.

---

_Promoted from strand: team-eval-pilot on 2026-07-18._

## D-152: Client-side auto-batching in `TEIReranker` (default `max_batch_size=32`) over server-side `--max-client-batch-size` raise
**Status**: Active · **Date**: 2026-07-18.

**Context:** TEI's `--max-client-batch-size` defaults to 32; NORA's RAG
pipeline routinely hands the reranker 50+ chunks. First end-to-end test
on dgx-spark-srv returned `422 "batch size 50 > maximum allowed batch
size 32"`. Two ways to fix: raise the server cap on the spark, or split
client-side. The server raise was offered as a one-line change in the
dgx-spark-srv compose; client-side split was ~60 lines of code.

**Decision:** `TEIReranker.rerank()` splits inputs into ≤`max_batch_size`
batches (constructor default 32, matching TEI's server default), fires
one HTTP call per batch, and merges scores globally via
sort-by-score-descending. A failed batch contributes no scores; its
chunks fall through to the unranked tail in input order, preserving the
size invariant (output length == input length even with partial failure).

**Why:** Client-side batching makes NORA portable across any TEI
deployment regardless of the server's `--max-client-batch-size` setting.
Server-side raise would couple NORA's correctness to a specific gateway
config — anyone else deploying TEI would hit the same 422 on the first
oversized request and have to coordinate with the spark admin.
Score-sort-merge is provably correct: cross-encoders score (query, doc)
pairs independently with no batch-relative normalization, so
concatenating scores across batches and sorting yields the same global
ranking a hypothetical single oversized call would have produced.
Alternative considered: raise the spark's cap to 100 → noted as a
separate latency optimization (fewer round trips) but not load-bearing;
can be done later as a perf tune. Recorded in journal Next.

**Consequences:** Two HTTP round trips for 50 chunks instead of one
(currently sequential — parallelizing them is a future ~2× perf win,
flagged in journal). `max_batch_size` is an internal class default, not
plumbed through `NORA_RERANK_BATCH_SIZE` (see D-149 TODO). If TEI
later bumps its default or admins raise it server-side, the constant in
the constructor stays at 32 (safe lower bound). Partial-batch failures
surface in the new INFO logs as `batch K/N — FAILED` — operators can
spot it without grepping for WARNING.

---

_Promoted from strand: team-eval-pilot on 2026-07-18._

## D-153: `retry-failed` subcommand on `sira_incremental.py` (stage-scoped re-process) over flag-on-main or external tool
**Status**: Active · **Date**: 2026-07-18.

**Context:** SIRA incremental ingestion runs in two stages (doc-enrich,
rerank), each writing per-(doc_id) or per-(query_id, doc_id) outcomes to
JSONL state files. When a subset of entries failed (LLM timeout,
transient HTTP error, malformed input), operators had three poor
options: (a) re-run the whole ingest — slow at corpus scale; (b)
hand-edit JSONL state to remove failed entries — error-prone; (c) build
a new tool — fragmented operations surface across binaries.

**Decision:** Add `retry-failed` as a subcommand of `sira_incremental.py`
(the same CLI as the main ingest). Flags: `--stage doc-enrich|rerank|both`,
`--include-all-filtered`. Implementation prunes JSONL state for the
targeted stage's failed entries (`_prune_jsonl_by_keys` + `_STAGE_FILES`
+ `_STAGE_KEY_FN` tables), then re-runs the normal incremental loop,
which now sees the failed entries as "absent" and re-processes them.

**Why (reasoning inferred from code; user to confirm):** Subcommand
structure colocates retry with the original ingest tool — operators
don't context-switch between binaries to fix a partial failure.
Stage-scoping lets operators target the specific failure without
re-running the cheap stage when only the expensive stage broke (or vice
versa). The `--include-all-filtered` flag covers the case where entries
were filtered out for legitimate reasons but the operator wants to retry
them anyway (e.g., upstream fix landed). Alternative considered: a
`--retry-failed` flag on the existing main subcommand → rejected because
retry semantics (prune-then-process) differ enough from fresh-ingest
semantics (process-only-new) that conflating them would muddy the main
loop. **TODO if rationale differs.**

**Consequences:** Two CLI surfaces now coexist on `sira_incremental.py`
— operators must know which subcommand to use for which task. The
retry path mutates state files in place (prunes failed-entry rows before
re-running); operators wanting to inspect failed entries before retry
must do so *before* invoking the subcommand. The `_STAGE_FILES` +
`_STAGE_KEY_FN` tables are the new authority for "which file holds which
stage's state and what's its primary key"; future stages must register
there.

---

_Promoted from strand: team-eval-pilot on 2026-07-18._

## D-154: `TEIEmbedder` uses TEI's native `/embed` shape over the OpenAI-compatible `/v1/embeddings`
**Status**: Active · **Date**: 2026-07-18.

**Context:** TEI ≥1.5 exposes two embedding routes — its native
`POST /embed` with `{"inputs": [...]}` returning array-of-arrays, and an
OpenAI-shape `POST /v1/embeddings` with `{"input", "model"}` returning
`{"data": [{"embedding": [...]}]}`. The operator's probe
(`probe_tei`) confirmed both worked on their deployment:
`openai-embedding: dim=1024, 51ms`; `tei-embedding: dim=1024, 6ms`.
Roughly 8× faster on the native shape.

**Decision:** `TEIEmbedder` posts to `{base_url}/embed` in TEI's
native shape; OpenAI-compat path is documented in `probe_tei.py` as
available but not used. If a future deployment needs OpenAI-compat
(TEI version too old, or pointing at a non-TEI server), a separate
`OpenAIEmbedding` provider can land then — same precedent set by the
rerank providers (separate `openai-rerank-*` classes alongside `tei`).

**Why:** ~8× per-call latency win matters at embedding volumes
(whole-corpus ingestion at thousands of chunks; per-query embedding
at runtime). Smaller request/response payload (no `model` field, no
wrapper). Symmetric with `TEIReranker` from the prior session, which
used the same reasoning to land as a distinct class. Alternative
considered: OpenAI-compat as the "portable" shape that also works
against vLLM-embedding endpoints — rejected because (a) NORA doesn't
currently target vLLM embedding, (b) when it does, an
`OpenAIEmbedding` class can be added in parallel rather than
retrofitted on top of `TEIEmbedder`'s native-shape internals.

**Consequences:** `TEIEmbedder` only works against TEI servers; not
portable to other OpenAI-compat embedding endpoints. If TEI changes
its native wire shape in a future major version, the class needs
updating (the OpenAI-compat path would not). The provider family now
has two native-shape members (`TEIEmbedder`, `TEIReranker`) and three
OpenAI-style members (`OpenAIRerankChat`, `OpenAIRerankDedicated`,
`ollama` reranker) — the asymmetry is real and documented in the
class docstrings.

---

_Promoted from strand: team-eval-pilot on 2026-07-18._

## D-155: SIRA per-stage routing maintained as patch files under `sandbox/sira_patches/`, not a NORA-owned SIRA fork
**Status**: Active · **Date**: 2026-07-18.

**Context:** SIRA is cloned to `sandbox/sira/` and gitignored —
committing into the clone is not an option without forking upstream.
The per-stage routing change touches four upstream scripts
(`scripts/{add_doc_index_adapter,enrich_query_and_retrieve,llm_reranking,run_pipeline}.py`).
Two persistent paths: maintain a NORA-owned SIRA fork (gives full
version control, costs ongoing upstream-sync work), or carry patches
in NORA and apply on demand.

**Decision:** Patches live as unified diffs under
`sandbox/sira_patches/*.patch` and apply via
`sandbox/install_configs.sh` against the local SIRA clone.
Idempotency via sentinel-grep on a unique string from the patch
(currently `NORA_SIRA_ENRICH_LLM_URL`). The clone itself stays
gitignored and upstream-trackable via plain `git -C sandbox/sira
pull`.

**Why:** No upstream-divergence maintenance burden — pulling fresh
SIRA + re-running `install_configs.sh` is the upgrade path. The
clone stays a clean snapshot of the upstream HEAD the operator
chose, making it easy to bisect against upstream when an upstream
regression bites. Alternative considered: a NORA-owned fork →
rejected because the SIRA strand is treated as integration code,
not as upstream we want to evolve. We need flexibility to add
NORA-specific behavior without committing to long-term upstream-sync
work.

**Consequences:** Patches must be manually re-conflict-resolved when
upstream SIRA changes the patched call sites; the `git apply
--check` bail-out in `install_configs.sh` is the operator's only
line of defense. Sentinel-grep idempotency is fragile when patches
evolve (the documented operational footgun: an old patch's sentinel
hides the extended patch's new content from being applied — operator
must `git checkout -- scripts/` first). The clone's gitignored
status means changes there don't show in `git status` from NORA's
root — operators can't see at a glance whether a stale patched
state is sitting in the clone.

---

_Promoted from strand: team-eval-pilot on 2026-07-18._

## D-156: Single-knob `NORA_SIRA_*_LLM_TIMEOUT` collapses aiohttp `total` + `sock_read`
**Status**: Active · **Date**: 2026-07-18.

**Context:** SIRA's pre-patch `ClientTimeout(total=300.0,
sock_read=60.0)` has two independent timeout values. The 60s
`sock_read` was the actual cut-off surprise for slow LLMs (a request
that takes >60s before the first byte arrives gets dropped, even
though `total=300` would otherwise let it run). Adding env-var
control could expose one knob, two knobs, or four knobs (per-stage ×
per-axis).

**Decision:** One env var per stage
(`NORA_SIRA_ENRICH_LLM_TIMEOUT`, `NORA_SIRA_RERANK_LLM_TIMEOUT`);
its value is applied to **both** `total` and `sock_read`. Defaults
to 300s to match SIRA's pre-patch `total`. Each stage logs the
resolved value at startup as `LLM timeout: total=Xs sock_read=Xs`.

**Why:** Most users want the two values equal — the slow-LLM use
case is "give the request a long time," not "let the connection sit
idle for X but kill it overall after Y." Two separate knobs would
be more API surface for little operational benefit. Default of 300
(not 60) was chosen because it's the more permissive number of
SIRA's defaults and users tuning concurrency / timeouts should not
be silently downgrading to 60s. Alternative considered: separate
`*_TIMEOUT_TOTAL` and `*_TIMEOUT_READ` env vars → rejected as
needless surface area; the asymmetric default (`total=300`,
`sock_read=60`) in SIRA's upstream was likely an oversight, not a
deliberate design.

**Consequences:** Cannot independently tune `total` vs `sock_read`
without a follow-up patch. If a future use case needs that (e.g.
fast-establishing connection that streams slowly), the env var pair
would split into two — keeping back-compat would require the
single-knob name to mean "both," and new `_TOTAL` / `_READ` knobs
to override. Patching `llm.py`'s `post_chat` to also become
env-var-aware could provide finer-grained per-call control, but is
out of scope for this patch.

---

_Promoted from strand: team-eval-pilot on 2026-07-18._

## D-157: `NORA_SIRA_RERANK_LLM_*` env-var family shared between batch pipeline and runtime service
**Status**: Active · **Date**: 2026-07-18.

**Context:** Earlier this strand-series, `sandbox/sira_query/service.py`
(NORA's runtime SIRA query service) defined `NORA_SIRA_RERANK_LLM_URL
/ _MODEL / _API_KEY` to route the runtime rerank stage to a faster
LLM than the proprietary one used for enrichment. The new
batch-pipeline routing patch needed env vars for the same purpose —
rerank to a different endpoint than enrichment. Two options: pick
fresh names (e.g. `NORA_SIRA_BATCH_RERANK_LLM_*`) to keep batch and
runtime decoupled, or reuse the existing names.

**Decision:** Reuse the same `NORA_SIRA_RERANK_LLM_URL / _MODEL`
names. Both the batch-pipeline scripts (via the patch) and the
runtime service (`sandbox/sira_query/service.py:64-66`) read from
the same env vars. Setting the env var routes both code paths
consistently.

**Why:** Operators don't think in "batch vs runtime" boundaries —
they think in "where do I want SIRA's rerank LLM to live?" One env
var pair answers that question for both contexts.
Single-source-of-truth: if you set
`NORA_SIRA_RERANK_LLM_URL=http://localhost:11434/v1` in your shell,
both the batch eval pass and the runtime query service hit Ollama.
No drift possible between the two. Alternative considered: separate
prefixes → rejected because no scenario was found where you'd
actually want them to differ; if one does emerge, splitting later
is cheap (env var = config field, easy refactor).

**Consequences:** Cannot route batch and runtime rerank to different
LLMs via env vars alone — they share. If a future need emerges
(e.g. batch evaluates against the same LLM as production but
runtime A/B-tests a candidate replacement), the env vars need a
per-context override pattern. The decision presumes operators want
consistency over flexibility, which has held so far in this
strand's deployments. The batch pipeline does NOT yet have a
`NORA_SIRA_RERANK_LLM_API_KEY` reader (the runtime service does);
pure oversight — header injection isn't currently needed for this
strand but should land if it ever does.

---

_Promoted from strand: team-eval-pilot on 2026-07-18._

## D-158: Patch SIRA's hardcoded `127.0.0.1` at the per-call site, not by adding a `sglang.host` config field
**Status**: Active · **Date**: 2026-07-18.

**Context:** SIRA hardcodes `127.0.0.1` in four places —
`scripts/run_pipeline.py:261, 387` (reachability check / spawn) and
`scripts/{add_doc_index_adapter,enrich_query_and_retrieve,llm_reranking}.py`
(URL construction). Removing the localhost constraint could be done
two ways: (a) add a config field `sglang.host` defaulting to
`127.0.0.1`, all call sites read `cfg.sglang.host`; (b) introduce
env-var-driven URL overrides per stage, leaving the localhost
default in place when env vars are unset.

**Decision:** Option (b) — env-var-driven per-stage URL overrides.
The `127.0.0.1` literal stays in the code as the fallback; env vars
route around it when set. No new Hydra config field added.

**Why:** Per-stage routing is the real operator need — enrichment
to one LLM, rerank to a different (typically faster) one. A single
`sglang.host` knob couldn't express that. Adding both (a
`sglang.host` knob AND per-stage env vars) is more total surface
area than needed. The env-var approach also makes the
`run_pipeline.py` precheck/spawn skip clean: if either env var is
set, SIRA's own sglang spawn is unwanted; the guard reads the env
vars directly without touching Hydra config. Alternative
considered: add `sglang.host` AND `sglang.{enrich,rerank}_host`
per-stage overrides — rejected as Hydra-config gymnastics for what
env vars handle in 8 lines per call site.

**Consequences:** The four patched call sites must stay in sync as
patches evolve — if SIRA upstream renames a script or refactors the
URL construction, the patch needs updating in four places. The
`127.0.0.1` literal becomes dead code in env-routed setups (still
present, never reached). Operators who want to point SIRA at a
non-localhost endpoint without per-stage routing now have no path
other than setting both env vars to the same URL — which works
fine but is mildly awkward.

_Promoted from strand: team-eval-pilot on 2026-07-18._

## D-159: Team-eval access gate: single instance, env flag + admin-token cookie (not reverse-proxy auth / two instances)
**Status**: Active · **Date**: 2026-07-18.

**Context:** For the team eval we needed two things on the web app: (1) team
members see only the `/test` page (not the admin tools — parse, corrections,
jobs, config, …), and (2) the test page runs the SIRA lane only (NORA's retrieval
is known-weak and excluded from eval), while the architect still has full access
to everything. The app had no auth at all.

**Decision:** A single deployment gated by `NORA_WEB_TEAM_MODE` (default off =
no-op). When on, `TeamModeMiddleware` whitelists `/test` + `/api/test` + `/static`
+ health and redirects everything else to `/test`; the test page is SIRA-locked
(disabled checkboxes + a hidden `lanes=sira` input + server-side force
`lanes=["sira"]` in both ask handlers). The admin unlocks the full app **for their
own browser** by visiting `/admin-unlock?token=<NORA_WEB_ADMIN_TOKEN>`, which sets
an HttpOnly cookie checked on every request (`is_admin` / `team_restricted` in a
new `core/src/web/team_mode.py`). Same URL for everyone; the cookie flips access
and the page UI (lane lock + description) per request.

**Why:** Single-instance + cookie keeps it one deployment, one URL, no extra
process to run — and the lock is enforced server-side (UI disabling alone is
bypassable). Rejected: **reverse-proxy auth** (no app code, but pushes config onto
the proxy the architect manages, and we wanted the lane-lock + per-role UI in the
app anyway); **two instances** (a team-mode instance + a full instance) — simplest
code but operationally heavier and the architect explicitly wanted one URL with
per-role behavior. Defense-in-depth on the lane lock (template + hidden input +
server force) because each layer alone is insufficient (disabled inputs don't
submit; a crafted POST bypasses the UI).

**Consequences:**
- New persistent surface: `NORA_WEB_TEAM_MODE`, `NORA_WEB_ADMIN_TOKEN`, the
  `nora_admin` cookie, `team_mode.py`, `TeamModeMiddleware`, the `/admin-unlock`
  route. `team_restricted(request)` is now threaded into the `/test` render +
  both ask handlers.
- **Security caveat (documented):** the cookie carries the admin token (HttpOnly);
  fine for an internal eval with one trusted admin, NOT real auth on an untrusted
  network — pair with HTTPS + the production reverse proxy. The token also rides
  in the unlock URL (treat as secret, use HTTPS).
- The progress display had to become server-authoritative (the client can't be
  trusted to know the forced lanes) — see the `lanes` SSE event.
- Renumbers to a canonical D-XXX at land.

_Promoted from strand: team-eval-pilot on 2026-07-18._

## D-160: Enrichment corrections: delta overlay on a dedicated corrections volume
**Status**: Active · **Date**: 2026-07-24.

**Context:** Domain experts review/correct SIRA's per-requirement enrichment
words; edits must survive re-enrichment runs and serve-label churn.
Enrichment output lives in build artifacts (runs/doc-enrich/*); promoted
serve labels are immutable hardlink snapshots (D-132) — neither is writable
at review time.

**Decision:** Corrections are a DELTA overlay (remove/add/suppress_all per
req) stored under `<CORRECTIONS_DIR>/sira-enrich/` — a dedicated mounted
volume outside db_root cells and serve labels. LLM output is never touched.

**Why:** Divergence from the D-011 full-copy corrections precedent,
deliberately: enrichments regenerate (re-runs, prompt/model changes), are
thousands of rows per cell, and the delta IS the evaluation signal the
strand exists to produce ("experts removed X from 40 reqs" = prompt
evidence). Full copies fork stale and bury the signal. In-cell storage
would either be wiped by rebuilds or mutate immutable labels.

**Consequences:** Effective enrichment = fold(LLM output, active records);
both consumers (sira-query at load, web table) compute it. Undo = record
deletion (non-destructive by construction). New compose volume:
CORRECTIONS_DIR rw into nora-web, ro into sira-query (+ sira-batch later).

_Promoted from strand: sira-enrichment-review on 2026-07-24._

## D-161: Word-record granularity: label, reason, attribution per edited word
**Status**: Active · **Date**: 2026-07-24.

**Context:** Edits need reviewer labels (campaign/hypothesis tags for
toggle + bulk ops) and reasons (structured category + free-form note) to
make aggregated analysis possible. Entry-level fields conflate when one
req is edited under several campaigns/reasons.

**Decision:** Every edit is a per-word RECORD:
{word, label, reason{category, note}, by, at, origin{release}}; remove/add
lists hold records; suppress_all is an entry-level record with the same
fields. One active record per (word, direction) — re-editing replaces.
Reason categories are extensible via reason-categories.json; labels toggle
via labels.json.disabled with REMOVE-WINS on cross-group collisions.

**Why:** Storage granularity != input friction: the UI stamps records from
sticky "current label/reason" inputs as chips are clicked. Word records
dissolve the conflation limitation for label AND reason at once, make undo
perfectly targeted, make label-toggling exact, and make analysis a flat
scan (pivot label x category x word).

**Consequences:** More nested schema; per-word metadata (kilobytes —
irrelevant). Label x category becomes the standing evaluation matrix.

_Promoted from strand: sira-enrichment-review on 2026-07-24._

## D-162: Cross-release propagation via per-MNO keying + vanilla-index Jaccard guard
**Status**: Active · **Date**: 2026-07-24.

**Context:** A correction made in one release should apply to all releases
of that MNO for the same req_id — unless the requirement changed
significantly in between.

**Decision:** Corrections are keyed per MNO (`sira-enrich/<MNO>.json`),
applying to every release by default. Guard: records stamp origin.release
only; at application time the multi-cell service computes token-set
Jaccard between (req, origin_release) and (req, current_release) over the
VANILLA BM25 index term sets. >= 0.85 (tunable in code — a
`make_verdict_fn` parameter; no runtime knob wired yet) -> apply; below,
or origin release not loaded -> HELD for review (UI: [Re-affirm]
re-stamps origin, [Discard] deletes).

**Why:** Per-MNO keying makes propagation a non-event rather than a copy
mechanism. Vanilla index terms beat body-text fingerprints on all axes:
the same tokenizer retrieval uses (no second normalization spec to
maintain), pre-enrichment by construction (no circularity — the on-disk
index is vanilla; phrases apply in memory), and "significant change" is
measured in the space corrections operate in. Storing no fingerprint
(origin release only) keeps the overlay lean and comparisons always-live.
Held-not-applied on mismatch is the conservative posture: the correction
was a judgment about THAT text.

**Consequences:** Effective-view computation is per-(record, release)
conditional. The design-time bm25x term-extraction risk resolved benignly
at implementation: `vanilla_tokens` re-tokenizes the requirement's corpus
text with the index's OWN tokenizer (`bm25.tokenize`) — same-tokenizer
consistency holds, and corpus text is pre-enrichment by construction.
Cannot-verify == verified-changed (both HELD) also subsumes the
suppress-sticky-across-re-enrichment question.

_Promoted from strand: sira-enrichment-review on 2026-07-24._

## D-163: Access split: service HTTP reads, web-owned overlay, per-cell hot reload
**Status**: Active · **Date**: 2026-07-24.

**Context:** The review UI (nora-web) needs sira cell data (req text,
plans, LLM words) that only sira-query mounts; experts must verify edits
by re-querying WITHOUT an operator in the loop for service reloads.

**Decision:** sira-query gains read endpoints (GET /cells/<cell>/enrichments
[?plan=], /plans; loaded_at per cell in /cells) and POST /cells/<cell>/reload
— re-runs _load_one_cell and atomically swaps _cells[cell] under a per-cell
lock. nora-web owns overlay read/WRITE on the corrections volume (flock,
last-writer-wins per record) and proxies all service reads (one gate story).
The review UI's Apply button calls reload on BOTH stacks.

**Why:** Extends the D-140 pattern (service reports on data it owns) without
turning a retrieval service into a stateful editor backend — reload mutates
nothing durable (idempotent re-read of disk). Enables the expert loop:
edit -> apply -> re-query -> judge, incl. instant A/B of a label group.
Verified enabling fact: enrichment phrases are applied IN MEMORY at cell
load, so reload suffices — no index rebuild, no re-promote, ever.
Rejected: mtime-based lazy auto-reload (surprise latency, hidden state).

**Consequences:** Web computes the effective view itself so edits render
instantly regardless of service state; pending detection is stateless —
originally overlay mtime > loaded_at, superseded by the view-scoped
content digest (D-166). Reload is the service's only mutation-ish op;
team-mode gate must admit the new page + API prefix.

_Promoted from strand: sira-enrichment-review on 2026-07-24._

## D-164: Evaluation export: label x category pivot + prompt-fix scorecard
**Status**: Active · **Date**: 2026-07-24.

**Context:** The strand's end product is prompt-tuning evidence, not just
corrected data: reviewers' aggregated reasons should name systematic
enrichment failures and measure whether prompt fixes worked.

**Decision:** A deterministic FIX-report-style text export (D-012 posture:
keyword tokens, req_ids, categories, counts — no requirement bodies):
label x category matrix, top removed/added words with req/plan counts +
sample req_ids, suppressions, held summary, free-form notes grouped by
category. Plus SCORECARD mode: per remove-record, does the current LLM
output still produce the word? still = unfixed, gone = fixed; aggregated
per label/category ("handover-noise 34/40 fixed"). Delivered via
GET /api/enrich-review/export (copyable + download); no automatic file
drops in v1.

**Why:** The pivot turns individual edits into named systematic issues;
the scorecard closes the loop (edits -> issue -> prompt change -> measured
fix rate) and is WHY stale remove-records are retained rather than pruned
— they are the measuring stick. Deterministic ordering keeps successive
exports diffable in chat (FIX precedent).

**Consequences:** Remove-records are permanent by default (data-retention
rule); bulk-delete by label is the sanctioned cleanup once a fix is
confirmed. Export generation needs service data (plans, current llm_words)
joined with the overlay — web-side, over the proxied reads.

_Promoted from strand: sira-enrichment-review on 2026-07-24._

## D-165: Labels are branches: merge-log main, per-label serving variants, ungated Apply
**Status**: Active · **Date**: 2026-07-24.

**Context:** The original design had labels as toggleable tags inside one
shared view. In team use that meant an expert's in-progress corrections
hit everyone's default view immediately — no review gate between an edit
and production serving, and no way to preview a campaign in isolation.

**Decision:** A label IS a branch. Main = unlabeled records + labels
listed in `accepted-labels.json` (the merge log). A label view = main +
that label's records, served as a separate per-(cell,label) in-memory
variant built on demand. Admin-gated (team-mode `is_admin`): merge into
main, un-merge, delete-all — all merge-log or record-set operations;
records are NEVER rewritten on merge. Apply — and Apply-all, the
per-MNO staleness sweep in the Labels drawer (visible only when a
cell's view digest lags; reloads every stale cell on every configured
service) — is deliberately NOT admin-gated.

**Why:** Branch/merge is the mental model experts already have.
Merge-log-only merges are instant, reversible, and provenance-preserving
(every record keeps its label/author/reason forever — the evaluation
signal the strand exists for). Ungated Apply is safe by construction: a
reload can only sync serving to what the overlay + merge log already
contain — it can never publish an unmerged label. Rejected: rewriting
records' labels on merge (destroys provenance and the scorecard's
measuring stick); gating Apply on admin (blocks the expert self-service
loop D-163 established).

**Consequences:** Every read/serving path is view-parameterized
(allowed = unlabeled ∪ accepted ∪ viewer's label). A main reload drops
a cell's label variants — experts re-Apply after someone else's merge,
surfaced honestly by the pending banner (fallback state's digest
mismatches). A merged label's view is content-identical to main. A
merge stales every loaded release of the MNO at once — Apply-all is
the one-press answer to that fan-out.

_Promoted from strand: sira-enrichment-review on 2026-07-24._

## D-166: Pending = view-scoped content digest, formula-locked across web and sira-query
**Status**: Active · **Date**: 2026-07-24.

**Context:** The designed pending signal was `overlay mtime > loaded_at`
(D-163). Field use showed its flaws: false positives (fully-undone
edits, touch-without-change), and no view scoping once labels became
branches. A later bug showed a subtler trap: hashing the merge log
flagged every MNO pending on any merge/un-merge — even MNOs the label
never touched.

**Decision:** Pending = sha256 over the view's FILTERED overlay records
(canonical JSON; the merge log itself is EXCLUDED from the hash),
compared between the web store's live disk state and the digest
sira-query reports for its applied state. The formula is duplicated on
both sides and must byte-match (cross-side parity test). All
pending/banner decisions read from the PRIMARY configured service
(`NORA_SIRA_QUERY_URLS[0]`) — the service every read path queries.

**Why:** Content comparison means fully-undone edits digest back to
in-sync — zero false pending. Merge-log exclusion: the filtered records
fully determine served content, so hashing the global log added only
false positives. Primary-service rule: a stale secondary (e.g. older
image, different formula) must not win a freshest-responder race and
wedge the banner. Rejected: mtime (false positives), per-record dirty
flags (server-side state to maintain; digest is stateless).

**Consequences:** Digest formula changes must ship in BOTH images
together. Service `/cells` reports per-cell view digests (feeds the
Apply-all sweep). Supersedes D-163's original mtime signal (corrected
there at promotion).

_Promoted from strand: sira-enrichment-review on 2026-07-24._

## D-167: Fold countermand: newer add beats older remove, ties keep remove-wins
**Status**: Active · **Date**: 2026-07-24.

**Context:** The fold's absolute remove-wins-over-add made a merged
label's removes permanent: a later expert's add record for the same
word existed on disk but never applied — no path to restore a word
short of un-merging the whole label (admin, all-or-nothing).

**Decision:** Per word: an applied remove suppresses an add UNLESS the
add's `at` stamp is strictly newer (ISO-8601 lexicographic compare).
Ties and missing stamps keep legacy remove-wins. Countermanded words
keep their original position; `applied_removes` excludes countermanded
words. Implemented identically in the service fold
(`apply_overlay_to_req`) and the web chip rendering (`_row_view`).

**Why:** "Latest human judgment wins" matches editing intuition and
makes every serving state reachable without admin intervention.
Tie-bias to remove preserves the behavior of historical stampless
records and fixtures. Rejected: deleting the remove record on re-add
(destroys the audit trail and the scorecard's evidence); requiring
un-merge (blocks on admin, punishes unrelated words in the label).

**Consequences:** `at` stamps become semantically load-bearing
(ordering, not just provenance). Both sides must mirror the rule
(covered by the cross-side test). A merged remove is no longer a
guarantee the word stays gone — reports/scorecard reflect latest state.

_Promoted from strand: sira-enrichment-review on 2026-07-24._

## D-168: Batched doc-enrichment under a dual token budget; batch = transport, never state
**Status**: Active · **Date**: 2026-08-04.

**Context.** Single-req enrichment = 23k+ LLM calls per full pass —
throughput-bound, and each call repeats the full prompt header. The
64k-token LLM context invites packing many requirements per call, but
sizing scans showed the RESPONSE side (p95 86 tokens/req as strict
JSON) can overflow its reserve long before a 50k prompt cap is
reached when requirement texts are small.

**Decision.** Pack whole requirements per batch under a DUAL budget:
prompt side (cap 50k tokens, default; header measured by composing
the actual template + taxonomy block, req text via a chars/token
heuristic, default 3.5) AND response side (context − cap = reserve;
reqs/batch ≤ reserve / resp_tokens_per_req, default 90). Batch closes
when either budget would be exceeded. Requirements are never
fragmented; one too big for the budget ships as a solo oversized
batch, warned. Output contract: strict req_id-keyed JSON, every
packed id present, ≤10 phrases/req (the cap makes the response bound
enforceable). Missing/errored reqs re-queue into fresh batches
(bounded, default 2 rounds) then fail per-req. Batches are TRANSPORT:
per-req trace rows remain the resume/state unit; batch_id is metadata;
per-batch sizes log to batches.jsonl.

**Why.** Dual budget because the measured numbers prove either side
can bind first. Whole-req packing keeps the contract simple and the
LLM's per-req context intact. Per-req resume keeps the adapter's
existing trace/resume semantics untouched — a batched run interrupts
and resumes identically to a legacy run. Rejected: per-batch resume
(a failed batch would re-run answered reqs), tokenizer dependency for
exact counts (heuristic + margin is enough).

**Consequences.** resp_tokens_per_req and chars_per_token are
calibration constants (env-tunable) — a model with much longer
answers needs re-measurement. Trace rows no longer carry per-req
raw_response (batch-level in batches.jsonl instead). Auto-activation
by template shape ({requirements} present) means legacy prompts keep
the old path byte-identically.

_Promoted from strand: sira-enrichment-pe on 2026-08-04._

## D-169: Per-MNO prompts, per-plan taxonomy attachment, one plan per batch
**Status**: Active · **Date**: 2026-08-04.

**Context.** The single SIRA doc prompt was derived when only one
MNO's corpus existed; other MNOs' cells enrich with a prompt patterned
on the wrong subdomain mix and vocabulary. NORA's per-plan feature
taxonomies (feature/key-concepts/keywords, ≤5k chars) existed but
were invisible to enrichment.

**Decision.** One derived prompt per MNO (derive-sira-prompts skill,
MNO-scoped scan; doc_requirement_<MNO>_<ver>.txt), resolved per cell
at ingestion by the cell's MNO (env NORA_SIRA_DOC_PROMPT_DIR; loud
fallback to the config prompt). Prompt composition: per-MNO corpus
overview + the plan's WHOLE taxonomy file with a fixed prologue +
task instructions + packed requirements. Batches group by (MNO,
release, plan) so one taxonomy block serves the whole prompt; no
small-plan coalescing in v1 (≥1 batch per plan accepted; per-batch
size logging exists to revisit from data). Missing taxonomy →
omit block, warn, enrich anyway.

**Why.** Prompts should pattern the LLM after the corpus they enrich
— per-MNO is the natural unit (corpora differ per MNO; releases of
one MNO are near-duplicates). Taxonomy-per-plan is the tightest
domain context available and is already maintained per plan by NORA.
One-plan-per-batch keeps the prompt contract simple (one taxonomy,
one plan context). Rejected: multi-plan batches with multiple
taxonomy blocks (packing gain not yet proven needed).

**Consequences.** Per-MNO prompts contain real MNO vocabulary — they
live in `customizations/prompts/`, committed to the company-internal
remote only under the D-062 pre-push-hook trust boundary (amended
2026-07-24; supersedes the original "runtime artifact, never
committed" posture — both docker images COPY customizations/ at
build, so containers resolve them at /app/customizations/prompts and
a prompt update requires an image rebuild). New-MNO
ingestion gains a standard step: run the skill for that MNO.
Query/relevance prompts are also generated per MNO but the
query-time service loads a single pair — per-cell selection there is
deferred.

_Promoted from strand: sira-enrichment-pe on 2026-08-04._

## D-170: Combined staging: both prompt levers ship in one re-enrichment
**Status**: Active · **Date**: 2026-08-04.

**Context.** Two levers change together in this strand: the
enrichment prompt (overview + taxonomy + batching) and the taxonomy
CONTENT (regenerated with the overview-primed generation prompt).
The scorecard (D-164) attributes improvements to a lever only if
levers change one at a time — but it measures against expert
remove-records, and none exist yet (eval loop not run).

**Decision.** Ship both levers in ONE combined re-enrichment; the
result is the new baseline. From the first eval-loop campaign
onward: one lever per iteration, scorecard attributes each change.

**Why.** A staged rollout now would measure against nothing firm
(soft signals only — phrase churn, spot checks). Batching makes
re-enrichment passes cheap, so the option to iterate lever-by-lever
later stays open. Rejected: staged rounds now (calendar cost for
un-measurable attribution).

**Consequences.** This strand's quality delta is unattributed by
construction — accepted. The eval-loop strand inherits the job of
establishing the baseline against the combined result.

_Promoted from strand: sira-enrichment-pe on 2026-08-04._

## D-171: Corpus overview: derived once per MNO, consumed by both pipelines
**Status**: Active · **Date**: 2026-08-04.

**Context.** The AI-scanned corpus overview primes the SIRA doc
prompt. NORA's taxonomy-generation prompt (code:
core/src/taxonomy/extractor.py) sees only TOC headings + metadata —
no corpus context at all — and its output now feeds enrichment, so
its quality compounds.

**Decision.** The derive-sira-prompts skill writes the overview as a
standalone per-MNO artifact (corpus_overview_<MNO>_<ver>.txt) beside
the prompts. NORA's FeatureExtractor gains an OPTIONAL corpus-context
input (config knob → per-MNO file, resolved by the doc's MNO):
present → inserted as a "Corpus context" section; absent → today's
prompt byte-identically (fail-soft). (Implementation = slice 5;
includes the taxonomy-stage cache question: fingerprint currently
excludes the overview file — first regen needs --force or the
fingerprint gains the overview hash.)

**Why.** Derive once, consume twice — one authoring flow, no drift
between the two pipelines' understanding of the corpus. A second
skill deriving a second overview would double maintenance for the
same content. Optional + fail-soft keeps NORA's pipeline independent
of SIRA-side artifacts.

**Consequences.** The overview becomes shared infrastructure: its
regeneration cadence (on material corpus change) now affects BOTH
enrichment and taxonomy quality. Taxonomy MODULE.md must document
the new optional input when slice 5 lands.

_Promoted from strand: sira-enrichment-pe on 2026-08-04._

## D-172: Taxonomy cache fingerprint includes corpus-overview hash
**Status**: Active · **Date**: 2026-08-04.

**Context.** Slice 5 made per-MNO corpus overviews an input to the
taxonomy stage's extraction prompt. The stage is cached on a corpus
fingerprint (D-DRAFT-9, strand multi-mno-nora lineage) that hashed
only the contributing parse trees — so adding or editing an overview
would NOT flip the fingerprint, and a re-run would silently serve the
cached, context-less taxonomy. The design doc left the choice open:
require `--force` after overview changes, or fold the overview into
the fingerprint.

**Decision.** `_corpus_fingerprint` additionally hashes every
`corpus_overview_*.txt` (name + bytes) under
`$NORA_TAXONOMY_OVERVIEW_DIR`. Adding, editing, or removing an
overview re-derives the taxonomy on the next run; `--force` remains
available but is never required for correctness.

**Why.** Prompt inputs are cache inputs — a cache key that omits one
of them serves stale output by construction. The combined-staging
plan (D-DRAFT-3) depends on regenerated taxonomies actually being
regenerated; an operator forgetting `--force` would invalidate the
whole re-enrichment baseline without any error surfacing.

**Consequences.** Any overview-file touch triggers a full (LLM-driven,
non-deterministic) taxonomy re-derivation — deliberate: overviews
change rarely and only when regeneration is wanted. Hashing is
slightly over-broad (an overview for an MNO absent from the corpus
still busts the cache) — accepted for simplicity. The fingerprint
now depends on an env var's contents: same trees + different
`NORA_TAXONOMY_OVERVIEW_DIR` ⇒ different fingerprint, which is the
correct reading (different prompt inputs, different taxonomy).

_Promoted from strand: sira-enrichment-pe on 2026-08-04._

## D-173: Taxonomy extracts each plan from its newest release only
**Status**: Active · **Date**: 2026-08-04.

**Context.** Taxonomy is a global stage over every cell's parse trees,
but its output layout (`out/taxonomy/<plan_id>_features.json`) and the
downstream per-plan lookup are release-blind. With multiple releases
ingested, the same plan was LLM-extracted once per release and the
copies silently overwrote each other in incidental lexicographic path
order — which copy survived was accidental (observed on the work PC as
a March/July mix after a crashed run). Options: (a) extract only the
newest instance of each plan; (b) per-cell output
`out/taxonomy/<MNO>/<REL>/` plus a cell-aware SIRA lookup; (c) keep
extracting everything with deterministic oldest→newest overwrite.

**Decision.** (a): dedupe to the newest instance of each
`(MNO, plan_id)` before extraction — file-level in
`_select_newest_trees`, then again at plan-unit level after
`split_tree_by_plan` (chapter-per-plan docs have an empty tree-level
plan_id, invisible to file-level dedup). "Newest" = release directory
name parsed MMMYYYY → `YYYYMM` (`Jul2026` → `202607`) for chronological
comparison, lexicographic fallback for other naming, file-name
tiebreak. Superseded copies are counted in stage stats and never sent
to the LLM. The cache fingerprint hashes the selected set only.

**Why.** Releases of one MNO are near-duplicates and the consumer keys
on plan_id alone, so per-cell fidelity (b) isn't consumable without
SIRA-side changes, and (c) pays full LLM cost for outputs that get
overwritten. (a) halves LLM calls where releases overlap — fewer
chances to hit a flaky endpoint — and makes the surviving copy correct
by construction. Plain lexicographic name comparison was rejected
after the user's correction: month-name prefixes sort Jul2026 before
Mar2025.

**Consequences.** Older releases contribute nothing to the taxonomy —
acceptable while releases are near-duplicates; revisit if a plan's old
release diverges materially from its newest. An edit to a superseded
tree does not re-derive (fingerprint covers selected files only).
Release dirs not in MMMYYYY form fall back to alphabetical comparison,
which may not be chronological — new naming conventions must extend
`_release_sort_key`.

_Promoted from strand: sira-enrichment-pe on 2026-08-04._

## D-174: Taxonomy resilience: per-unit ledger, retry = re-run, no empty successes
**Status**: Active · **Date**: 2026-08-04.

**Context.** The taxonomy stage ran one unguarded loop over all trees:
a single sporadic LLM/server error killed the whole run,
`taxonomy.json` and the cache fingerprint were only written at the
end (a crash lost all progress), and an unparseable LLM response was
saved as an empty-but-valid features file. SIRA's enrichment side
already had a retry mechanism; the user asked for equivalent
capabilities: proceed-past-errors, resume, and failed-entry cleanup
with "some kind of retry-failed flag".

**Decision.** Per-unit fail-soft + resume via
`out/taxonomy/extraction_state.json`: each unit records status
(`ok`/`failed`), source-tree hash, and error; the file is rewritten
after every unit so a hard kill (docker stop, OOM) still resumes.
On re-run, unchanged `ok` units are reused and `failed` units retry
automatically — re-running the same command IS the retry mechanism;
no `--retry-failed` flag exists. A partial run consolidates what
succeeded and returns WARN (`TAX-W004`); the corpus fingerprint is
stamped only on a zero-failure run; all-units-failed is a FAIL.
Unparseable responses raise `LLMParseError` (extractor) instead of
returning empty `DocumentFeatures`; stale/orphaned features files are
deleted on re-derive. Prompt inputs (corpus overviews) are hashed into
the per-unit reuse key, not just the stage fingerprint.

**Why.** A failed unit has no valid output to protect, so retrying it
by default is always correct — a flag would just be a way to forget
it. Stamping the fingerprint on a degraded run would cache-lock the
degraded taxonomy (the exact failure mode that motivated D-DRAFT-5's
"prompt inputs are cache inputs" rule). Empty-success responses
poisoned both the cache and consolidation with silently-missing
features.

**Consequences.** `extraction_state.json` is a persistent stage
artifact operators may inspect (ok/failed histogram is in the runbook)
and occasionally surgically edit (done once: invalidating heading-less
mno-b units by dropping `#`-keyed entries). "Re-run until failed=0" is
now the documented operating loop for sporadic endpoint errors.
`_parse_response` raising instead of returning empty is a behavior
change for any future caller — callers must treat it as retryable.

_Promoted from strand: sira-enrichment-pe on 2026-08-04._

## D-175: Plan-unit taxonomy: split chapter-per-plan docs, headings join by ancestry
**Status**: Active · **Date**: 2026-08-04.

**Context.** One MNO publishes a single requirement doc where each
chapter is a plan: tree-level plan_id is empty, per-req plan_id is set
(D-DRAFT-1 lineage), 87 plans in one file. Unsplit, taxonomy produced
one `_features.json` (empty prefix — unfindable by SIRA's per-plan
lookup) from one whole-doc LLM call whose 200-line outline cap dropped
everything past the first ~200 headings. Extraction sends only
section-number + title outline lines, so heading nodes are the main
feature signal; but headings carry no req_id (hence no per-req plan),
and leaves in this corpus carry `parent_section` but no
`section_number`. Dropping unplanned nodes discarded 870 of 4942
nodes. Two attachment strategies were considered: document-order
backward-fill vs. an ancestry join.

**Decision.** The unit of taxonomy extraction is a plan, not a file.
`split_tree_by_plan` (taxonomy public surface) splits multi-plan trees
into per-plan subtrees; single-plan trees pass through unchanged (a
blank tree plan_id is promoted from its requirements). Headings attach
to the majority plan among plan-bearing reqs whose `parent_section`
sits at or under the heading's `section_number`; only nodes enclosing
no plan-bearing requirement drop (logged). Document order is preserved
per group; `plan_name` becomes the chapter heading title. The pipeline
stage dedupes units by `(MNO, plan_id)` (D-DRAFT-6) and ledgers them
as `<tree-relpath>#<plan_id>` so chapter-plans fail/resume/retry
independently; single-plan trees keep plain `<tree-relpath>` keys
(existing ledgers carry over).

**Why.** Per-plan calls fix all three failures at once: findable
output names, focused outlines under the 200-line cap, correct
consolidation attribution. The ancestry join was chosen over
document-order backward-fill after run-length diagnostics on the real
tree: chapters interleave multiple plans and trailing headings precede
the next chapter's leaves, so order-based fill misattributes at every
chapter boundary, while the `parent_section` join is position-independent
and was fully populated (4072/4072 leaves). Verified on the work PC:
drops fell 870 → 115, all requirement-free tail sections.

**Consequences.** mno-b yields ~87 LLM calls instead of one (that cost
is the fix). Heading attachment requires populated `parent_section` —
a profile that omits it degrades to leaf-only outlines (headings drop,
logged). Prose sections without req_ids remain invisible to
taxonomy/enrichment by design; their text stays reachable via section
nodes / `build_context_string`. A multi-plan chapter's top heading
goes to its majority plan only.

_Promoted from strand: sira-enrichment-pe on 2026-08-04._

## D-176: Post-interruption recovery: heal-torn invariant repair + mode-aware lane repair flags
**Status**: Active · **Date**: 2026-08-04.

**Context:** A power outage killed a long doc-enrichment run mid-write.
SIRA's resume is doc_id-keyed over append-only JSONL traces, but a hard kill
loses each open file's buffered tail independently, leaving (a) torn
half-written trailing lines — which crash the end-of-run merge's bare
`json.loads` — and (b) a broken kept↔enrichment pairing: a `trace.kept` row
whose enrichment row was lost makes the doc permanently "done" with its
enrichment silently gone; the reverse merges duplicate phrases on re-enrich.
Operators also had to hand-run per-cell `retry-failed` before every resume.

**Decision:** (1) New `sira_incremental heal-torn` subcommand: drop
unparseable lines from every `*.jsonl` in a stage run dir (shard files
included), then repair the doc-enrich invariant in BOTH directions — evict
kept-without-enrichment, drop orphan enrichments; `trace.failed` rows
untouched (retry-failed's jurisdiction). (2) `sira_lane --heal-torn` and
`--retry-failed [--include-all-filtered]` run the repairs per target cell
before the lane (heal first, so retry-failed parses every row), and are
skipped with an explicit note under `--wipe-all-derived` because the adapter
wipes `runs/` — repairs would be silently discarded.

**Why:** Torn-line dropping alone is insufficient — per-file buffers flush
independently, so either side of the kept/enrichment pair can survive alone;
only the two-way invariant repair makes resume trustworthy after a hard
kill. The repair lives in `sira_incremental` (the established home of trace
surgery, D-153's colocation rationale); the lane flags are thin delegating
conveniences, preserving D-153's subcommand-over-ingest-flag boundary.
Mode-awareness prevents the misleading "healed N → immediately wiped"
sequence. Alternative considered: manual host-side torn-line script →
rejected as unrepeatable and blind to the invariant damage.

**Consequences:** Run-dir trace files are no longer strictly append-only
from the operator's view — heal/retry rewrite them in place (inspect before
repairing). `heal_run_dir` must track any future change to the kept/
enrichment write pairing in `enrich_batching`. One-command recovery
(`sira_lane --heal-torn --retry-failed`) applies to incremental runs only;
full rebuilds ignore the flags by design.

_Promoted from strand: sira-enrichment-pe on 2026-08-04._

## D-177: Batch-path reasoning sentinel: code-appended marker instruction, opt-in env knob
**Status**: Active · **Date**: 2026-08-04.

**Context:** The enrichment endpoint's chain-of-thought leaks UNTAGGED
into `content` — no `<think>` delimiters to strip — and reasoning
sometimes includes draft JSON, which defeats both tag-stripping and
brace-scanning. The batch path never touches NORA's `openai_provider`
(raw aiohttp in the patch), so `NORA_LLM_REASONING_SENTINEL` cannot
cover it. Live symptom: unknown-req_id warnings and (pre-hardening) a
fenced thinking-draft could win the parse over the final answer.

**Decision:** Mirror NORA's sentinel pattern sandbox-side, opt-in via
`NORA_SIRA_BATCH_REASONING_SENTINEL=1`: (1) `compose_prompt` appends the
`===FINAL_ANSWER===` instruction to every batch prompt in CODE — per-MNO
prompt files stay untouched; the per-plan header token estimate includes
the suffix so packing stays honest. (2) `parse_batch_response` keeps only
what follows the LAST marker occurrence; marker absent → no-op
(fail-soft, byte-identical legacy behavior). (3) `FINAL_ANSWER_MARKER`
is duplicated in `enrich_batching.py` rather than imported.

**Why:** Code-appending beats prompt-file edits: one change covers every
MNO, needs no re-derivation or per-MNO review, and toggles per
environment (only the endpoint that thinks untagged pays the prompt
tokens). The constant is duplicated because sandbox never imports core
(D-111 boundary); the string is stable and cross-referenced in comments
both sides. Marker-absent no-op mirrors `_strip_reasoning`, so the knob
being off — or a model ignoring the instruction — degrades to prior
behavior rather than failing. Alternatives: endpoint-side
`enable_thinking: false` via `chat_template_kwargs` (kept as a
complementary lever; not all serving stacks honor it), per-MNO prompt
edits (rejected: rebake + re-derive churn per MNO, no per-env toggle).

**Consequences:** Sentinel-on changes prompt bytes → enrichments differ
from pre-sentinel cells (flagged in journal; uniformity re-enrich only
if eval motivates it). The sentinel does NOT stop thinking — response
budget must still absorb reasoning tokens (`RESP_TOKENS_PER_REQ` raised
to 400 on this endpoint, validated). Two sentinel constants now exist
(core + sandbox) that must stay string-identical if ever changed.

_Promoted from strand: sira-enrichment-pe on 2026-08-04._

## D-178: Single-req retry mode reuses the batched machinery (max_reqs=1)
**Status**: Active · **Date**: 2026-08-04.

**Context:** Reqs that fail inside large batches (truncation, one bad
neighbor poisoning a parse) needed a clean retry path. Two candidate
shapes existed: revive the legacy per-req prompt path (`{doc_text}`
template, separate parse), or run the batched pipeline with one req per
call.

**Decision:** `NORA_SIRA_BATCH_MAX_REQS` on `BatchConfig` (0 = derived
from response budget; ≥1 tightens only — `min(derived, cfg.max_reqs)`;
the explicit cap can never loosen the response-budget bound), surfaced
as `sira_lane --max-reqs N`. `1` = single-req mode: the SAME batched
prompt (taxonomy block included), packing, sentinel, requeue and trace
machinery — just one req per LLM call. Standard retry pairing:
`--retry-failed --max-reqs 1`.

**Why:** One prompt shape for both modes means one parser, one trace
schema, one verified pipeline — no dual-path drift, and per-MNO prompt
files serve both cases (architect call: the taxonomy block is wanted in
solo prompts too). The legacy per-req path stays what it is: a
compatibility fallback for non-batched templates, not a retry tool.

**Consequences:** Solo calls pay the full batched-prompt token overhead
(taxonomy block per call). Batch ids/stats mix solo and batched eras in
one run dir — handled by verify's invocation scoping (D-DRAFT-13).

_Promoted from strand: sira-enrichment-pe on 2026-08-04._

## D-179: Three-layer verify: per-cell primitive, orchestrator sweep, lane gate
**Status**: Active · **Date**: 2026-08-04.

**Context:** Batch + single-req enrichment needed systematic health
checking. Early sketches put multi-cell sweeping into `sira_incremental`
(which duplicated cell discovery with different rules) or proposed one
monolithic command; triage (WHICH reqs failed) initially conflated with
verification (IS anything wrong).

**Decision:** Three layers mirroring how the lane already drives
repairs per cell: `sira_incremental verify-run` = single-cell
primitive; `sira_multi --verify` = sweep (run_cells-style `--only`
intersection, warn-on-missing); `sira_lane --verify` = post-build gate.
Cell discovery unified on `sira_cells.enumerate_cells`. Verdict
contract: FAIL = structural breaks (torn lines, kept↔enrichment
invariant, duplicates, kept∩failed) → exit 1; WARN = quality signals
(parse errors, unanswered, non-benign failures, uncovered) → exit 1
only under `--strict`. Verify output is paste-safe BY CONSTRUCTION
(counts/statuses/sanitized errors only); id-bearing triage lives in a
separate LOCAL-ONLY tool (`sira_enrich_inspect --failed`). Batch stats
scope to the LATEST invocation (segmentation on batch-id sequence
resets, concurrency-jitter tolerance, `history:` line) because batches
files are append-only across resumes.

**Why:** Layering matches the existing repair pattern (lane →
heal/retry per cell), so each tool has one job and single-cell debugging
stays cheap. The paste-safe/triage split enforces the collab protocol
mechanically instead of by discipline. Latest-invocation scoping exists
because cumulative stats blended dead eras (observed live: 4 cells
all-WARN from historical rows; missing@final 4317 vs 279 actual) — a
clean retry pass must PASS on its own merits.

**Consequences:** Every failure-status addition must be classified
benign/non-benign in one place (`_BENIGN_FAILED_STATUSES`). Invocation
segmentation has a known limit: back-to-back tiny invocations (< jitter
window) merge. trace.failed is always current (retry evicts before
re-run) while batches files are history-bearing — readers must keep the
two mental models apart (documented in docker README).

_Promoted from strand: sira-enrichment-pe on 2026-08-04._

## D-180: Coarse doc/section chunks: skip enrichment by default, opt-in flags
**Status**: Active · **Date**: 2026-08-04.

**Context:** By-type verify decomposition showed ~93% of the ~2,100
persistent solo-retry failures were coarse `doc:`/`section:` rollup
rows — the model deterministically returns `{}`/no JSON for long
concatenated rollup texts — while per-req rows failed at ~0.6%. Coarse
rows were also the main driver of retry-round waste and token cost.

**Decision:** Enrichment SKIPS coarse rows by default at the
`run_batched` choke point. Skipped rows are traced as
`skipped_doc_chunk` / `skipped_section_chunk` — benign for verify,
coverage-counted, resume-visible. Opt-ins: `--enrich-doc-chunks` /
`--enrich-section-chunks` on `sira_lane`/`sira_multi` (exported as
`NORA_SIRA_BATCH_ENRICH_*_CHUNKS`). Re-scoping after a policy flip:
`retry-failed --include-skipped`. Verify reports failed/non-benign/
uncovered split by row type (req/doc/section).

**Why:** Trace rows (vs. silently dropping items) keep the coverage
invariant intact — verify still proves every corpus row was handled —
and make the policy self-describing in the run dir. Statuses record
policy, not failure, so default retries leave them alone (no churn).
Coarse rows still retrieve via BM25 on raw text; enrichment is additive,
so the quality cost of skipping is bounded while the failure noise it
removes is large.

**Consequences:** New trace statuses join the benign set and every
consumer (verify, triage listing, retry eviction) special-cases the
`skipped_` prefix. Enriching coarse chunks later requires BOTH the
opt-in flags and `--include-skipped` (documented recipe). Existing runs
convert their coarse failures to skipped rows on the next retry pass.

_Promoted from strand: sira-enrichment-pe on 2026-08-04._

## D-181: Permanent-refusal detection + fallback LLM (markers env-local)
**Status**: Active · **Date**: 2026-08-04.

**Context:** After the coarse-chunk policy, a 146-req residue failed
identically on every solo retry: the endpoint PERMANENTLY refuses those
inputs — a fixed notice instead of an answer (DEBUG_RAW-confirmed:
notice is the entire response). Retrying the same endpoint can never
succeed; each refused req burned all requeue rounds, every pass.

**Decision:** Shared detector `sandbox/llm_refusal.py`: refusal =
response starts with a configured marker AND carries no JSON payload.
Markers come from `NORA_LLM_REFUSAL_MARKERS` (`||`-separated) and are
DEPLOYMENT-LOCAL — set only in gitignored env files, never in committed
code/tests/docs. Batch lane: refused call gets ONE fallback-LLM shot
(same prompt/budget; `NORA_SIRA_ENRICH_FALLBACK_LLM_URL/_MODEL`; batch
row tagged `llm=fallback`, verify prints `fallback-answered N`); with
markers but no fallback, reqs fail fast as `llm_refused` (skip
remaining rounds), excluded from default retry, re-scoped via
`--include-refused`. Query service: identical detection inside the
`_llm_call` choke point (covers query-enrich + chat rerank;
`NORA_SIRA_QUERY_FALLBACK_LLM_*`; `/healthz` reports
`refusal_fallback {configured, used}`). Taxonomy lane: deferred (TBD).

**Why:** Detection at the call boundary (not per-caller parsing) gives
both lanes one implementation; the two-condition trigger (prefix AND
no-JSON) makes false positives on genuine answers implausible. Env-local
markers keep deployment-specific strings out of the public repo by
construction. Fail-fast without a fallback stops the observed
3-rounds-per-refusal waste and stamps a status that names the real
cause. One fallback pair per lane (not per stage) keeps the env surface
small — the fallback only ever sees inputs the primary refuses, so a
weaker local model is strictly better than nothing.

**Consequences:** Fallback enrichments mix into the same run,
auditable only via the `llm=fallback` tag. `llm_refused` joins the
status taxonomy (non-benign; excluded from default eviction — a third
include-flag on retry-failed). The detector ships flat into the SIRA
clone (install_configs.sh) — patch layer changed, image rebake
required. Marker hygiene is now a standing rule: real values never
committed; tests use invented placeholders.

_Promoted from strand: sira-enrichment-pe on 2026-08-04._

## D-182: Incremental enrichment (content-hash resume + drift guard)
**Status**: Active · **Date**: 2026-08-04.

**Context:** Corpus growth is the steady state (more releases, more MNOs). A
full doc-enrichment pass is ~13h. Growing the corpus 10× must not force a 10×
rebuild.

**Decision:** `sandbox/sira_incremental.py` rides SIRA's native doc_id-keyed
resume and adds content-hash awareness. `prune` (pre-enrich) evicts changed +
removed doc_ids from the run's trace so they re-enrich; unchanged docs stay
skipped. `commit` (post-enrich) records the corpus content-hashes as the new
baseline. A pinned `run_name` accumulates the trace across ingests. A
cumulative-drift guard warns when corpus growth since the last full rebuild
exceeds a ratio (default 1.5×, `--strict-growth` makes it a hard stop).

**Why:** Only new + changed docs hit the LLM; the BM25 rebuild and DF filter are
cheap and always run over the full corpus. Content-hashing catches same-doc_id
content edits (e.g. a correction) that SIRA's id-only resume would wrongly skip.
The drift guard surfaces when corpus-wide DF statistics have shifted enough that
cached enrichment (DF-filtered against a smaller corpus) is meaningfully stale.

**Consequences:** Operators must pin `run_name` and run prune/commit around each
ingest (documented in SETUP). A wrong/typo'd run_name silently falls back to
best-pointer behavior. The drift warning is advisory unless `--strict-growth`.

_Promoted from strand: plan-aware-sira on 2026-08-04._

## D-183: Extractor drops giant fill-only background rects/curves before table detection
**Status**: Active · **Date**: 2026-08-12.

**Context:** mno-b's single-PDF corpus paints pages with full-width fill-only,
stroke-less background bands (header band, large content band, footer band).
pdfplumber's "lines" table strategy treats rect edges as table rules; when a
real ruled table shares its x-extent with a band, the band edges join the
table lattice, the detected bbox inflates to the band, and the extractor's
committed-bbox text suppression swallows every paragraph on the page into
phantom table rows — requirements silently vanish from the IR.

**Decision:** `_drop_background_rects()` in `core/src/extraction/pdf_extractor.py`
filters page objects before `find_tables()`: any object with
`object_type in ("rect", "curve")`, a fill, no stroke, and area >= 50% of the
page area (`_BG_RECT_PAGE_FRAC = 0.5`) is removed. When a page has no such
object the original page is returned unfiltered (identity no-op), so corpora
without bands take the exact pre-fix code path.

**Why:** A page-fraction threshold on fill-only/stroke-less objects targets
decorative paint precisely: real table cells are stroked and small; no
legitimate table rule is a giant unstroked filled shape. The "curve" arm is
included because pdfminer classifies the identical fill-only band as a curve
(not rect) when the producer paints it as a closed line path instead of a
`re` operator — both encodings feed edges to the lattice. Rejected: per-profile
pdfplumber `table_settings` tuning (would fight the symptom per corpus instead
of removing the cause, and risks degrading genuine table detection).

**Consequences:** Table detection sees a filtered object set on banded pages;
any future giant filled shape that IS meaningful (e.g. a full-page table with
filled background and no strokes) would be filtered — accepted as implausible
for ruled-table corpora. Tests pin pdfplumber's current inflate/hug behavior
on a synthetic banded PDF, so a pdfplumber upgrade that changes lattice
construction will surface as a test failure prompting re-evaluation.

_Promoted from strand: mno-b-tables on 2026-08-12._

## D-184: Leading-id mode — TABLE/IMAGE blocks attach to the preceding requirement
**Status**: Active · **Date**: 2026-08-12.

**Context:** In `leading_id_body` detection mode (mno-b's shape — requirements
are flat body paragraphs opening with a req_id under non-requirement
headings), the parser's TABLE and IMAGE branches attached only to
`current_section`. Section nodes carry no req_id and downstream chunk
consumers drop id-less nodes, so tables were "in the tree" but absent from
the corpus — the parser-side half of the mno-b table loss.

**Decision:** In leading-id mode, TABLE and IMAGE blocks attach to
`current_leading_req or current_section`, mirroring the paragraph-continuation
branch, with a fresh-heading guard: a table/image arriving immediately after
a heading attaches to the heading's section and clears the stale requirement
cursor. Two boundaries hold: (a) deferred table-anchored extraction keeps the
SECTION as parent even in leading-id mode (table-anchored reqs parent into
the section hierarchy, not onto a sibling requirement); (b) the guard only
reads `previous_block_was_heading` — the flag is never modified in the
TABLE/IMAGE branches, so heading-continuation merging is byte-identical in
both modes. Heading mode is unchanged.

**Why:** A table following a requirement paragraph is that requirement's
content (bearer tables, config matrices) — the same document-order logic the
paragraph branch already used; asymmetry between the branches was the bug.
The fresh-heading guard prevents a section-preamble table from being claimed
by the last requirement of the previous section. Deviations (a)/(b) from the
field fix plan keep the section hierarchy and heading-merge invariants stable;
both were validated against the real corpus.

**Consequences:** Requirement nodes in leading-id corpora now carry `tables`
/ `images` and their inlined text; consumers see table content under the
req_id it belongs to. Extends the leading-id detection-mode contract (drafted
in the golden-eval strand, pending its landing) — parser MODULE.md invariants
updated as part of this landing.

_Promoted from strand: mno-b-tables on 2026-08-12._

## D-185: mno-b keeps geometric table extraction; Docling evaluated and rejected
**Status**: Active · **Date**: 2026-08-12.

**Context:** mno-c ingests via the Docling layout provider
(`layout_provider: "docling"`), which yields lossless HTML tables. After the
mno-b band/attachment fixes, the natural question was whether mno-b (and
mno-a) should adopt the same recipe.

**Decision:** mno-b stays on geometric (pdfplumber) table extraction — no
layout provider. Decided on field evidence: Docling was run on a sample page
range of the real mno-b document and showed no fidelity improvement over the
geometric tables.

**Why:** mno-b's tables are ruled/lattice tables that the geometric path
(post band-filter) captures faithfully; Docling adds nothing to correct
output. The cost side is real: a several-hundred-page PDF through a CPU
layout model turns a minutes-long extract into hours and forces a full cell
rebuild + re-enrichment. No fidelity gain at that cost is a clear reject.

**Consequences:** mno-b's profile keeps geometric extraction; the band filter
(D-183) remains load-bearing for it (a layout provider would have sidestepped
bands differently). Revisit trigger: a future mno-b document release with
borderless or merged-cell tables the geometric path can't represent.

_Promoted from strand: mno-b-tables on 2026-08-12._

## D-186: Recall checker: extract-vs-parse req-id diff as a bucketed, parse-blind sandbox tool
**Status**: Active · **Date**: 2026-08-16.

**Context:** Team reports of requirements visible in source PDFs but
absent downstream; no mechanized way to measure recognition recall or
localize where ids fall out of the pipeline.

**Decision:** `sandbox/verify_req_recall.py` diffs every extract-IR's
req-id candidates against the parse trees per cell, driven entirely by
the materialized cell profile (id pattern, type pattern, TOC entry
pattern). Output is BUCKETED CANDIDATES, not assertions: MISSING (with
locations), demoted, table-only, struck-only, toc-only, cross-doc,
section-id, UNPARSED. Liveness: a block is struck when either the block
flag or font_info.strikethrough is set; TOC entry lines are never body;
ids defined in a sibling doc of the same cell bucket as cross-doc.

**Why:** Bucketing over asserting because a raw regex over extract text
cannot distinguish definitions from citations — classification needs a
human/document pass, and the tool's job is to make that pass start from
a short list. Parse-blind by design: the checker does NOT replicate
parser logic (strike cascade, zoning), so cascade drops deliberately
surface as MISSING — replicating parse logic would make the checker
agree with the parser by construction and blind to its bugs. The
liveness rules were each field-driven: the naive version inflated
MISSING ~40x on a strike-heavy corpus (struck headings counted via
their live TOC echoes) and misread sibling-doc citations as gaps.

**Consequences:** Checker totals are candidates until classified;
cascade-dropped ids always need a classification pass. The tool depends
on materialized profiles (out/profile), so it runs only on built envs.

_Promoted from strand: req-recall on 2026-08-16._

## D-187: Fused-heading promotion: end-anchored-only rescue; extractor join untouched
**Status**: Active · **Date**: 2026-08-16.

**Context:** Headings whose title and id are separated only at layout
level join into fused text (`SOME TITLEABC-FOO-123`); with a run
topology that defeats the last-run solo-match, such ids never promoted.

**Decision:** In `anchor="last_run"` corpora, a multi-run heading whose
text carries an id at the very END (trailing whitespace tolerated)
promotes it and strips it from the title; logged as
`parser.format_error kind=fused_trailing_heading`. Mid-heading id
mentions still never promote. The extractor's run join is untouched.

**Why:** End-anchored-only preserves the no-promotion semantic for
inline citations while healing every fused topology. Extractor-side
separator insertion was rejected: DOCX splits runs mid-word, so blind
separators corrupt titles; and a parser-side fix heals existing
extracts (parse-only rebuild, no re-extract). Field note: on the live
corpus the "fused" headings turned out to be struck revisions the
parser correctly drops — the rescue found nothing to rescue there, but
stands as correct behavior for genuinely fused shapes.

**Consequences:** A heading legitimately ENDING with a citation would
be promoted; no such shape observed, and requirement_type filtering
still applies downstream.

_Promoted from strand: req-recall on 2026-08-16._

## D-188: Announced requirements: standalone labeled-id paragraphs anchor their own node (forward attach + backward move)
**Status**: Active · **Date**: 2026-08-16.

**Context:** In id_label corpora, requirements appear as a standalone
paragraph `(Marker) ID: <id>` with the statement in adjacent blocks.
Previously the announcement was absorbed into the preceding node (or at
best scavenged as the section's id), so every announcement after a
section's first vanished from the corpus (~500 per cell).

**Decision:** A body paragraph that is NOTHING but the labeled id
(optional parenthesized marker, which becomes the priority) spawns its
own Requirement — structurally like a leading-id node (no
section_number, parented to the enclosing heading); following
body/table/image blocks attach to it until the next heading or
announcement. A post-pass handles the trailing form (body first, id
line last): an announced node that ends the walk EMPTY moves its id
backward onto the nearest preceding content node without an id of its
own, and the empty shell is dropped. A labeled id embedded in longer
prose remains a reference.

**Why:** Spawning per announcement is the only shape that preserves
each requirement's own body; section-scavenging collapses N
requirements into one node. The backward move is a post-pass (not
spawn-time lookahead) so the validated forward behavior is untouched
and only provably-empty nodes move. Field-validated: MISSING 876→380 /
829→341 with zero ids lost and corpus rows growing by exactly the new
nodes.

**Consequences:** Sections that formerly scavenged an announced id now
parent a child requirement instead (tree reshape, id set preserved).
Empty container sections are the normal shape for structure whose body
lives on announced nodes; tree-backed UI surfaces see more id-less
container nodes.

_Promoted from strand: req-recall on 2026-08-16._

## D-189: Bare unlabeled ids stay references; bare_small_font_stamp exists but is active nowhere
**Status**: Active · **Date**: 2026-08-16.

**Context:** ~19 bare small-font solo-id paragraphs per cell in an
id_label corpus looked like id-after-body requirement stamps; a prior
contract (with a regression test) says an unlabeled id in an id_label
corpus is always a reference.

**Decision:** The contract stands. The opt-in profile knob
`requirement_id.bare_small_font_stamp` (default false; guards: exact
solo id, small font, enclosing section has body text and no id) was
implemented rather than a contract flip — and field validation then
INVERTED the premise: the bare ids are the corpus's citation style (18
of 19 defined in sibling docs; 1 cites a deliberately-nonexistent
deprecated requirement). The knob correctly refused every activation
and remains in code, active in no profile.

**Why:** An opt-in knob converts a risky global contract change into a
per-corpus decision with guards — and the guards are what caught the
wrong premise. Keeping the dormant knob costs nothing and the guard
behavior is now field-proven for any future corpus that genuinely
stamps ids after bodies.

**Consequences:** Activating the knob is a committed-profile edit, so
it is reviewable per corpus. The cell-wide cross-doc resolution in the
recall checker (D-DRAFT-1) exists specifically so this misread class
cannot recur.

_Promoted from strand: req-recall on 2026-08-16._

## D-190: Regenerating writers use temp + atomic rename (hardlink-snapshot isolation)
**Status**: Active · **Date**: 2026-08-16.

**Context:** Serve labels are promoted as hardlink snapshots and
declared immutable, but four writer classes rewrote existing files in
place on the same inode — parse-tree saves on forced re-parse, extract
IR saves, the adapter's corpus/queries/qrels/metadata emits, and the
incremental prune's run-cache rewrites — so build re-runs retroactively
mutated promoted labels.

**Decision:** Every regenerating writer writes a same-directory temp
file and `os.replace()`s it: each rewrite lands on a fresh inode, so
hardlink-shared snapshots keep their bytes, and a crash mid-write can
never tear a resume-critical file. Alternatives rejected: copy-based
promote (disk cost, doesn't fix crash-tearing) and post-snapshot
link-breaking in promote (masks future writer regressions instead of
surfacing them).

**Why:** Fixes the whole class at the writers rather than defending at
the snapshot; the crash-safety is a free second win for exactly the
files a torn write hurts most. Field-validated: 515 label files
byte-identical through a promote → forced rebuild → prune cycle, zero
temp residue, link counts dropping to 1 as designed.

**Consequences:** New writers that regenerate existing artifacts must
follow the same pattern (tests assert the hardlink-isolation property
directly). Labels mutated before the fix were written off — nothing
serving-critical was touched, and old labels are GC candidates.

_Promoted from strand: req-recall on 2026-08-16._

## D-191: Zone ruling: reference-list, standards-list, revision-history, and frontmatter ids drop with their sections by design
**Status**: Active · **Date**: 2026-08-16.

**Context:** Recall classification on the strike-heavy corpus left ~90
ids per cell in reference-list, standards-list, revision-history, and
introduction sections; deciding whether those are recognition gaps
bounds the remaining triage work.

**Decision:** Ids appearing inside those zones are citations or
metadata, not requirement definitions — the parser's reference-list,
revision-history, and content-start/zoning machinery deliberately drops
them with their sections. They are correct drops, not gaps.

**Why:** Those sections enumerate or cite requirements rather than
define them; recognizing their ids would mint duplicate or phantom
requirements — the exact defect class earlier ingestion work removed.

**Consequences:** Recall triage treats ids in those zones as explained;
only ids in body/requirement zones count toward the true gap.
Doc-title-level ids remain to be classified against plan metadata.

_Promoted from strand: req-recall on 2026-08-16._

## D-192: Separator-absorption id canonicalization; parser and checker mirror exactly
**Status**: Active · **Date**: 2026-08-16.

**Context:** Source families write ids with stray whitespace: between
bare tokens ("ABC FOO 12") and adjacent to existing separators
("ABC-FOO- 123", a systematic labeled-announcement class, 44+ per
cell). Naive whitespace→underscore canonicalization produced malformed
"ABC-FOO-_123" forms, and the recall checker diffing raw strings
against canonical tree ids reported entire doc families as phantom
MISSING (404/408 on one family).

**Decision:** Whitespace ADJACENT to an existing `-`/`_` separator
absorbs into the separator; only whitespace between two bare tokens
becomes an underscore. Implemented identically in the parser
(`_canonicalize_req_id`) and the checker (`_normalize`) — the two must
never diff a canonical form differently. Pattern-side tolerance
(`-\s?\d+`) is profile-tier and safe only because canonicalization
absorbs the space.

**Why:** Whitespace next to a separator is a source-formatting
artifact, not a missing separator — treating it as a token boundary
invents a second identity for the same requirement. The alternative
(checker-only normalization) leaves the parser storing malformed ids.

**Consequences:** Canonical id forms are stable across space-variant
sources; any future id-comparing tool must reuse the same two-step
canonicalization. Field-validated: +19 ids per affected cell, all
clean forms; phantom family cleared to 4.

_Promoted from strand: req-recall on 2026-08-16._

## D-193: Extraction claims sibling tails; displacement guards protect inline-labeled owners
**Status**: Active · **Date**: 2026-08-16.

**Context:** Chained announcement-after-body docs put statement N+1
into announced node N, so the extraction post-pass's claimable tail
carries a SIBLING sub-number relative to the absorber
("…6" tail in a "…5" node). The original strictly-extends guard
refused siblings, leaving field-validated empties unresolved; and an
absorber whose tail carried a DIFFERENT inline label could be
mis-claimed or overwritten (the field −1 regression).

**Decision:** The extraction guard refuses only a tail whose number
EQUALS the absorber's own section number (the demoted-duplicate shape
the guard was built for) or is an ANCESTOR of it; siblings and
extensions both claim. Two displacement guards: extraction refuses a
tail carrying a labeled id different from the claiming node's; the
plain backward move refuses a carrier whose text ENDS with a labeled
id.

**Why:** Field block-shapes proved the sibling case is the dominant
chained-doc reality, not an edge; strict-extends was protecting
against a shape (same-number duplicates) that the equality check
covers alone. The label guards keep the relaxation safe: a statement
carrying its own label always outranks positional inference.

**Consequences:** Any different-branch dotted tail is now claimable —
a text-level mis-claim sweep is queued as follow-up since coarse id
audits can't catch one. Field-validated: zero ids lost, chained
empties resolved.

_Promoted from strand: req-recall on 2026-08-16._

## D-194: Inline-trailing announcements: close the cursor, keep the scavenge shape (own-node spawn deferred)
**Status**: Active · **Date**: 2026-08-16.

**Context:** A third announcement form puts statement and label in ONE
paragraph ("<sub-num> <statement> (Marker) ID: <id>"). The
announced-cursor routing swallowed such paragraphs into the open
announced node, skipping the scavenge that anchored the inline label —
the label degraded to plain text and a neighboring announcement's id
was stamped over the node (field −1 regression).

**Decision:** A body paragraph ENDING with its own labeled id closes
the open announced cursor and takes the section body path, where the
existing scavenge anchors the inline id exactly as before the
announced-req machinery landed. End-of-text match only — mid-prose
citations keep flowing to the open announced req. The off-by-one node
shape (statement N+1's text on node N) is deliberately preserved; a
proper own-node spawn for the inline form is queued as post-batch
follow-up, not shipped pre-batch.

**Why:** Restoring the proven scavenge anchor was a zero-new-mechanism
fix on the batch's critical path; an own-node spawn changes node
topology corpus-wide and deserved its own validation cycle. A fourth
form (inline labels on numbering-path statements, one doc version)
needs that mechanism and was explicitly accepted as residue.

**Consequences:** One doc version ships with −1 anchored id + 3 empty
announced nodes as known state. The follow-up strand inherits the
own-node spawn design, which will also retire the off-by-one shape.

_Promoted from strand: req-recall on 2026-08-16._

## D-195: Heading-continuation defense is numbering-path-only
**Status**: Active · **Date**: 2026-08-16.

**Context:** Field validation showed styled level-1 chapter headings
with solo-id last runs failing to anchor while identically-shaped
level-2 siblings anchored — an empirical depth gate the design said
should not exist. Root cause: the heading-continuation defense (a
guard against PDF split-line artifacts misclassified as phantom
depth-1 chapters) documented itself as numbering-path-only but never
checked the detection method, so a TOC-paired chapter heading directly
following another heading was swallowed into that section's title, id
and all.

**Decision:** The defense is gated to the numbering classification
path in code. Word `Heading N` styles are authoritative: a styled
level-1 heading is never a split-line continuation artifact, so
docx_styles corpora bypass the defense entirely.

**Why:** The artifact the defense targets is a PDF text-extraction
failure mode; DOCX style tags cannot produce it. Depth is not a
definition-vs-citation discriminator anywhere else in the design — the
anchor-mode rules are — and this was the only (accidental) depth gate.

**Consequences:** Chapter-level requirement ids anchor on docx_styles
corpora (33-id field residue resolved on re-parse). Numbering-path
corpora keep the defense unchanged.

_Promoted from strand: req-recall on 2026-08-16._

## D-196: Generic <PLAN> placeholder class includes hyphen
**Status**: Active · **Date**: 2026-08-16.

**Context:** A live user complaint: an entire plan family absent from
the eval UI and retrieval. The generic `<PLAN>` placeholder
materialized to `[A-Za-z0-9_ ]+` — space allowed, hyphen not — so
every id under a hyphenated plan token failed to anchor (one family
100% invisible), and the recall checker, sharing the same pattern, was
blind to the loss by construction (the predicted pattern-blind class
found live).

**Decision:** The generic class is `[A-Za-z0-9_ -]+`, canonicalized
core-side in the placeholder table so every profile inheriting the
placeholder gets the fix; the interim inlined profile edit reverts to
the generic placeholder.

**Why:** Hyphenated plan tokens are real definitions, not noise; the
surrounding pattern (fixed prefix + `_\d+` suffix) keeps
false-positive risk immaterial. Core-side canonicalization beats
per-profile inlining because profile forks from the generic class
recreate the blindness.

**Consequences:** +931/+925 ids per affected cell; complaint doc went
0 → 231/231 anchored. Standing lesson recorded: any pattern the
checker shares with the parser is a shared blind spot — coverage
sweeps must scan with broader patterns than the profile's own.

_Promoted from strand: req-recall on 2026-08-16._

## D-197: Ship criterion applied: batch once with explicitly accepted residue
**Status**: Active · **Date**: 2026-08-16.

**Context:** After nine validation rounds the remaining recall gap was
one doc version needing a fourth recognition mechanism (inline labels
on numbering-path statements). Enrichment is hours-long and the hold
existed to run it exactly once; the original user complaint shipped
only with the batch.

**Decision:** Run the single combined enrichment batch with the
residue explicitly accepted and recorded: −1 anchored id + 3 empty
announced nodes in one doc version, plus a one-occurrence
prefix-space label sub-variant. Ship criterion: residue demonstrably
deep (new mechanism, not a fix to existing ones), confined to one doc
version, and net recall massively positive (checker-missing 13980 →
1524; ~1900 requirements recovered per largest cell, zero ids lost).

**Why:** Holding the batch for a mechanism that needs its own design
and validation cycle would have delayed every recovered requirement
and the complaint fix for a marginal, bounded gain. Accepting residue
implicitly, by contrast, hides it — the acceptance is recorded with a
follow-up queue.

**Consequences:** Corpus 37543 → 40097 served; eval-UI acceptance
passed (original complaint end-to-end, fresh 3-id report resolved by
pre-existing fixes). Follow-up queue (inline own-node spawn,
prefix-space tolerance, mis-claim text sweep) is a standing scope
decision after landing.

_Promoted from strand: req-recall on 2026-08-16._

## D-198: Headers-only tables render the compact [Table: …] inline; the compact form may span lines
**Status**: Active · **Date**: 2026-08-17.

**Context:** Headers-only tables (no data rows) were silently dropped
from node text — nodes looked table-healthy while their cells vanished
from the corpus; field validation surfaced 51 recovered sibling tables
on a single cell. Separately, when header cells contain multi-line
prose the compact render spans lines.

**Decision:** Headers-only tables render as the compact
``[Table: …]`` inline. The compact form is explicitly allowed to span
lines; consumers must not assume one-line.

**Why:** A headers-only table's header cells are often its entire
payload — dropping them is silent content loss, while a full HTML
render for a row-less table is weight without benefit. Stating the
multi-line contract beats letting consumers grow a single-line
assumption the renderer never promised.

**Consequences:** ~50 recovered inlines per large cell at rebuild;
anything parsing compact forms must handle embedded newlines (contract
documented at the renderer).

_Promoted from strand: table-fidelity on 2026-08-17._

## D-199: Merged-cell DOCX tables render lossless HTML at extract time, strike-gated
**Status**: Active · **Date**: 2026-08-17.

**Context:** Merged-cell DOCX tables flattened to markdown lose their
rowspan/colspan structure (several hundred merged tables in the field
corpus). But extract-time HTML rendering risks resurrecting content
the parser's profile-gated strike machinery would drop.

**Decision:** The DOCX extractor renders merged tables as lossless
HTML into the block (rowspan/colspan from merged cells, continuation
cells omitted, content escaped), gated on merges present AND no struck
runs; struck merged tables keep the markdown flatten. The parser's
html-preferred inline rule consumes it with no parser change.

**Why:** Merged structure survives into the corpus only via HTML; the
strike gate keeps strike-drop policy where it belongs (parser-side,
profile-gated) instead of being bypassed upstream.

**Consequences:** Merged tables inline as HTML downstream; struck
merged tables intentionally degrade to the flatten; unmerged tables
field-verified byte-identical through the change.

_Promoted from strand: table-fidelity on 2026-08-17._

## D-200: Empty-table guard: contentless TABLE blocks store nothing
**Status**: Active · **Date**: 2026-08-17.

**Context:** Layout providers can emit TABLE blocks with neither HTML
nor any non-whitespace grid content. Storing them hung empty TableData
on nodes and appended empty inlines — nodes looked table-bearing while
carrying nothing, and phantom tables could perturb requirement
attachment.

**Decision:** A TABLE block with no html and no non-whitespace grid
cell is skipped entirely: nothing stored, nothing inlined, invisible
to the attachment cursors.

**Why:** An empty table is extraction noise, not content; representing
it misleads every consumer and can break a real table's attachment.

**Consequences:** Rebuilds drop empty-table residue (field-validated:
a tables_field decrease exactly accounting the residue nodes); no
node appears table-bearing without content.

_Promoted from strand: table-fidelity on 2026-08-17._

## D-201: The table checker mirrors the renderer's output classes
**Status**: Active · **Date**: 2026-08-17.

**Context:** The inline checker recognized only one render form:
HTML-inlined corpora were false-flagged as missing wholesale, and
compact/anchored forms went uncounted — field triage decomposed one
cell's 158 reported misses into 21 checker-semantics artifacts + 137
real renderer gaps.

**Decision:** The checker counts every render class the pipeline
actually emits — HTML-inlined, compact ``[Table: …]``, and anchored
forms.

**Why:** A checker recognizing fewer forms than the renderer emits
reports false gaps and buries real ones; mirroring the output classes
keeps MISSING an honest signal.

**Consequences:** Checker totals are comparable across corpora
regardless of render path; any new render form must extend the checker
in the same change.

_Promoted from strand: table-fidelity on 2026-08-17._

## D-202: Bounds-first req-id over-capture guard with a profile-tunable word bound
**Status**: Active · **Date**: 2026-08-17.

**Context:** A permissive plan-token class under a one-sided anchor can
run past the real id into surrounding prose (any prose ending in
``-<digits>`` completes the match); whitespace canonicalization welds
the capture into one silently corrupted token. Field-validated: 354
corrupted served rows on one corpus, 1 per cell on another. The first
guard deployment attempted recovery on every whitespace-bearing
capture with a fixed 3-word bound — and field validation rejected it:
~220 legitimate multi-word-plan ids lost per cell, and "shortest slice
that fullmatches" recovery could silently truncate a legitimate id
whose plan token carries an interior ``-<digits>`` segment.

**Decision:** Every raw pattern match passes a bounds-first guard
before canonicalization: an in-bound capture (≤
``requirement_id.guard_max_words`` whitespace-separated words — a
profile knob, module default 6 — and ≤64 chars) passes UNTOUCHED,
never recovered, never rejected; only over-bound captures get
anchored-side weld recovery (recovered slice itself in-bound), else a
loud no-id skip. Recovery is side-aware: end-anchored captures
(trailing rescue, TOC tail-peel) recover from the suffix side so a
mid-text citation is never resurrected.

**Why:** In-bound pass-through eliminates the truncation class
structurally rather than by tuning; a visible no-id skip beats an
invisible wrong id; and corpus inventory width is corpus data — it
belongs in the profile (retunable without a code round-trip), not in
code, which is exactly where the data-blind first deployment went
wrong.

**Consequences:** Profiles for corpora with narrow inventories should
pin the knob below the default (one profile pins 5 from measured
inventory); the checker must read the same knob (D-204); two
ParseStats counters (``req_id_over_captures_recovered`` /
``req_id_captures_rejected``) surface guard activity per parse; the
single-id weld shape (id + prose within the bound) is accepted
residue, governed by the bound and profile class discipline.

_Promoted from strand: id-precision on 2026-08-17._

## D-203: Containment trigger: two complete ids in one capture recover bounds-independently
**Status**: Active · **Date**: 2026-08-17.

**Context:** Field experiment (a knob-tightening test) proved the
``id + prose + id`` weld capture can be NARROWER than the corpus's
widest legitimate id — the weld sat at ≤4 raw words while legitimate
inventory reaches 5 — so no value of the word bound separates them.
Segment arithmetic was also ruled unsafe as a bound basis (single-word
ids can carry literal underscores).

**Decision:** A capture whose proper whitespace-bounded PREFIX slice
AND proper SUFFIX slice each fullmatch the id pattern holds at least
two complete ids and triggers anchored-side recovery regardless of
bounds — checked before the in-bound pass-through.

**Why:** Two complete ids in one capture is a structural fact about
the capture, not a width heuristic — definitionally not one id.
Field-measured on a full cell inventory: the weld capture counts 2,
all 10753 legitimate ids count exactly 1, zero false positives (only
one edge of a legitimate id carries the literal prefix, so its edges
never both fullmatch). Equivalent to a non-greedy ≥2-match count for
prefix-bearing patterns, and it reuses the existing slice machinery.

**Consequences:** The bounds (D-202) and containment are
complementary: bounds catch id + long prose, containment catches
id + prose + id; the single-id in-bound weld shape remains accepted
residue. The 0-false-positive result is measured on one corpus's
inventory; the trigger is generic but its false-positive claim should
be re-checked when a corpus's pattern lacks a literal prefix.

_Promoted from strand: id-precision on 2026-08-17._

## D-204: Parser and checker share one guard function and one knob
**Status**: Active · **Date**: 2026-08-17.

**Context:** 354 corrupted served ids survived every standing check
because the recall checker scanned extract-side text with the SAME
unguarded pattern semantics as the parser — parser and checker
over-captured in agreement, so the corruption was invisible to the
tool meant to find it. Guarding only the parser would instead surface
every weld as a false MISSING on the extract side.

**Decision:** The guard is a module-level function
(``guard_req_id_capture``) in the parser module; the extract-side
recall checker imports it and reads the word bound from the SAME
profile knob (``requirement_id.guard_max_words``) it passes to the
parser.

**Why:** Divergence between the two sides is the failure mode — shared
blindness hides corruption, one-sided guarding manufactures false
gaps. A single shared function with a single shared knob makes
divergence structurally impossible instead of procedurally avoided.

**Consequences:** The sandbox checker imports from core (an accepted
sandbox→core dependency, consistent with existing sandbox tooling);
any future change to guard semantics automatically applies to both
sides; checker id-diff baselines shift only when guard semantics
change, and such changes must expect a re-baseline round.

_Promoted from strand: id-precision on 2026-08-17._

## D-205: dedup repair command: newest-wins duplicate-row repair for resume traces
**Status**: Active · **Date**: 2026-08-17.

**Context:** Repeated or interrupted enrich invocations append
duplicate kept + enrichment records for the same doc (field-observed
via single-req smoke reruns, with DIFFERENT phrase sets per record).
``verify-run --strict`` FAILs on duplicate kept rows, but no repair
command addressed the state: heal-torn sees no torn lines or orphans
(the duplicate rows are well-formed and paired), prune skips unchanged
docs, retry-failed only evicts failed rows. The field repair was
manual, under one-off authorization.

**Decision:** ``sira_incremental dedup`` — keep only the NEWEST row
per resume key (doc_id for doc-enrich, (query_id, doc_id) for rerank)
in the stage's kept trace and enrichments file, dropping earlier
duplicates; temp + atomic-rename rewrite (never in place — promoted
snapshots share files by hardlink); ``--dry-run`` reports without
rewriting; unparseable and None-keyed lines are kept defensively.

**Why:** A strict verifier that can FAIL on a state the tooling cannot
repair forces ad-hoc manual file surgery on resume-critical files —
the command encodes the field-proven procedure (keep newest, both
files, atomic) so the failure mode is reachable-but-repairable.
Newest-wins because rows append in run order: the last record is the
latest enrichment.

**Consequences:** The repair surface for run dirs is now
prune / retry-failed / heal-torn / dedup, each owning a disjoint
state class; dedup is repair-only (does not prevent duplicates —
prevention would need invocation-level locking, out of scope);
operational note — the command ships in the sira-batch image, so
sandbox-side changes require rebaking BOTH images.

_Promoted from strand: id-precision on 2026-08-17._
