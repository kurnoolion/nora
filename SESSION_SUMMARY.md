# Session Summary: Telecom Requirements AI System Design

**Date:** April 11-13, 2026
**Purpose:** Feed this to Claude Code at the start of a new session to resume where we left off.

---

## Who You Are

You are my Applied AI Software Design Partner. We work collaboratively — structured decomposition before solutions, checkpoint-based progress, transparent reasoning, honest about uncertainty. Don't jump to code without aligning on design first. Push back when something doesn't look right.

I'm an AI solution architect with deep telecom domain expertise (3GPP, GSMA, US MNO device requirements).

---

## What We're Building

An AI system for intelligent querying, cross-referencing, and compliance analysis of US MNO (Verizon, AT&T, T-Mobile) device requirement specifications.

**Core architecture:** Knowledge Graph + RAG hybrid
- Graph scoping identifies WHERE to look (cross-doc, cross-MNO traversal)
- Targeted vector RAG ranks WHAT's most relevant within that scope
- Requirement hierarchy provides structural CONTEXT for LLM synthesis

**Why not pure RAG:** Already tried, failed. Can't handle cross-document dependencies, destroys hierarchical structure, misses standards context, no MNO/release awareness.

---

## How We're Building

You are a senior engineering partner. We're building software solution that we designed together — you think, reason, and push back like a collaborator, not an instruction executor.

Code quality:
- Write code incrementally. Don't produce large blocks hoping everything works together. Build up piece by piece.
- When making design choices, explain your reasoning briefly. "I chose X because Y" helps me evaluate the decision.
- If you're not sure about an approach, say so and propose alternatives. Uncertainty about architecture is normal — hiding it causes bugs.

Collaboration:
- If my requirements are ambiguous or seem to conflict, ask before building. A 30-second clarification beats a 30-minute rewrite.
- If you see a problem with my approach — an edge case I missed, a simpler alternative, a potential footgun — flag it. I want a second pair of eyes, not a yes-machine.
- When a task is complex, suggest a plan before diving into code. "Here's how I'd break this down" is a valuable first response.

Debugging:
- When debugging, think out loud. Walk through hypotheses, eliminate possibilities, explain what you're checking and why.
- If you can't identify the issue, say so and describe what you've ruled out. That narrows the search even if it doesn't solve the problem.
- Add debug instrumentation so that you can analyze the logs I provided during debug session, find root cause and fix.
- Add instrumentation for key performance KPIs like accuracy, RAM/CPU/disk usages (peak & Average), request per second, user response time, LLM API response time etc, that will be monitored and used for potential optimizations (Caching, scaling etc). These KPIs shall be stored in a persistent DB.

Dealing with new requirements:
- Review how the design is impacted.
- Proactively work with me picking right design choices
- Once we mutually agree on the choices, update design document
- Then move on to incremental implementation of design delta, regression testing and new requirement implementation.

Honesty:
- If you're not familiar with a library, framework, or pattern I'm using, say so rather than guessing at its API.
- If code I've written has a problem, tell me directly. Diplomatic honesty beats silent compliance.
- "I'm not sure this is the best approach, but here's my reasoning" is a great response. Use it.

---

## Key Design Decisions Made

1. **KG + RAG over pure RAG** — graph is the routing layer, RAG does fine-grained ranking
2. **Graph scoping then targeted RAG** — not graph OR RAG, but graph THEN RAG within scoped candidate set
3. **Profile-driven structural parsing** — standalone DocumentProfiler derives document structure from representative docs (no LLM); generic structural parser applies the profile; LLM only for enrichment (concept tagging, relationship classification)
4. **Standards ingestion: Option C (Hybrid Selective)** — only ingest referenced 3GPP sections + surrounding context (parent, definitions, adjacent subsections), not full specs
5. **Bottom-up feature taxonomy** — derive from documents using LLM, then human review; not pre-defined
6. **Two cross-document patterns:** (a) Fragmented — one capability across multiple docs, handled by Feature nodes; (b) Dependent — requirement X needs requirement Y from another doc, handled by typed edges
7. **Four standards relationship types:** DEFER, CONSTRAIN, OVERRIDE, EXTEND — with pre-computed delta summaries
8. **Keep RAG** — don't skip to context stuffing even though context window may be large; production LLM context may be smaller
9. **Single unified graph + vector store** — not MxN partitioned; enables cross-MNO comparison and cross-release diff as natural traversals; shared standards and feature nodes
10. **DocumentProfiler + generic parser** — replaces per-MNO parser registry; DocumentProfiler is standalone, LLM-free, outputs human-editable JSON profile; generic parser applies profile to any MNO's docs; adding new MNO = profile representative docs, no code changes
11. **Test cases as first-class graph citizens** — separate parser, Test_Case nodes, tested_by/tests edges, doc_type metadata in vector store
12. **Release-specific standards versioning** — different MNOs may reference different 3GPP releases; separate Standard_Section nodes per release
13. **Multi-format support (PDF, DOC, DOCX, XLS, XLSX)** — format-aware extraction layer produces normalized intermediate representation; DOC converted to DOCX via LibreOffice headless; supports embedded OLE objects and images/diagrams
14. **Folder-structure-driven metadata** — `/<MNO>/<Release>/Requirements/`, `/<MNO>/<Release>/TestCases/`, `/Standards/<Spec>/<Release>/`
15. **LLM abstraction via Protocol** — `LLMProvider` Protocol in `src/llm/base.py` is the only LLM interface; any class with matching `complete()` method works (structural typing, no inheritance); swap providers by passing a different instance
16. **Configurable vector store** — embedding model, vector DB backend, distance metric, and chunk contextualization are all configurable via `VectorStoreConfig` (JSON-serializable). `EmbeddingProvider` and `VectorStoreProvider` Protocols follow same pattern as `LLMProvider`. Supports experimentation with different models/metrics to find best accuracy/speed tradeoff.

---

## Capabilities the System Must Support

### Requirement Q&A Bot
- Single-doc Q&A
- Cross-doc Q&A (query needs multiple requirement docs to answer)
- Cross-MNO comparison ("compare VZW vs TMO IMS registration")
- Standards comparison ("how does VZW differ from 3GPP for T3402?")
- Release diff / version comparison ("what changed in VZW eSIM from Oct 2025 to Feb 2026?")
- Traceability (requirement to test case mapping)

### Test Case Q&A
- Lookup test cases by requirement
- Test case content queries
- Test coverage analysis

### Compliance Agent (post-PoC)
- Single requirement compliance check against Excel compliance sheets
- Cross-document compliance consistency
- Auto-fill from module/chipset documentation
- Delta compliance sheet generation between releases

---

## Document Structure (VZW OA — analyzed from LTEDATARETRY.pdf)

- **Req ID format:** `VZ_REQ_{PLANID}_{NUMBER}` (e.g., VZ_REQ_LTEDATARETRY_7748)
- **Section hierarchy:** Up to 6+ levels (e.g., 1.4.3.1.1.10)
- **Every section IS a requirement** with its own VZ_REQ ID
- **Document zones:** 1.1-1.2 (meta), 1.3 (software specs/timers/algorithms), 1.4 (scenarios — bulk of requirements)
- **Cross-references:** 3GPP TS citations with section and release, other VZW plan names
- **Each doc references specific 3GPP release** (captured in Introduction/References section)
- **136 pages** for LTEDATARETRY alone

---

## Constraints

- **PoC:** Claude or Gemini API, personal PC, publicly available VZW docs only (5 PDFs in repo)
- **Production:** Proprietary on-premise LLM (supports thinking mode, context TBD maybe 2M), no external LLMs/cloud AI
- **Some flexibility** for open-source on-premise models
- **Scale:** PoC = 5 docs; Production = multiple MNOs x quarterly releases x hundreds of docs = GBs

---

## Design Document

The complete Technical Design Document is at `TDD_Telecom_Requirements_AI_System.md` (v0.4). It contains:
- Full architecture diagrams (ingestion + query pipelines)
- Detailed ingestion pipeline (7 stages including multi-format extraction)
- Knowledge graph model (8 node types, 15+ edge types)
- Query pipeline (6 stages including MNO/release resolution)
- All target capabilities with query flow examples
- PoC plan (11 steps, including DocumentProfiler) with evaluation criteria
- 15 identified risks with mitigations

---

## PoC Implementation Progress

### Completed Steps

#### PoC Step 1 — Document Content Extraction (DONE, committed)
- **Code:** `src/extraction/` (pdf_extractor.py, base.py, registry.py, extract.py)
- **Data model:** `src/models/document.py` — Normalized IR (DocumentIR, ContentBlock, FontInfo, Position, BlockType)
- **Output:** `data/extracted/*_ir.json` — 5 VZW docs extracted
- **Key design:** pymupdf for text+font metadata, pdfplumber for tables, table-region deduplication, font-group splitting for mixed-font blocks, header/footer filtering
- **CLI:** `python -m src.extraction.extract <path-or-dir>`

#### PoC Step 2 — DocumentProfiler (DONE, committed)
- **Code:** `src/profiler/` (profiler.py, profile_schema.py, profile_cli.py)
- **Output:** `profiles/vzw_oa_profile.json` — VZW OA profile derived from LTEDATARETRY + LTEB13NAC
- **Validated** against 3 held-out docs (LTESMS, LTEAT, LTEOTADM) — all passed with 0 warnings
- **Key design:** Font size clustering for heading detection, regex mining for req IDs and metadata, frequency analysis for body text, zone classification by keyword matching
- **CLI:** `python -m src.profiler.profile_cli create|update|validate`

#### PoC Step 3 — Generic Structural Parser (DONE, committed)
- **Code:** `src/parser/` (structural_parser.py, parse_cli.py)
- **Output:** `data/parsed/*_tree.json` — 5 VZW docs parsed into RequirementTree structures
- **Key classes:** GenericStructuralParser, RequirementTree, Requirement, CrossReferences, StandardsRef, TableData, ImageRef
- **CLI:** `python -m src.parser.parse_cli --profile <profile> --doc <ir.json> --output <tree.json>`

#### PoC Step 5 — Cross-Reference Resolver (DONE, committed)
- **Code:** `src/resolver/` (resolver.py, resolve_cli.py)
- **Output:** `data/resolved/*_xrefs.json` — per-document cross-reference manifests
- **Three resolution types:** internal (same tree), cross-plan (other trees in corpus), standards (3GPP TS citations)
- **Key classes:** CrossReferenceResolver, CrossReferenceManifest, ResolvedInternalRef, ResolvedCrossPlanRef, ResolvedStandardsRef
- **CLI:** `python -m src.resolver.resolve_cli --trees-dir data/parsed --output-dir data/resolved`

#### PoC Step 6 — Feature Taxonomy (DONE, committed)
- **LLM abstraction:** `src/llm/` (base.py — LLMProvider Protocol, mock_provider.py — MockLLMProvider with keyword catalog)
- **Feature extraction:** `src/taxonomy/extractor.py` — per-document LLM-driven feature extraction
- **Consolidation:** `src/taxonomy/consolidator.py` — cross-document feature merge and deduplication
- **Data model:** `src/taxonomy/schema.py` — Feature, DocumentFeatures, TaxonomyFeature, FeatureTaxonomy
- **Output:** `data/taxonomy/*_features.json` (per-doc) + `data/taxonomy/taxonomy.json` (unified)
- **CLI:** `python -m src.taxonomy.taxonomy_cli --trees-dir data/parsed --output-dir data/taxonomy`
- **LLM swap:** Any class with `complete(prompt, system, temperature, max_tokens) -> str` satisfies the Protocol. See `src/llm/base.py` for documentation.

#### PoC Step 7 — Standards Ingestion (DONE, committed)
- **Reference collector:** `src/standards/reference_collector.py` — scans cross-ref manifests AND requirement tree text for section-level 3GPP references; aggregates by (spec, release)
- **Spec resolver:** `src/standards/spec_resolver.py` — maps spec+release to 3GPP FTP download URL using version encoding conventions (release 8→"8xx", 10→"axy", 11→"bxy", etc.); probes FTP directory listings to find latest version per release
- **Downloader:** `src/standards/spec_downloader.py` — downloads ZIP from 3GPP FTP, extracts DOC/DOCX, auto-converts DOC→DOCX via LibreOffice headless, local caching under `data/standards/TS_{spec}/Rel-{N}/`
- **Spec parser:** `src/standards/spec_parser.py` — parses 3GPP DOCX into section tree using Heading styles; handles numbered sections, annexes, definitions; extracts metadata from filename version codes
- **Section extractor:** `src/standards/section_extractor.py` — extracts referenced sections + parent + siblings + definitions for contextual completeness
- **Data model:** `src/standards/schema.py` — SpecReference, AggregatedSpecRef, StandardsReferenceIndex, SpecSection, SpecDocument, ExtractedSpecContent
- **Output:** `data/standards/reference_index.json` + `data/standards/TS_{spec}/Rel-{N}/{spec_parsed.json, sections.json}`
- **CLI:** `python -m src.standards.standards_cli --manifests-dir data/resolved --trees-dir data/parsed --output-dir data/standards`
- **Generic design:** No hardcoded spec lists — all specs and versions derived from how they're referenced in MNO requirement documents. Works for any MNO. No LLM required.

### Code Review & Bug Fixes (completed after Step 3)

Thorough review of all 3 PoC steps with fresh eyes, identified and fixed 6 bugs:

1. **Resource leak in PDFExtractor** — `fitz_doc` and `plumber_pdf` not closed on exception. Fixed with try/finally wrapping extraction into `_extract_impl`.
2. **Hardcoded `VZ_REQ` broke generic design** — Profiler's `_detect_cross_references` and parser's `_extract_cross_refs` hardcoded VZ_REQ instead of using detected patterns. Fixed: profiler passes detected req ID pattern to cross-ref detection; parser uses profile `components` config (separator + plan_id_position) to extract plan IDs from any MNO's req IDs.
3. **Trailing dot in 3GPP spec numbers** — `[\d.]+` regex greedily captured sentence-ending dots (e.g., "3GPP TS 24.301." → captured "24.301."). Fixed with `\d[\d.]*\d` across all spec-parsing regexes.
4. **SUPPORTED_EXTENSIONS mismatch** — CLI advertised .doc/.docx/.xls/.xlsx but registry only had .pdf. Fixed: CLI now reads from `registry.supported_extensions()`.
5. **Profiler header/footer detection was a no-op** — Profiler tried to detect h/f from IR blocks that already had h/f stripped by the extractor. Replaced `_detect_header_footer` with `_collect_header_footer` that reads patterns from extractor's `extraction_metadata`.
6. **`bbox` tuple→list on JSON round-trip** — `DocumentIR.load_json` returned bbox as list (from JSON) instead of tuple. Found by round-trip tests. Fixed with explicit `tuple()` conversion.

Also removed dead code: unused `metadata: dict = {}` and unnecessary `block_type` intermediate variable in PDFExtractor.

### Test Suite (378 tests, all passing)

| File | Tests | Coverage |
|---|---|---|
| `test_document_ir.py` | 10 | DocumentIR serialize/deserialize round-trip, all block types, positions, metadata |
| `test_profile_schema.py` | 9 | DocumentProfile round-trip for every nested structure, loads real VZW profile |
| `test_patterns.py` | 39 | Section numbering, req IDs, plan ID extraction, 3GPP spec numbers (trailing dot regression), h/f patterns |
| `test_pipeline.py` | 30 | End-to-end extract→profile→parse on real PDFs, cross-ref consistency, parent-child link integrity |
| `test_resolver.py` | 19 | Internal/cross-plan/standards resolution, summary counts, manifest round-trip, pipeline integration |
| `test_taxonomy.py` | 40 | MockLLMProvider protocol/keyword matching, FeatureExtractor prompt/parse, TaxonomyConsolidator merge/dedup, schema round-trips, full pipeline integration |
| `test_standards.py` | 35 | Spec resolver encoding/URLs, reference collector helpers + integration, spec parser metadata/sections/ancestry, section extractor selection, schema round-trips |
| `test_graph.py` | 48 | Schema ID generation, requirement/xref/standards/feature graph builders with synthetic data, serialization round-trips, full build with synthetic data, integration tests on real data (connectivity, traversals) |
| `test_vectorstore.py` | 57 | Config round-trip, protocol conformance (EmbeddingProvider, VectorStoreProvider), ChunkBuilder contextualization/metadata/tables/images/toggles, deduplication, Builder orchestration with mock providers, integration tests on real parsed data |
| `test_query.py` | 55 | Schema models, MockQueryAnalyzer (entities/concepts/MNOs/features/plans/query types), MNOReleaseResolver, GraphScoper (entity/feature/plan/title lookup + edge traversal), RAGRetriever (scoped + metadata retrieval + diversity), ContextBuilder (enrichment + formatting), synthesizer citations, pipeline orchestration, integration with synthetic graph |
| `test_eval.py` | 36 | Question set structure (counts, categories, IDs, ground truth), metric scoring (perfect/zero/partial/hallucination), score/report serialization, A/B comparison logic, runner integration with synthetic graph + mock store, overall score weighting |

Note: `test_pipeline.py` (30 tests) requires `pymupdf`; `test_standards.py` spec parser tests (6) require a downloaded spec DOCX; `test_graph.py` (48) requires `networkx`; `test_query.py` (55) requires `networkx`; `test_eval.py` (36) requires `networkx`; `test_vectorstore.py` integration tests (7) require parsed/taxonomy data. The remaining tests run without external dependencies.

### Known Design Concerns (deferred)

- **Heading levels are misleading** — Profile shows 3 levels all at 13.5-14.5pt differentiated by bold/caps, but these don't map to section depth. Parser correctly uses section numbering for hierarchy, so not a functional bug, but the profile data is confusing.
- **`update_profile` is incomplete** — Only updates req IDs, cross-refs, and zones. Doesn't re-analyze heading levels, body text, or plan metadata.
- **Req ID tables captured as data tables** — Some sections have small tables that are just formatting artifacts around requirement IDs, not actual data tables.
- **Mock provider feature accuracy** — MockLLMProvider uses keyword matching which is approximate. Real LLM will produce more domain-accurate feature extractions. IMS_REGISTRATION appearing in all 5 docs is a mock artifact (many docs contain "registration" in headings).

#### PoC Step 8 — Knowledge Graph Construction (DONE, committed)
- **Schema:** `src/graph/schema.py` — 6 node types (MNO, Release, Plan, Requirement, Standard_Section, Feature), 10 edge types across 5 categories (organizational, within-doc, cross-doc, standards, feature), deterministic ID generation functions
- **Builder:** `src/graph/builder.py` — 7-step construction: MNO/Release/Plan → Requirement nodes + parent_of hierarchy → depends_on edges from xref manifests → Standard_Section nodes from extracted sections + references_standard edges → Feature nodes + maps_to edges (two-pass: nodes first, then edges) → shared_standard edges (cross-plan only)
- **Data model:** `GraphStats` dataclass for summary statistics with JSON serialization
- **Serialization:** JSON (node-link format via `nx.node_link_data`) and GraphML (with list/dict attrs converted to JSON strings)
- **CLI:** `python -m src.graph.graph_cli --verify` — builds graph + runs 7 diagnostic queries (reqs per plan, feature coverage, most-referenced standards, cross-plan deps, shared standards, connectivity, path examples)
- **Graph stats (real data):** 1,078 nodes (1 MNO, 1 Release, 5 Plans, 705 Requirements, 350 Standard_Sections, 16 Features), 11,732 edges (663 parent_of, 705 belongs_to, 300 depends_on, 326 references_standard, 9,260 maps_to, 204 shared_standard, 268 parent_section, 5 contains_plan, 1 has_release), 22 connected components with 98.1% in largest
- **Output:** `data/graph/knowledge_graph.json` + `data/graph/graph_stats.json`
- **Note on maps_to granularity:** MockLLMProvider produces coarse feature mappings (all reqs in a plan → plan's features). Real LLM would refine to specific requirement subsets. The 9,260 edges are expected mock behavior.

#### PoC Step 9 — Vector Store Construction (DONE, committed)
- **Protocols:** `src/vectorstore/embedding_base.py` — `EmbeddingProvider` Protocol (embed, embed_query, dimension, model_name); `src/vectorstore/store_base.py` — `VectorStoreProvider` Protocol (add, query, count, reset) + `QueryResult` dataclass
- **Config:** `src/vectorstore/config.py` — `VectorStoreConfig` dataclass with all tuneable parameters (embedding model/provider/batch_size/device/normalize, vector store backend/metric/collection, chunk contextualization toggles, extra dict for provider-specific settings). Loads from / saves to JSON for reproducible experiments.
- **Chunk builder:** `src/vectorstore/chunk_builder.py` — converts each requirement into a contextualized text chunk following TDD 5.9 format: [MNO/Release/Plan/Version] header + [Path: hierarchy] + [Req ID] + title + body text + tables as Markdown + image captions. Each chunk carries metadata (mno, release, doc_type, plan_id, req_id, section_number, zone_type, feature_ids).
- **Embedding provider:** `src/vectorstore/embedding_st.py` — `SentenceTransformerEmbedder` using sentence-transformers library. Configurable model name, device, batch size, normalization. No API key needed.
- **Vector store backend:** `src/vectorstore/store_chroma.py` — `ChromaDBStore` using ChromaDB with persistent storage. Configurable distance metric (cosine/l2/ip), collection name. Metadata sanitization (list/dict → JSON strings for ChromaDB compatibility) with deserialization on query.
- **Builder:** `src/vectorstore/builder.py` — `VectorStoreBuilder` orchestrates: load trees + taxonomy → build chunks → deduplicate by ID (keeps longer text) → batch embed → store. `BuildStats` dataclass for summary statistics.
- **CLI:** `python -m src.vectorstore.vectorstore_cli` — supports `--config` JSON file + CLI flag overrides (--model, --metric, --backend, --device, etc.), `--rebuild`, `--info`, `--query` with `--filter-plan`/`--filter-mno`, `--save-config`. Saves config + stats alongside vector store data for reproducibility.
- **Deduplication:** Builder deduplicates chunks with same ID (parser artifact: VZ_REQ_LTEAT_33081 appears in two sections), keeping the chunk with more text content. 706 raw chunks → 705 after dedup.
- **Design:** All components are configurable and swappable via Protocols — adding a new embedding model or vector store backend requires no changes to existing code, just a new class matching the Protocol.

#### PoC Step 10 — Query Pipeline (DONE, committed)
- **Schema:** `src/query/schema.py` — Query pipeline data models: `QueryType` (8 types: SINGLE_DOC, CROSS_DOC, CROSS_MNO_COMPARISON, RELEASE_DIFF, STANDARDS_COMPARISON, TRACEABILITY, FEATURE_LEVEL, GENERAL), `DocTypeScope` (3 scopes), `QueryIntent`, `MNOScope`, `ScopedQuery`, `CandidateNode`/`CandidateSet`, `RetrievedChunk`, `StandardsContext`/`ChunkContext`/`AssembledContext`, `Citation`, `QueryResponse` (with `save_json()`)
- **Stage 1 — Query Analysis:** `src/query/analyzer.py` — `MockQueryAnalyzer` uses keyword matching (MNO aliases, plan aliases, feature keywords, 3GPP spec patterns, req ID patterns, timer/cause code entity extraction, telecom concept patterns); `LLMQueryAnalyzer` uses structured JSON prompt with fallback to mock on parse failure
- **Stage 2 — MNO/Release Resolution:** `src/query/resolver.py` — `MNOReleaseResolver` discovers available MNOs/releases from graph nodes; resolution rules: explicit MNO+release → use as-is; MNO only → latest release; no MNO → all available; "latest" → first in sorted list; fuzzy release matching (exact, substring, year+month)
- **Stage 3 — Graph Scoping:** `src/query/graph_scope.py` — `GraphScoper` with configurable max_depth; default depth per query type (1 for single_doc, 2 for cross_doc/feature/general); 4 lookup strategies (entity, feature, plan, title search); BFS edge traversal with allowed edge types per query type, bidirectional, score decay (0.7^depth), scope filtering by MNO/release
- **Stage 4 — Targeted RAG:** `src/query/rag_retriever.py` — `RAGRetriever` with configurable top_k and diversity_min_per_plan; scoped retrieval (filter by graph candidate req_ids using `$in`) vs metadata retrieval (filter by MNO/release); diversity enforcement ensures minimum chunks per plan; handles large candidate sets (>500) by retrieving 3x and filtering client-side
- **Stage 5 — Context Assembly:** `src/query/context_builder.py` — `ContextBuilder` enriches chunks with graph context (hierarchy path, parent text, standards references, related req IDs); query-type-specific system prompts; formatted context with provenance headers, hierarchy, standards, cross-refs; strips chunk headers to avoid duplication
- **Stage 6 — LLM Synthesis:** `src/query/synthesizer.py` — `LLMSynthesizer` sends assembled context to LLM, extracts citations via regex (`VZ_REQ_*` IDs and `3GPP TS X.Y, Section Z` patterns using `\d[\d.]*\d` to avoid trailing-dot capture); `MockSynthesizer` returns structured summary grouping by plan with req IDs and standards references
- **Pipeline:** `src/query/pipeline.py` — `QueryPipeline` wires all 6 stages; `query(text, verbose)` runs full pipeline with optional verbose logging of intermediate results; `load_graph(path)` helper
- **CLI:** `src/query/query_cli.py` — `--query`/`-q` single query, `--interactive`/`-i` interactive mode, `--top-k`, `--max-depth`, `--max-context`, `--verbose`/`-v`, `--output`/`-o` JSON export
- **Tests:** 55 tests across 9 test classes (schema, analyzer, resolver, graph scoper, RAG retriever, context builder, synthesizer, pipeline, integration) using synthetic graph and mock vector store

#### PoC Step 11 — Evaluation (DONE, committed)
- **Test questions:** `src/eval/questions.py` — 18 questions across 5 categories (4 single-doc, 4 cross-doc, 4 feature-level, 3 standards comparison, 3 traceability) with `GroundTruth` per question (expected plans, req IDs, features, standards, concepts, min_plans, min_chunks)
- **Metrics:** `src/eval/metrics.py` — `score_question()` computes 5 metrics per question: completeness (plan coverage), accuracy (req ID recall), citation quality (req + standards citations), standards integration (spec mentions), hallucination-free (no fabricated req IDs from unknown plans). `QuestionScore` with weighted overall (0.30/0.25/0.20/0.15/0.10). `EvalReport` with per-category and overall averages.
- **Runner:** `src/eval/runner.py` — `EvalRunner` runs questions through pipeline in two modes: `graph_scoped` (normal) and `pure_rag` (bypass graph scoping, Stage 3 returns empty CandidateSet). `ABComparison` computes wins/losses/ties and per-category deltas.
- **CLI:** `src/eval/eval_cli.py` — `--ab` for A/B comparison, `--category` to filter, `--output` for JSON report, `--verbose` for pipeline details. Displays TDD 9.4 target checks (PASS/FAIL).
- **Pipeline change:** `pipeline.py` — added `_bypass_graph` flag + `CandidateSet` import for pure-RAG evaluation mode
- **Tests:** 36 tests (12 question set structure, 8 metric scoring, 3 serialization, 3 A/B comparison, 7 runner integration, 3 overall score)

### Remaining Steps

4. Test case parsing (separate parser for test case documents)

---

## Where We Left Off

**Status:** PoC Steps 1, 2, 3, 5, 6, 7, 8, 9, 10, and 11 complete. Step 4 (test case parsing) skipped for now. All PoC steps except Step 4 are done.

**What just happened (this session):**
- Completed Step 10 (Query Pipeline) — full 6-stage pipeline with mock implementations for all stages
- Completed Step 11 (Evaluation) — 18 test questions with ground truth, 5 metrics, A/B comparison framework (graph-scoped vs pure RAG)
- Added 91 new tests (55 query + 36 eval) bringing total to 378
- Fixed: standards comparison vs release_diff classification ordering; trailing-dot in citation section regex
- Added `_bypass_graph` to pipeline for pure-RAG A/B comparison mode

**Previous sessions completed:**
- Step 1 (extraction), Step 2 (profiler), Step 3 (parser), code review + 6 bug fixes
- Step 5 (cross-reference resolver), Step 6 (feature taxonomy), Step 7 (standards ingestion)
- Step 8 (knowledge graph construction), Step 9 (vector store construction)

**Immediate next actions:**
1. Install sentence-transformers and chromadb to run the actual vector store build: `pip install sentence-transformers chromadb`
2. Build the vector store: `python -m src.vectorstore.vectorstore_cli`
3. Run end-to-end queries: `python -m src.query.query_cli --query "..." --verbose`
4. Run evaluation: `python -m src.eval.eval_cli --ab --output data/eval/report.json`
5. Experiment with different configs: `--model all-mpnet-base-v2`, `--metric l2`, etc.
6. To download all referenced specs (not just 24.301 and 36.331): `python -m src.standards.standards_cli`
7. When internal LLM is available, swap MockLLMProvider for real provider (see `src/llm/base.py` for instructions)
8. Stale output files in `data/extracted/`, `profiles/`, `data/parsed/` should be regenerated with fixed code (bug fixes from earlier haven't been re-run through the full pipeline)

---

## Project File Structure

```
req-agent/
├── CLAUDE.md                              # Claude Code instructions
├── SESSION_SUMMARY.md                     # This file
├── README.md                              # How to run and test all PoC steps
├── TDD_Telecom_Requirements_AI_System.md  # Full technical design (v0.4)
├── requirements.txt                       # Python dependencies
├── profiles/
│   └── vzw_oa_profile.json               # VZW OA document profile
├── src/
│   ├── models/
│   │   └── document.py                   # Normalized IR data model
│   ├── extraction/
│   │   ├── base.py                       # Abstract extractor
│   │   ├── pdf_extractor.py              # PDF extraction (pymupdf + pdfplumber)
│   │   ├── registry.py                   # Extractor registry + path metadata
│   │   └── extract.py                    # Extraction CLI
│   ├── profiler/
│   │   ├── profile_schema.py             # Profile data model
│   │   ├── profiler.py                   # DocumentProfiler (heuristic analysis)
│   │   └── profile_cli.py               # Profiler CLI
│   ├── parser/
│   │   ├── structural_parser.py          # GenericStructuralParser (profile-driven)
│   │   └── parse_cli.py                  # Parser CLI
│   ├── resolver/
│   │   ├── resolver.py                   # CrossReferenceResolver
│   │   └── resolve_cli.py               # Resolver CLI
│   ├── llm/
│   │   ├── base.py                       # LLMProvider Protocol
│   │   └── mock_provider.py              # MockLLMProvider (keyword-based)
│   ├── taxonomy/
│   │   ├── schema.py                     # Feature taxonomy data model
│   │   ├── extractor.py                  # Per-document feature extraction
│   │   ├── consolidator.py               # Cross-document feature consolidation
│   │   └── taxonomy_cli.py               # Taxonomy CLI
│   ├── standards/
│   │   ├── schema.py                     # Standards ingestion data model
│   │   ├── reference_collector.py        # Reference aggregation from manifests + tree text
│   │   ├── spec_resolver.py              # 3GPP FTP URL resolution
│   │   ├── spec_downloader.py            # Download + cache + DOC→DOCX conversion
│   │   ├── spec_parser.py               # 3GPP DOCX → section tree
│   │   ├── section_extractor.py          # Referenced section + context extraction
│   │   └── standards_cli.py              # Standards CLI
│   ├── graph/
│   │   ├── schema.py                     # Node/edge types, ID generation functions
│   │   ├── builder.py                    # KnowledgeGraphBuilder (7-step construction)
│   │   └── graph_cli.py                  # Graph CLI with --verify diagnostics
│   ├── vectorstore/
│   │   ├── embedding_base.py             # EmbeddingProvider Protocol
│   │   ├── embedding_st.py               # SentenceTransformerEmbedder
│   │   ├── store_base.py                 # VectorStoreProvider Protocol + QueryResult
│   │   ├── store_chroma.py               # ChromaDBStore (persistent ChromaDB)
│   │   ├── config.py                     # VectorStoreConfig (all tuneable params)
│   │   ├── chunk_builder.py              # ChunkBuilder (requirement → contextualized chunk)
│   │   ├── builder.py                    # VectorStoreBuilder (orchestration)
│   │   └── vectorstore_cli.py            # CLI with config support + test queries
│   ├── query/
│   │   ├── schema.py                     # Query pipeline data models (intents, candidates, responses)
│   │   ├── analyzer.py                   # Stage 1: Query analysis (Mock + LLM)
│   │   ├── resolver.py                   # Stage 2: MNO/Release resolution
│   │   ├── graph_scope.py                # Stage 3: Graph scoping (entity/feature/plan/title + BFS)
│   │   ├── rag_retriever.py              # Stage 4: Targeted RAG retrieval with diversity
│   │   ├── context_builder.py            # Stage 5: Context assembly with graph enrichment
│   │   ├── synthesizer.py                # Stage 6: LLM synthesis with citation extraction
│   │   ├── pipeline.py                   # Pipeline orchestrator (6-stage)
│   │   └── query_cli.py                  # CLI (single query, interactive, verbose)
│   └── eval/
│       ├── questions.py                  # 18 test questions with ground truth
│       ├── metrics.py                    # Scoring functions and report aggregation
│       ├── runner.py                     # EvalRunner with A/B comparison
│       └── eval_cli.py                   # CLI (run, --ab, --category, --output)
├── tests/
│   ├── test_document_ir.py               # IR round-trip tests (10)
│   ├── test_profile_schema.py            # Profile round-trip tests (9)
│   ├── test_patterns.py                  # Regex pattern tests (39)
│   ├── test_pipeline.py                  # End-to-end pipeline tests (30, needs pymupdf)
│   ├── test_resolver.py                  # Cross-reference resolver tests (19)
│   ├── test_taxonomy.py                  # Feature taxonomy tests (40)
│   ├── test_standards.py                 # Standards ingestion tests (35)
│   ├── test_graph.py                     # Knowledge graph tests (48)
│   ├── test_vectorstore.py              # Vector store tests (57)
│   ├── test_query.py                    # Query pipeline tests (55)
│   └── test_eval.py                     # Evaluation framework tests (36)
├── data/
│   ├── extracted/                        # IR JSON files (5 docs)
│   ├── parsed/                           # RequirementTree JSON files (5 docs)
│   ├── resolved/                         # Cross-reference manifest JSON files (5 docs)
│   ├── taxonomy/                         # Feature taxonomy JSON files (5 per-doc + 1 unified)
│   ├── standards/                        # Downloaded + parsed 3GPP specs
│   │   ├── reference_index.json          # Aggregated reference index
│   │   └── TS_{spec}/Rel-{N}/            # Per-spec per-release: ZIP, DOCX, parsed, sections
│   ├── graph/                            # Knowledge graph output
│   │   ├── knowledge_graph.json          # Full graph (node-link JSON)
│   │   └── graph_stats.json              # Summary statistics
│   └── vectorstore/                      # Vector store output
│       ├── config.json                   # Config used for build
│       └── build_stats.json              # Build statistics
└── *.pdf                                 # Source PDFs (5 VZW OA docs)
```

---

## Memory Files

The design decisions, project context, VZW document structure analysis, and collaboration preferences are saved in Claude Code's memory system. They should auto-load in a new session if the working directory path is the same. If the directory was renamed, tell Claude to check `SESSION_SUMMARY.md` and `TDD_Telecom_Requirements_AI_System.md` for full context.
