# Session Summary: Telecom Requirements AI System Design

**Date:** April 11-12, 2026
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
3. **Deterministic structural parsing** — rule-based parsers per MNO format; LLM only for enrichment (concept tagging, relationship classification)
4. **Standards ingestion: Option C (Hybrid Selective)** — only ingest referenced 3GPP sections + surrounding context (parent, definitions, adjacent subsections), not full specs
5. **Bottom-up feature taxonomy** — derive from documents using LLM, then human review; not pre-defined
6. **Two cross-document patterns:** (a) Fragmented — one capability across multiple docs, handled by Feature nodes; (b) Dependent — requirement X needs requirement Y from another doc, handled by typed edges
7. **Four standards relationship types:** DEFER, CONSTRAIN, OVERRIDE, EXTEND — with pre-computed delta summaries
8. **Keep RAG** — don't skip to context stuffing even though context window may be large; production LLM context may be smaller
9. **Single unified graph + vector store** — not MxN partitioned; enables cross-MNO comparison and cross-release diff as natural traversals; shared standards and feature nodes
10. **Per-MNO parser registry** — each MNO gets dedicated parser; VZW parser built for PoC
11. **Test cases as first-class graph citizens** — separate parser, Test_Case nodes, tested_by/tests edges, doc_type metadata in vector store
12. **Release-specific standards versioning** — different MNOs may reference different 3GPP releases; separate Standard_Section nodes per release
13. **Multi-format support (PDF, DOCX, XLS/XLSX)** — format-aware extraction layer produces normalized intermediate representation; supports embedded OLE objects (XLSX in DOCX, etc.) and images/diagrams
14. **Folder-structure-driven metadata** — `/<MNO>/<Release>/Requirements/`, `/<MNO>/<Release>/TestCases/`, `/Standards/<Spec>/<Release>/`

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

The complete Technical Design Document is at `TDD_Telecom_Requirements_AI_System.md` (v0.3). It contains:
- Full architecture diagrams (ingestion + query pipelines)
- Detailed ingestion pipeline (7 stages including multi-format extraction)
- Knowledge graph model (8 node types, 15+ edge types)
- Query pipeline (6 stages including MNO/release resolution)
- All target capabilities with query flow examples
- PoC plan (10 steps) with evaluation criteria
- 15 identified risks with mitigations

---

## Where We Left Off

**Status:** Design phase COMPLETE. Ready to start PoC implementation.

**Next step:** PoC Step 1 — Document content extraction
- Need working Python environment with POSIX shell (was setting this up when session ended)
- Packages needed: `pymupdf pdfplumber python-docx openpyxl olefile Pillow`
- Start by extracting text + tables from the 5 VZW PDF files in the repo
- Build the normalized intermediate representation (Section 5.1.7 of TDD)

**Git repo initialized** in the working directory. TDD and CLAUDE.md should be committed.

---

## Memory Files

The design decisions, project context, VZW document structure analysis, and collaboration preferences are saved in Claude Code's memory system. They should auto-load in a new session if the working directory path is the same. If the directory was renamed, tell Claude to check `SESSION_SUMMARY.md` and `TDD_Telecom_Requirements_AI_System.md` for full context.
