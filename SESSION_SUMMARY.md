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

### Code Review & Bug Fixes (completed after Step 3)

Thorough review of all 3 PoC steps with fresh eyes, identified and fixed 6 bugs:

1. **Resource leak in PDFExtractor** — `fitz_doc` and `plumber_pdf` not closed on exception. Fixed with try/finally wrapping extraction into `_extract_impl`.
2. **Hardcoded `VZ_REQ` broke generic design** — Profiler's `_detect_cross_references` and parser's `_extract_cross_refs` hardcoded VZ_REQ instead of using detected patterns. Fixed: profiler passes detected req ID pattern to cross-ref detection; parser uses profile `components` config (separator + plan_id_position) to extract plan IDs from any MNO's req IDs.
3. **Trailing dot in 3GPP spec numbers** — `[\d.]+` regex greedily captured sentence-ending dots (e.g., "3GPP TS 24.301." → captured "24.301."). Fixed with `\d[\d.]*\d` across all spec-parsing regexes.
4. **SUPPORTED_EXTENSIONS mismatch** — CLI advertised .doc/.docx/.xls/.xlsx but registry only had .pdf. Fixed: CLI now reads from `registry.supported_extensions()`.
5. **Profiler header/footer detection was a no-op** — Profiler tried to detect h/f from IR blocks that already had h/f stripped by the extractor. Replaced `_detect_header_footer` with `_collect_header_footer` that reads patterns from extractor's `extraction_metadata`.
6. **`bbox` tuple→list on JSON round-trip** — `DocumentIR.load_json` returned bbox as list (from JSON) instead of tuple. Found by round-trip tests. Fixed with explicit `tuple()` conversion.

Also removed dead code: unused `metadata: dict = {}` and unnecessary `block_type` intermediate variable in PDFExtractor.

### Test Suite (128 tests, all passing)

| File | Tests | Coverage |
|---|---|---|
| `test_document_ir.py` | 10 | DocumentIR serialize/deserialize round-trip, all block types, positions, metadata |
| `test_profile_schema.py` | 9 | DocumentProfile round-trip for every nested structure, loads real VZW profile |
| `test_patterns.py` | 39 | Section numbering, req IDs, plan ID extraction, 3GPP spec numbers (trailing dot regression), h/f patterns |
| `test_pipeline.py` | 30 | End-to-end extract→profile→parse on real PDFs, cross-ref consistency, parent-child link integrity |
| `test_resolver.py` | 19 | Internal/cross-plan/standards resolution, summary counts, manifest round-trip, pipeline integration |
| `test_taxonomy.py` | 40 | MockLLMProvider protocol/keyword matching, FeatureExtractor prompt/parse, TaxonomyConsolidator merge/dedup, schema round-trips, full pipeline integration |

Note: `test_pipeline.py` (30 tests) requires `pymupdf` which may not be installed in all environments. The remaining 98 tests run without it.

### Known Design Concerns (deferred)

- **Heading levels are misleading** — Profile shows 3 levels all at 13.5-14.5pt differentiated by bold/caps, but these don't map to section depth. Parser correctly uses section numbering for hierarchy, so not a functional bug, but the profile data is confusing.
- **`update_profile` is incomplete** — Only updates req IDs, cross-refs, and zones. Doesn't re-analyze heading levels, body text, or plan metadata.
- **Req ID tables captured as data tables** — Some sections have small tables that are just formatting artifacts around requirement IDs, not actual data tables.
- **Mock provider feature accuracy** — MockLLMProvider uses keyword matching which is approximate. Real LLM will produce more domain-accurate feature extractions. IMS_REGISTRATION appearing in all 5 docs is a mock artifact (many docs contain "registration" in headings).

### Remaining Steps

4. Test case parsing (separate parser for test case documents)
7. Standards ingestion (3GPP spec parsing, selective section extraction)
8. Knowledge Graph construction (Neo4j or similar)
9. Vector store (embeddings for parsed requirement text)
10. Query pipeline (graph scoping + RAG ranking + LLM synthesis)
11. Evaluation (accuracy, coverage, latency benchmarks)

---

## Where We Left Off

**Status:** PoC Steps 1, 2, 3, 5, and 6 complete. Step 4 skipped for now. Ready for Step 7 or other remaining steps.

**What just happened (this session):**
- Completed Step 5 (cross-reference resolver) — resolves internal, cross-plan, and standards references across all parsed trees
- Completed Step 6 (feature taxonomy) — LLM abstraction layer with Protocol-based provider interface, mock provider for testing, per-document feature extraction, cross-document consolidation into unified taxonomy
- Added 59 new tests (19 resolver + 40 taxonomy) bringing total to 128
- Taxonomy output verified: 16 features extracted across 5 VZW docs, each with primary/referenced tracking and MNO coverage

**Immediate next actions:**
1. Move to PoC Step 7 (standards ingestion) or another remaining step
2. When internal LLM is available, swap MockLLMProvider for real provider (see `src/llm/base.py` for instructions)
3. Stale output files in `data/extracted/`, `profiles/`, `data/parsed/` should be regenerated with fixed code (bug fixes from earlier haven't been re-run through the full pipeline)

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
│   └── taxonomy/
│       ├── schema.py                     # Feature taxonomy data model
│       ├── extractor.py                  # Per-document feature extraction
│       ├── consolidator.py               # Cross-document feature consolidation
│       └── taxonomy_cli.py               # Taxonomy CLI
├── tests/
│   ├── test_document_ir.py               # IR round-trip tests (10)
│   ├── test_profile_schema.py            # Profile round-trip tests (9)
│   ├── test_patterns.py                  # Regex pattern tests (39)
│   ├── test_pipeline.py                  # End-to-end pipeline tests (30, needs pymupdf)
│   ├── test_resolver.py                  # Cross-reference resolver tests (19)
│   └── test_taxonomy.py                  # Feature taxonomy tests (40)
├── data/
│   ├── extracted/                        # IR JSON files (5 docs)
│   ├── parsed/                           # RequirementTree JSON files (5 docs)
│   ├── resolved/                         # Cross-reference manifest JSON files (5 docs)
│   └── taxonomy/                         # Feature taxonomy JSON files (5 per-doc + 1 unified)
└── *.pdf                                 # Source PDFs (5 VZW OA docs)
```

---

## Memory Files

The design decisions, project context, VZW document structure analysis, and collaboration preferences are saved in Claude Code's memory system. They should auto-load in a new session if the working directory path is the same. If the directory was renamed, tell Claude to check `SESSION_SUMMARY.md` and `TDD_Telecom_Requirements_AI_System.md` for full context.
