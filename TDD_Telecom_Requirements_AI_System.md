# Technical Design Document: Telecom Requirements AI System

**Version:** 0.3
**Date:** April 2026
**Status:** PoC Design Phase
**Change Log:**
- v0.1 (2026-04-11): Initial design — single MNO, single release
- v0.2 (2026-04-12): Multi-MNO, multi-release, test case ingestion, unified graph, folder structure
- v0.3 (2026-04-12): Multi-format document support (PDF, DOCX, XLS), embedded objects, image/diagram handling

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Problem Statement](#2-problem-statement)
3. [System Constraints](#3-system-constraints)
4. [Architecture Overview](#4-architecture-overview)
   - 4.1 High-Level Architecture
   - 4.2 Unified Graph vs. Partitioned (MxN) Design Decision
   - 4.3 Document Organization and Folder Structure
5. [Ingestion Pipeline](#5-ingestion-pipeline)
   - 5.1 Document Content Extraction
   - 5.2 Structural Parser (Requirements)
   - 5.3 Structural Parser (Test Cases)
   - 5.4 Cross-Reference Extraction
   - 5.5 Standards Specs Ingestion
   - 5.6 Feature Taxonomy Derivation
   - 5.7 Knowledge Graph Construction
   - 5.8 Vector Store Construction
6. [Knowledge Graph Model](#6-knowledge-graph-model)
   - 6.1 Node Types
   - 6.2 Edge Types
   - 6.3 Cross-Document Handling
   - 6.4 Cross-MNO Handling
   - 6.5 Cross-Release Handling
7. [Query Pipeline](#7-query-pipeline)
   - 7.1 Query Analysis
   - 7.2 MNO and Release Resolution
   - 7.3 Graph Scoping
   - 7.4 Targeted Vector RAG
   - 7.5 Context Assembly
   - 7.6 LLM Synthesis
8. [Target Capabilities](#8-target-capabilities)
   - 8.1 Requirement Q&A Bot
   - 8.2 Test Case Q&A
   - 8.3 Requirement Compliance Agent
9. [PoC Plan](#9-poc-plan)
10. [Risks and Mitigations](#10-risks-and-mitigations)

---

## 1. Executive Summary

This document defines the technical design for an AI system that enables intelligent querying, cross-referencing, and compliance analysis of US Mobile Network Operator (MNO) device requirement and test case specifications across **multiple MNOs** (Verizon, AT&T, T-Mobile, etc.) and **multiple quarterly releases**.

The system combines a **unified Knowledge Graph** with **Retrieval-Augmented Generation (RAG)** to overcome the limitations of pure vector-based RAG, which fails to capture the deep inter-dependencies between requirements across documents, MNOs, releases, and referenced telecom standards.

The core architectural insight is: **use a Knowledge Graph to determine WHERE to look, use vector RAG to determine WHAT is most relevant within that scope, and use the requirement hierarchy to provide structural CONTEXT for LLM synthesis.**

A single unified graph and vector store spans all MNOs and releases, with logical partitioning via metadata attributes. This enables cross-MNO comparison queries, cross-release version diffs, and shared standards/feature nodes — all through natural graph traversal without the complexity of merging results from separate stores.

The system will be built incrementally — starting with a Proof of Concept (PoC) on a small set of publicly available VZW requirements using commercial LLMs (Claude/Gemini), then scaling to a production deployment using a proprietary LLM and on-premise infrastructure.

---

## 2. Problem Statement

### 2.1 Background

US Mobile Network Operators (e.g., Verizon, AT&T, T-Mobile) release device requirement specifications quarterly. These documents:

- Are based on telecom standards (3GPP, GSMA, OMA) with MNO-specific customizations
- Cover functional areas such as LTE data retry, SMS, OTA device management, activation, IMS, carrier aggregation, etc.
- Contain Android device preload requirements, platform customizations, and MNO service integration specifications
- Include tables, diagrams, and attachments
- Are accompanied by corresponding test case documents (with their own document format per MNO)
- Follow a consistent internal structure per MNO (e.g., all Verizon OA docs share the same format; AT&T and T-Mobile have their own formats)
- Each MNO release references specific releases of telecom standards (e.g., VZW Feb 2026 may reference 3GPP Release 10, while TMO may reference Release 15)
- Total several hundred megabytes per quarterly release per MNO

### 2.2 Key Challenges

**Cross-document dependencies:** A single logical capability (e.g., device activation) may be defined across multiple requirement documents (SIM, UI, Network, Entitlement). No single document contains the complete picture.

**Requirement inter-dependencies:** Requirements in one document may be functionally dependent on requirements in another document (e.g., Data Retry behavior changes based on whether the device supports SMS over IMS, which is defined in the SMS document).

**Standards layering:** MNO requirements reference specific sections and releases of 3GPP/GSMA standards, with four relationship types — defer to the standard, constrain an optional behavior, override the default, or extend beyond the standard. Many MNO requirements are incomplete without the referenced standards text. Different MNO releases may reference different standards releases.

**Multi-MNO complexity:** Different MNOs have different requirements for the same feature (e.g., IMS registration). Device teams need to understand how MNO requirements compare and where they diverge.

**Version management:** Requirements change quarterly. Tracking deltas between releases is a core use case — both within one MNO (e.g., VZW Feb 2026 vs Oct 2025) and across MNOs.

**Test case traceability:** Each requirement document typically has a corresponding test case document. Users need to trace from requirements to test cases and vice versa, and query test case content directly.

### 2.3 Why Pure RAG Fails

Pure vector-based RAG was evaluated and found inadequate because:

1. **No relationship awareness:** Vector retrieval treats each chunk independently. It cannot follow "this requirement depends on that one in another document."
2. **Undirected retrieval scope:** Semantic search over the entire corpus may return chunks from one document while missing critical related chunks in another.
3. **Destroyed structure:** Chunking destroys the hierarchical parent-child relationships between requirements that carry essential context.
4. **Poor telecom terminology handling:** Standard embedding models were not trained on 3GPP terminology, leading to poor retrieval for acronym-heavy queries.
5. **Missing standards context:** Requirements that say "follow 3GPP TS 24.301 section 5.5.1" are incomplete without the referenced standards text, which pure RAG does not incorporate.
6. **No MNO/release awareness:** Pure RAG has no mechanism to scope results to a specific MNO or release, or to support cross-MNO comparison queries.

---

## 3. System Constraints

| Constraint | Detail |
|-----------|--------|
| **LLM** | Proprietary foundational LLM for production; supports thinking mode; context window size TBD (potentially up to 2M tokens) |
| **Infrastructure** | On-premise deployment; no external LLM or cloud AI services (data privacy/security) |
| **Open source** | Some flexibility to use open-source models on-premise |
| **PoC LLM** | Claude or Gemini (using only publicly available MNO requirements) |
| **PoC environment** | Personal PC |
| **Data sensitivity** | Production data is proprietary MNO requirements; PoC uses only publicly available documents |
| **Scale (production)** | Multiple MNOs (3+), multiple releases per MNO (4+ per year), hundreds of requirement + test case documents per release; total corpus can be several GBs |
| **Scale (PoC)** | 5 Verizon OA documents (Feb 2026 release) |
| **MNO formats** | Each MNO has its own consistent document format for requirements and test cases; separate parsers needed per MNO |
| **Document formats** | Documents may be in PDF, DOCX, or XLS/XLSX format; may contain embedded tables, images, diagrams, and embedded Microsoft documents (OLE objects) |

---

## 4. Architecture Overview

The system consists of two major pipelines: **Ingestion** (offline, document processing) and **Query** (online, user-facing), operating on a **single unified graph and vector store** that spans all MNOs and releases.

### 4.1 High-Level Architecture

```
┌───────────────────────────────────────────────────────────────────────────┐
│                          INGESTION PIPELINE                               │
│                                                                           │
│  Source Folder Structure:                                                  │
│  /<MNO>/<Release>/Requirements/*.{pdf,docx,xlsx}                          │
│  /<MNO>/<Release>/TestCases/*.{pdf,docx,xlsx}                             │
│  /Standards/<Spec>/<Release>/*.pdf  (pre-downloaded)                      │
│                                                                           │
│  ┌───────────┐   ┌──────────────────┐   ┌───────────────┐                │
│  │ MNO Req   │──>│ Content          │──>│  Structural   │                │
│  │ Docs      │   │ Extraction       │   │  Parser       │                │
│  │(pdf/docx/ │   │ (format-aware,   │   │  (per-MNO)    │                │
│  │ xlsx)     │   │  embedded obj    │   └──────┬────────┘                │
│  └───────────┘   │  extraction,     │          │                         │
│                  │  image export,   │   ┌──────┴────────┐                │
│  ┌───────────┐   │  → normalized    │   │  Cross-Ref    │                │
│  │ MNO Test  │──>│  intermediate    │   │  Extraction   │                │
│  │ Case Docs │   │  representation) │   │  (regex+LLM)  │                │
│  └───────────┘   └──────────────────┘   └──────┬────────┘                │
│                                                │                         │
│  ┌───────────┐   ┌──────────────────┐   ┌──────┴────────┐                │
│  │ Standards │──>│ Standards Section│   │  Feature      │                │
│  │ Specs     │   │ Extractor        │   │  Taxonomy     │                │
│  └───────────┘   └────────┬─────────┘   │  Derivation   │                │
│                           │             └──────┬────────┘                │
│                           │                      │                       │
│                           ▼                      ▼                       │
│               ┌────────────────────────────────────────┐                 │
│               │        UNIFIED KNOWLEDGE GRAPH          │                 │
│               │  All MNOs × All Releases × All Docs     │                 │
│               │                                        │                 │
│               │  Requirement nodes (mno, release)      │                 │
│               │  Test Case nodes (mno, release)        │                 │
│               │  Feature nodes (shared across MNOs)    │                 │
│               │  Standards nodes (shared, versioned)   │                 │
│               │  Plan/Release nodes (per MNO)          │                 │
│               └──────────────────┬─────────────────────┘                 │
│                                  │                                       │
│               ┌──────────────────┴─────────────────────┐                 │
│               │        UNIFIED VECTOR STORE             │                 │
│               │  All chunks with metadata filters:      │                 │
│               │  mno, release, doc_type (req/testcase)  │                 │
│               └────────────────────────────────────────┘                 │
└───────────────────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────────────────┐
│                           QUERY PIPELINE                                  │
│                                                                           │
│  User Query                                                               │
│      │                                                                    │
│      ▼                                                                    │
│  ┌────────────────────┐                                                   │
│  │  Query Analysis    │  Extract entities, features, intent               │
│  └────────┬───────────┘                                                   │
│           ▼                                                               │
│  ┌────────────────────┐                                                   │
│  │  MNO & Release     │  Resolve which MNO(s) and release(s)             │
│  │  Resolution        │  Default to latest release if unspecified         │
│  └────────┬───────────┘                                                   │
│           ▼                                                               │
│  ┌────────────────────┐                                                   │
│  │  Graph Scoping     │  Entity lookup + feature lookup +                 │
│  │                    │  edge traversal (filtered by MNO/release)         │
│  │                    │  → candidate set                                  │
│  └────────┬───────────┘                                                   │
│           ▼                                                               │
│  ┌────────────────────┐                                                   │
│  │  Targeted RAG      │  Vector similarity WITHIN candidate set           │
│  │                    │  (metadata-filtered by MNO/release)               │
│  └────────┬───────────┘                                                   │
│           ▼                                                               │
│  ┌────────────────────┐                                                   │
│  │  Context Assembly  │  Chunks + hierarchy + standards +                 │
│  │                    │  MNO/release annotations                          │
│  └────────┬───────────┘                                                   │
│           ▼                                                               │
│  ┌────────────────────┐                                                   │
│  │  LLM Synthesis     │  Answer with citations, MNO-aware                │
│  └────────────────────┘                                                   │
└───────────────────────────────────────────────────────────────────────────┘
```

### 4.2 Unified Graph vs. Partitioned (MxN) Design Decision

**Decision: Single unified graph and vector store, with logical partitioning via node/chunk metadata.**

**Considered alternative:** M (MNOs) × N (releases) separate graphs and vector stores.

**Why unified wins:**

| Concern | Unified Graph | MxN Separate Graphs |
|---------|--------------|-------------------|
| **Cross-MNO comparison** ("VZW vs TMO IMS registration") | Natural — traverse nodes filtered by different MNOs under the same feature | Must query 2 separate graphs and merge results externally |
| **Cross-release diff** ("VZW Feb 2026 vs Oct 2025") | Natural — traverse nodes filtered by different releases | Must query 2 separate graphs and diff externally |
| **Standards sharing** | One set of standards nodes, linked to requirements from all MNOs that reference them | Standards duplicated across MxN graphs |
| **Feature taxonomy sharing** | One taxonomy — "IMS Registration" feature links to VZW, TMO, and ATT requirements | Taxonomy duplicated or maintained separately per graph |
| **Ingestion isolation** | Must handle carefully — ingesting new release should not corrupt existing data | Natural isolation — each graph is independent |
| **Query routing** | One query, metadata-filtered | Must determine which graphs to query, then merge |
| **Scale** | Larger single graph (mitigated by graph DB indexing on mno+release) | Many smaller graphs (operational overhead of managing MxN stores) |

**The unified approach is superior for our use cases** because cross-MNO and cross-release queries are first-class requirements, not edge cases. The shared standards and feature nodes eliminate duplication and enable comparison queries through natural graph traversal.

**Ingestion safety:** New MNO/release ingestion is additive — new nodes and edges are added with the new `mno`+`release` metadata. Existing nodes are not modified. Standards nodes are shared but immutable once created (a 3GPP section doesn't change; if a different release of the standard is referenced, it becomes a new standards node with a different release attribute).

### 4.3 Document Organization and Folder Structure

Source documents are organized in a hierarchical folder structure that maps directly to the graph's logical partitioning:

```
/data/
├── VZW/
│   ├── 2026_Feb/
│   │   ├── Requirements/
│   │   │   ├── LTEDATARETRY.pdf
│   │   │   ├── LTESMS.pdf
│   │   │   ├── LTEAT.pdf
│   │   │   └── ...
│   │   └── TestCases/
│   │       ├── LTEDATARETRY_TC.pdf
│   │       ├── LTESMS_TC.pdf
│   │       └── ...
│   ├── 2025_Oct/
│   │   ├── Requirements/
│   │   └── TestCases/
│   └── ...
├── TMO/
│   ├── 2026_Q1/
│   │   ├── Requirements/
│   │   └── TestCases/
│   └── ...
├── ATT/
│   └── ...
└── Standards/
    ├── 3GPP/
    │   ├── TS_24.301/
    │   │   ├── Release_10/
    │   │   │   └── ts_24301_v10.pdf
    │   │   ├── Release_15/
    │   │   │   └── ts_24301_v15.pdf
    │   │   └── ...
    │   ├── TS_24.008/
    │   └── ...
    ├── GSMA/
    │   └── ...
    └── OMA/
        └── ...
```

**The folder path encodes metadata:** The ingestion pipeline derives `mno`, `release`, and `doc_type` (requirement vs. test case) from the folder path. This eliminates the need for manual metadata entry.

**Standards pre-download:** As a pre-processing step, all referenced standards specs are downloaded and organized by spec ID and release. During ingestion, the cross-reference extractor identifies which spec sections are needed, and the standards section extractor pulls them from the pre-downloaded specs.

---

## 5. Ingestion Pipeline

### 5.1 Document Content Extraction

**Objective:** Extract raw text, tables, images, and embedded objects from MNO requirement and test case documents in multiple formats (PDF, DOCX, XLS/XLSX), producing a **format-normalized intermediate representation** that downstream parsers consume uniformly.

#### 5.1.1 Supported Document Formats

| Format | Typical Usage | Extraction Approach |
|--------|--------------|-------------------|
| **PDF** | Published/finalized requirement specs, standards | `pymupdf` (PyMuPDF) or `pdfplumber` |
| **DOCX** | Working/editable requirement specs, some test plans | `python-docx` for structure; `mammoth` for HTML conversion |
| **XLS/XLSX** | Requirement matrices, compliance sheets, some test cases | `openpyxl` (XLSX) / `xlrd` (XLS) or `pandas` |

**Format detection:** Based on file extension. The extraction layer auto-selects the appropriate extractor.

```python
# Extractor registry pattern
extractors = {
    ".pdf": PDFExtractor(),
    ".docx": DOCXExtractor(),
    ".xlsx": XLSXExtractor(),
    ".xls": XLSExtractor(),
}
```

#### 5.1.2 PDF Extraction

**Tooling:** `pymupdf` (PyMuPDF) or `pdfplumber`.

**Extracts:**
- Text blocks with position/layout information for hierarchy detection
- Tables with preserved row/column structure (using `pdfplumber.extract_tables()` or `pymupdf` table extraction)
- Embedded images exported as image files for downstream processing (see 5.1.6)
- Headers, footers, and page numbers stripped

**Challenge:** PDF is a presentation format — it has no semantic structure. Table detection relies on heuristics (ruled lines, column alignment). Complex nested tables or tables spanning multiple pages may require manual correction.

#### 5.1.3 DOCX Extraction

**Tooling:** `python-docx` for native access to document structure.

**Extracts:**
- Paragraphs with heading styles (Heading 1, Heading 2, etc.) — these directly map to the section hierarchy, making structural parsing significantly more reliable than PDF
- Tables as structured objects with row/column access — far more reliable than PDF table extraction
- Inline images and floating images exported as image files
- **Embedded OLE objects** (see 5.1.5)
- Styles and formatting metadata (bold, italic — may carry semantic meaning like "shall" requirements)
- Numbered lists with nesting levels

**Advantage over PDF:** DOCX preserves the document's semantic structure (headings, lists, table cells). The structural parser receives pre-structured input rather than having to reconstruct structure from visual layout.

#### 5.1.4 XLS/XLSX Extraction

**Tooling:** `openpyxl` (XLSX) or `pandas`.

**Extracts:**
- All sheets with sheet names (sheet names often indicate functional area)
- Cell values with data types preserved (text, number, date, formula results)
- Merged cell regions (common in requirement matrices — header cells span multiple columns/rows)
- Cell comments/notes (may contain requirement rationale or annotations)
- Embedded images/charts exported as image files

**Typical structures in XLS/XLSX requirements:**
- Requirement matrices: rows = individual requirements, columns = ID, description, priority, status, etc.
- Test case tables: rows = test cases, columns = test ID, steps, expected results, requirement refs
- Compliance sheets: rows = requirements, columns = compliance status, R&D comments

**Handling:** XLS/XLSX documents are typically already tabular, so the extraction produces structured records directly. Each row becomes a candidate requirement or test case record that the structural parser refines.

#### 5.1.5 Embedded Object Handling

MNO requirement documents frequently contain **embedded Microsoft documents** — e.g., an Excel spreadsheet embedded within a Word document, or a Word document embedded within another Word document.

**Types of embedded objects:**

| Object Type | Common Scenarios | Extraction Method |
|-------------|-----------------|-------------------|
| **OLE-embedded DOCX in DOCX** | Sub-specifications or appendices inserted as embedded objects | Extract the embedded file via `python-docx` OLE part access → recursively process as DOCX |
| **OLE-embedded XLSX in DOCX** | Parameter tables, compliance matrices, configuration tables | Extract the embedded file → process as XLSX |
| **OLE-embedded image in DOCX** | Architecture diagrams, flow charts, signal flow diagrams | Extract as image → process per 5.1.6 |
| **OLE-embedded PDF in DOCX** | Referenced standards excerpts, external spec inserts | Extract embedded PDF → process per 5.1.2 |
| **Package parts in DOCX** | ActiveX controls, other binary objects | Log as unprocessable; flag for manual review |

**Recursive extraction:** The extraction pipeline handles embedded documents recursively:

```
DOCX document
  ├── Text paragraphs → extracted directly
  ├── Tables → extracted directly
  ├── Images → exported, processed per 5.1.6
  ├── Embedded XLSX → extracted → processed as spreadsheet
  ├── Embedded DOCX → extracted → recursively processed
  └── Embedded PDF → extracted → processed as PDF
```

**Contextual linking:** Extracted content from embedded objects retains a reference to its location in the parent document (e.g., "embedded after section 1.3.4, paragraph 3"). This allows the structural parser to correctly position the embedded content within the requirement hierarchy.

**OLE extraction tooling:**
- `python-docx` can access the document's OLE parts via the `Part` API
- `olefile` library for direct OLE container parsing (fallback for complex objects)
- `python-pptx` if PowerPoint objects are encountered (less common)

#### 5.1.6 Image and Diagram Handling

Requirement documents contain images that carry requirement-relevant information:

| Image Type | Examples | Information Content |
|-----------|---------|-------------------|
| **Architecture diagrams** | Software stack diagrams, UE architecture | Layer boundaries, component responsibilities |
| **Flow charts** | Call flow diagrams, procedure sequences | Step-by-step behavioral requirements |
| **Signal flow diagrams** | Message sequence charts (MSC), ladder diagrams | Protocol interactions, message parameters |
| **State diagrams** | UE state machines, timer state transitions | State transitions, conditions, actions |
| **Tables as images** | Screenshots of tables from other documents | Structured data (lossy — prefer native tables) |
| **Network topology** | Network architecture, interface diagrams | Connectivity requirements, interface names |

**Processing pipeline:**

```
Image extracted from document
    │
    ├──[PoC path]──> Store as-is; link to parent requirement node
    │                with image_description from surrounding text context
    │
    └──[Production path]──> Multimodal LLM processing:
                            1. Image → LLM with prompt:
                               "Describe this telecom diagram. Extract:
                                entities, relationships, procedures,
                                parameters, and any requirement-relevant
                                information."
                            2. LLM output → text description stored as
                               supplementary content on the requirement node
                            3. Extracted entities added to the knowledge graph
```

**PoC approach:** Images are extracted and stored alongside their parent requirement node with a reference. The surrounding text context (paragraph before/after the image) is used as a proxy description. Full multimodal processing is deferred to production.

**Production approach:** Use the proprietary LLM's multimodal capability (or an on-premise vision model) to extract structured information from diagrams. This is particularly valuable for:
- Call flow diagrams that define behavioral requirements not captured in text
- Architecture diagrams that show software layer boundaries (modem vs. application processor)
- State machine diagrams that define timer/retry behavior

#### 5.1.7 Normalized Intermediate Representation

All extractors produce a common intermediate format consumed by the structural parsers:

```json
{
  "source_file": "LTEDATARETRY.docx",
  "source_format": "docx",
  "mno": "VZW",
  "release": "2026_Feb",
  "doc_type": "requirement",
  "content_blocks": [
    {
      "type": "heading",
      "level": 2,
      "text": "GENERIC THROTTLING ALGORITHM",
      "style": "Heading 2",
      "position": {"page": 21, "index": 145}
    },
    {
      "type": "paragraph",
      "text": "The UE shall implement T3402 on a PLMN basis...",
      "position": {"page": 21, "index": 146},
      "metadata": {"contains_requirement_id": "VZ_REQ_LTEDATARETRY_7743"}
    },
    {
      "type": "table",
      "headers": ["Cause Code", "Action", "Timer"],
      "rows": [
        ["3", "Retry with backoff", "T3402"],
        ["6", "Switch APN", "T3396"]
      ],
      "position": {"page": 22, "index": 147}
    },
    {
      "type": "image",
      "image_path": "extracted_images/img_p23_001.png",
      "surrounding_text": "Figure 1: Throttling Algorithm State Machine",
      "position": {"page": 23, "index": 148}
    },
    {
      "type": "embedded_object",
      "object_type": "xlsx",
      "extracted_path": "extracted_objects/timer_params.xlsx",
      "surrounding_text": "Table: Timer Parameter Values",
      "position": {"page": 24, "index": 149},
      "extracted_content": {
        "sheets": [{"name": "Parameters", "headers": [...], "rows": [...]}]
      }
    }
  ]
}
```

This normalized representation ensures that the structural parser (Section 5.2) works identically regardless of whether the source was PDF, DOCX, or XLS. The parser operates on `content_blocks`, not on raw format-specific data.

**Key benefit of DOCX over PDF:** When the source is DOCX, heading levels come directly from document styles (highly reliable). When the source is PDF, heading levels must be inferred from font size, boldness, and indentation (heuristic, error-prone). The structural parser can use the `source_format` field to adjust its confidence and strategy accordingly.

### 5.2 Structural Parser (Requirements)

**Objective:** Parse the normalized intermediate representation (from Section 5.1.7) into a structured requirement tree, leveraging the consistent per-MNO document format.

**Input:** Normalized content blocks from the document content extraction layer. The parser operates on the intermediate representation regardless of original file format.

**Approach:** Rule-based parser **specific to each MNO's document format**. A parser registry maps MNO identifiers to their corresponding parser implementation.

```python
# Parser registry pattern
parsers = {
    "VZW": VZWRequirementParser(),
    "TMO": TMORequirementParser(),
    "ATT": ATTRequirementParser(),
}
```

**VZW OA Parser** (reference implementation for PoC):
- **Requirement ID pattern:** `VZ_REQ_{PLANID}_{NUMBER}` — globally unique, with plan name embedded
- **Section numbering:** Hierarchical numbering (e.g., `1.4.3.1.1.10`) up to 6+ levels deep
- **Plan metadata:** Plan Name, Plan ID, Version Number, Release Date in document header
- **Consistent section zones:**
  - Sections 1.1–1.2: Metadata (applicability, acronyms, hardware specs)
  - Section 1.3: Core software specifications (timers, algorithms, rules)
  - Section 1.4: Scenario-based requirements

**Output per document:** Structured JSON with MNO and release metadata derived from folder path:

```json
{
  "mno": "VZW",
  "release": "2026_Feb",
  "plan_id": "LTEDATARETRY",
  "plan_name": "LTE_Data_Retry",
  "version": 39,
  "release_date": "February 2026",
  "referenced_standards_releases": {
    "3GPP TS 24.301": "Release 10",
    "3GPP TS 24.008": "Release 10"
  },
  "requirements": [
    {
      "req_id": "VZ_REQ_LTEDATARETRY_7748",
      "section_number": "1.4.3.1.1.2",
      "title": "EMM Cause Codes 3, 6, 7, 8",
      "parent_req_id": "VZ_REQ_LTEDATARETRY_7747",
      "parent_section": "1.4.3.1.1",
      "hierarchy_path": [
        "LTE Data Retry",
        "SCENARIOS",
        "EMM SPECIFIC PROCEDURES",
        "ATTACH REQUEST",
        "ATTACH REJECT WITH EMM CAUSE CODE",
        "EMM Cause Codes 3, 6, 7, 8"
      ],
      "text": "...",
      "tables": [{"headers": [...], "rows": [...], "source": "inline|embedded_xlsx"}],
      "images": [{"path": "extracted_images/img_001.png", "caption": "Figure 1: ...", "description": "..."}],
      "embedded_objects": [{"type": "xlsx", "extracted_path": "...", "content_summary": "..."}],
      "children": ["VZ_REQ_LTEDATARETRY_..."],
      "cross_references": {
        "internal": ["VZ_REQ_LTEDATARETRY_2376"],
        "external_plans": ["LTESMS"],
        "standards": [
          {
            "spec": "3GPP TS 24.301",
            "section": "5.5.1.2.5",
            "release": "Release 10"
          }
        ]
      }
    }
  ]
}
```

**Note on `referenced_standards_releases`:** Each MNO requirement document typically specifies in its Introduction/References section which release of each standard it references (e.g., "This document is based on 3GPP Release 10 specifications"). This is captured at the document level and used to resolve the correct standards version during standards ingestion.

### 5.3 Structural Parser (Test Cases)

**Objective:** Parse test case documents into structured test case records, linked to their corresponding requirements.

**Approach:** Rule-based parser specific to each MNO's test case format. Test case documents have their own format distinct from requirement documents, but are consistent within each MNO.

**Key extraction targets:**

| Field | Description | Purpose |
|-------|-------------|---------|
| `test_id` | Test case identifier | Unique node identity |
| `test_name` | Test case title | Searchable, human-readable |
| `description` | What the test validates | Q&A content |
| `preconditions` | Setup state required | Context for understanding |
| `steps` | Ordered test procedure | Detailed Q&A content |
| `expected_result` | Pass/fail criteria | Compliance evaluation |
| `requirement_refs` | Requirement IDs this test covers | Traceability edges |
| `priority` | Test priority/category | Filtering |

**Output per test case document:** Structured JSON:

```json
{
  "mno": "VZW",
  "release": "2026_Feb",
  "plan_id": "LTEDATARETRY_TC",
  "corresponding_req_plan": "LTEDATARETRY",
  "test_cases": [
    {
      "test_id": "TC_LTEDATARETRY_001",
      "test_name": "Verify T3402 behavior on PLMN basis",
      "description": "...",
      "preconditions": ["..."],
      "steps": ["..."],
      "expected_result": "...",
      "requirement_refs": ["VZ_REQ_LTEDATARETRY_7743"],
      "priority": "mandatory"
    }
  ]
}
```

**Traceability link extraction:** Test case documents typically reference specific requirement IDs. These references are extracted to create `tested_by` / `tests` edges in the knowledge graph. If explicit requirement ID references are absent, LLM-based matching between test case descriptions and requirement text is used as a fallback.

### 5.4 Cross-Reference Extraction

**Objective:** Identify all references from each requirement to other requirements, other plans (within the same MNO), and external standards.

**Three extraction methods, in order of reliability:**

**Method 1: Explicit Plan References (Deterministic)**
- Pattern match for MNO-specific requirement ID patterns (e.g., `VZ_REQ_{PLANID}_{NUMBER}` for VZW)
- Pattern match for known plan names within the same MNO
- Reliability: High

**Method 2: Standards References (Deterministic)**
- Regex extraction of 3GPP spec citations: `3GPP TS \d+\.\d+`, section numbers, release versions
- Regex for GSMA/OMA spec citations
- **Release resolution:** If a reference doesn't specify a standards release, use the document-level `referenced_standards_releases` mapping (from Section 5.2) to resolve the correct version
- Reliability: High

**Method 3: Concept/Feature References (LLM-Assisted)**
- For each requirement, LLM extracts telecom concept tags (timers, procedures, interfaces, cause codes, device modes, protocols)
- Requirements across documents sharing concept tags are candidate cross-document links
- **This operates within a single MNO+release scope first**, then extends to cross-MNO concept linking via the shared feature taxonomy
- Reliability: Medium — requires validation

**Output:** A cross-reference manifest per document listing all outbound references with their types.

### 5.5 Standards Specs Ingestion (Hybrid Selective — Option C)

**Objective:** Ingest the specific sections of 3GPP/GSMA standards that MNO requirements reference, with enough surrounding context for completeness. Handle **release-specific** standards references.

**Pre-processing step:** Download all referenced standards specs organized by spec ID and release version into the `/Standards/` folder structure (see Section 4.3). This is a one-time setup per standards release.

**Approach:**

**Step 1: Collect all standards references across all ingested MNO documents**

Aggregate references across all MNOs and releases, preserving the specific standards release referenced:

```
Aggregate output:
  VZW/2026_Feb:
    - 3GPP TS 24.301 Release 10: sections 5.5.1.2.5, 5.3.7a, 5.4.2, ...
    - 3GPP TS 24.008 Release 10: sections ...
  TMO/2026_Q1:
    - 3GPP TS 24.301 Release 15: sections 5.5.1.2.5, 5.3.7a, ...
    - 3GPP TS 24.501 Release 15: sections ...  (5G NAS)
```

**Key observation:** Different MNOs (or different releases of the same MNO) may reference **different releases** of the same 3GPP spec. For example, VZW Feb 2026 may reference 3GPP TS 24.301 Release 10, while TMO 2026 Q1 references Release 15. Both standards versions must be ingested as separate Standards_Section nodes.

**Step 2: For each referenced section, extract from the correct standards release:**
- The referenced section itself (primary content)
- The parent section (structural context)
- Definitions/terminology sections referenced within the section
- Adjacent sub-sections that are part of the same procedure

This produces a **focused but contextually complete** subset of each standard — estimated at 5–10% of the full spec per standard per release.

**Step 3: Classify the relationship between each MNO requirement and its referenced standards section:**

Using the LLM during ingestion:

| Relationship | Meaning | Detection Signal |
|-------------|---------|-----------------|
| **DEFER** | MNO says "follow 3GPP" with no modifications | "shall follow the steps detailed in 3GPP...", "per 3GPP TS..." with no additional constraints |
| **CONSTRAIN** | MNO narrows an optional behavior or makes it mandatory | "shall implement X on a per-PLMN basis" where 3GPP leaves this as implementation choice |
| **OVERRIDE** | MNO specifies behavior that differs from 3GPP default | Explicit timer values, specific parameter settings that differ from 3GPP defaults |
| **EXTEND** | MNO adds requirements beyond what 3GPP specifies | Requirements with no corresponding 3GPP section, MNO-specific scenarios |

**Step 4: Store the delta summary as edge metadata**

The LLM produces a concise summary of what specifically differs (e.g., "VZW mandates T3402 on per-PLMN basis; 3GPP leaves as implementation choice"). This is pre-computed so that "how does VZW differ from 3GPP?" queries can be answered without re-analysis at query time.

**Step 5 (cross-MNO enrichment): For standards sections referenced by multiple MNOs, pre-compute MNO comparison summaries**

When the same 3GPP section is referenced by requirements from different MNOs (even if different standards releases), flag it as a comparison opportunity. This enables queries like "How do VZW and TMO differ in their handling of T3402?"

### 5.6 Feature Taxonomy Derivation

**Objective:** Build a feature/capability taxonomy bottom-up from the documents themselves, enabling cross-document and **cross-MNO** grouping of requirements by logical feature.

**This addresses Pattern (a) of cross-document requirements** — where a single logical capability is defined across multiple documents — **and extends it to the cross-MNO dimension**.

**Approach: Three-step LLM-driven derivation**

**Step 1: Document-Level Feature Extraction (per MNO, per release)**

For each document, feed plan metadata + section headings (TOC) to the LLM:

```
Prompt:
You are a telecom domain expert. Given the following {MNO} requirement
document table of contents, extract:
1. The primary telecom features/capabilities this document covers
2. Other features/capabilities this document DEPENDS ON or REFERENCES
   (even if primarily defined elsewhere)
3. Key telecom concepts, protocols, and interfaces mentioned

Output as structured JSON.
```

**Step 2: Cross-Document Consolidation (per MNO first, then cross-MNO)**

First consolidate within each MNO to get per-MNO feature taxonomies. Then merge across MNOs:

```
Prompt:
Given feature taxonomies derived from VZW, TMO, and ATT requirement documents:
1. Identify features that appear across multiple MNOs (these are universal telecom features)
2. Identify MNO-specific features not present in other MNOs
3. Produce a unified feature taxonomy where each feature indicates which MNOs have requirements for it

Output a unified taxonomy.
```

**Step 3: Human Review**

The derived taxonomy is reviewed by a domain expert to:
- Correct incorrectly merged or split features
- Validate dependency directions
- Add features the LLM missed
- Confirm cross-MNO feature alignment (e.g., VZW's "LTE Data Retry" and TMO's equivalent are the same logical feature)

**Output:** A unified feature taxonomy:

```json
{
  "features": [
    {
      "feature_id": "IMS_REGISTRATION",
      "name": "IMS Registration",
      "description": "IMS network registration procedures including initial registration, re-registration, and de-registration",
      "mno_coverage": {
        "VZW": ["LTEIMS", "LTEVOICE"],
        "TMO": ["TMO_IMS_REQ"],
        "ATT": ["ATT_IMS_SPEC"]
      },
      "depends_on_features": ["PDN_CONNECTIVITY", "SIM_PROVISIONING"],
      "keywords": ["IMS", "SIP REGISTER", "P-CSCF", "ISIM"]
    }
  ]
}
```

**The feature taxonomy is MNO-agnostic at the feature level** — "IMS Registration" is a telecom concept that exists regardless of MNO. The `mno_coverage` field maps to specific plans per MNO that contribute to this feature. This enables cross-MNO comparison queries.

### 5.7 Knowledge Graph Construction

**Objective:** Build a single unified knowledge graph across all MNOs, releases, documents, test cases, standards, and features.

See [Section 6: Knowledge Graph Model](#6-knowledge-graph-model) for the full graph schema.

**Tooling:** NetworkX (PoC) or Neo4j (production).

**Construction sequence (per MNO, per release):**
1. Create Release node (MNO + release identifier)
2. Create Plan nodes from document metadata, linked to Release
3. Create Requirement nodes from structural parser output, linked to Plan
4. Create hierarchy edges (parent-child) within each document
5. Create Test Case nodes from test case parser output, linked to corresponding Plan
6. Create traceability edges (requirement ↔ test case)
7. Create cross-reference edges within MNO+release (from cross-reference extraction)

**Cross-cutting construction (after all MNO/releases ingested):**
8. Create/update Standards nodes (shared across MNOs, versioned by release)
9. Create standards edges with relationship types and delta metadata
10. Create/update Feature nodes (shared across MNOs, from unified taxonomy)
11. Create feature mapping edges (requirement → feature)
12. Create cross-MNO concept links (via shared feature and standards nodes)

### 5.8 Vector Store Construction

**Objective:** Create embeddings for requirement and test case nodes for use in targeted vector retrieval, in a single unified vector store with metadata filters.

**Chunk strategy:** Each requirement node and each test case node becomes one chunk. This is a critical design choice — we do NOT use arbitrary fixed-size chunking. The structural parser has already produced semantically meaningful units.

**Metadata per chunk:**

| Field | Example | Purpose |
|-------|---------|---------|
| `mno` | "VZW" | Filter by MNO |
| `release` | "2026_Feb" | Filter by release |
| `doc_type` | "requirement" or "testcase" | Filter by document type |
| `plan_id` | "LTEDATARETRY" | Filter by plan |
| `req_id` or `test_id` | "VZ_REQ_LTEDATARETRY_7748" | Unique identifier |
| `feature_ids` | ["DATA_RETRY", "EMM_PROCEDURES"] | Feature membership |

**Contextualization:** Before embedding, each chunk is prepended with its structural context and enriched with table/image content:

```
[MNO: VZW | Release: Feb 2026 | Plan: LTE_Data_Retry | Version: 39]
[Path: SCENARIOS > EMM SPECIFIC PROCEDURES > ATTACH REQUEST > ATTACH REJECT > EMM Cause Codes 3, 6, 7, 8]
[Req ID: VZ_REQ_LTEDATARETRY_7748]

<requirement text>

[Table: Cause Code Action Matrix]
| Cause Code | Action | Timer |
| 3 | Retry with backoff | T3402 |
...

[Image: Figure 1 - Throttling Algorithm State Machine]
<image caption or LLM-generated description if available>
```

**Table handling in chunks:** Tables are serialized as Markdown tables within the chunk text. This preserves structure while keeping the content embeddable and LLM-readable.

**Image handling in chunks:** For PoC, image captions and surrounding text are included. For production, LLM-generated image descriptions (from 5.1.6) replace the caption with richer textual content.

For test case chunks:
```
[MNO: VZW | Release: Feb 2026 | Test Plan: LTE_Data_Retry_TC]
[Test ID: TC_LTEDATARETRY_001 | Tests: VZ_REQ_LTEDATARETRY_7743]

<test case description, steps, expected result>
```

**Tooling:** FAISS or ChromaDB (PoC); production vector store TBD based on infrastructure.

**Embedding model considerations:**
- Standard embedding models (e.g., `text-embedding-3-large`) may underperform on telecom terminology
- If retrieval quality is insufficient, consider fine-tuning an embedding model on telecom text or using a domain-adapted model
- This is an optimization to evaluate during PoC — start with a general-purpose model and measure

---

## 6. Knowledge Graph Model

### 6.1 Node Types

| Node Type | Description | Key Attributes | Source |
|-----------|-------------|---------------|--------|
| **MNO** | A mobile network operator | `mno_id`, `name` | Configuration |
| **Release** | A quarterly requirement release for an MNO | `mno`, `release_id`, `release_date`, `is_latest` | Folder structure |
| **Plan** | An MNO requirement document | `plan_id`, `plan_name`, `version`, `release_date`, `mno`, `release` | Document metadata |
| **Requirement** | An individual requirement from an MNO document | `req_id`, `plan_id`, `mno`, `release`, `section_number`, `title`, `text`, `tables`, `images`, `embedded_objects`, `hierarchy_path` | Structural parser |
| **Test_Plan** | An MNO test case document | `test_plan_id`, `corresponding_req_plan`, `mno`, `release` | Test case parser |
| **Test_Case** | A test case from a test plan document | `test_id`, `test_plan_id`, `mno`, `release`, `name`, `description`, `steps`, `expected_result`, `priority` | Test case parser |
| **Standard_Section** | A specific section of a 3GPP/GSMA/OMA standard at a specific release | `spec_id`, `section`, `release`, `text` | Standards ingestion |
| **Feature** | A logical telecom capability spanning multiple documents and MNOs | `feature_id`, `name`, `description`, `keywords`, `mno_coverage` | Taxonomy derivation |

### 6.2 Edge Types

#### Organizational Edges

| Edge Type | From → To | Meaning |
|-----------|-----------|---------|
| `has_release` | MNO → Release | This MNO has this release |
| `contains_plan` | Release → Plan | This release contains this plan |
| `contains_test_plan` | Release → Test_Plan | This release contains this test plan |

#### Within-Document Edges

| Edge Type | From → To | Meaning |
|-----------|-----------|---------|
| `parent_of` | Requirement → Requirement | Hierarchical containment (section 1.4.3 contains 1.4.3.1) |
| `belongs_to` | Requirement → Plan | Requirement is part of this plan document |
| `belongs_to` | Test_Case → Test_Plan | Test case is part of this test plan |

#### Cross-Document Edges (within same MNO+release)

| Edge Type | From → To | Meaning | Detection |
|-----------|-----------|---------|-----------|
| `depends_on` | Requirement → Requirement (other plan) | Functional dependency — behavior changes based on the other requirement | Explicit plan references in text (deterministic) |
| `shared_standard` | Requirement ↔ Requirement | Both reference the same standards section | Matching 3GPP section references (deterministic) |
| `concept_link` | Requirement ↔ Requirement | Related via shared telecom concept | LLM-extracted concept tags (medium confidence) |

#### Standards Edges

| Edge Type | From → To | Meaning | Attributes |
|-----------|-----------|---------|------------|
| `defers_to` | Requirement → Standard_Section | "Do what 3GPP says" — actual behavior is in the standard | `delta_summary`: null |
| `constrains` | Requirement → Standard_Section | Narrows an optional behavior | `delta_summary`: what is constrained |
| `overrides` | Requirement → Standard_Section | Differs from standard default | `delta_summary`: what differs and how |
| `extends` | Requirement → Standard_Section | Adds beyond what standard specifies | `delta_summary`: what is added |
| `parent_section` | Standard_Section → Standard_Section | Structural hierarchy within a standard | — |

#### Feature Edges

| Edge Type | From → To | Meaning |
|-----------|-----------|---------|
| `maps_to` | Requirement → Feature | This requirement contributes to this feature/capability |
| `feature_depends_on` | Feature → Feature | One feature requires another |

#### Traceability Edges

| Edge Type | From → To | Meaning |
|-----------|-----------|---------|
| `tested_by` | Requirement → Test_Case | This test case validates this requirement |
| `tests` | Test_Case → Requirement | Inverse of above |
| `test_plan_for` | Test_Plan → Plan | This test plan corresponds to this requirement plan |

#### Cross-MNO Edges (implicit via shared nodes)

Cross-MNO relationships are **not modeled as direct edges** between requirements of different MNOs. Instead, they are realized through shared Feature and Standard_Section nodes:

```
VZW Requirement ──maps_to──> [IMS_REGISTRATION] <──maps_to── TMO Requirement
VZW Requirement ──defers_to──> [3GPP_24.301_5.5.1] <──constrains── TMO Requirement
```

This means a cross-MNO comparison query traverses: MNO_A requirement → shared feature/standard → MNO_B requirement. No explicit cross-MNO edges are needed.

#### Cross-Release Edges

| Edge Type | From → To | Meaning | Attributes |
|-----------|-----------|---------|------------|
| `version_of` | Requirement → Requirement (different release) | Same requirement across releases | `change_type`: added/modified/removed/unchanged |
| `succeeds` | Release → Release | This release is the successor of the previous | — |

**`version_of` edge construction:** During ingestion of a new release, for each requirement in the new release:
1. Match by requirement ID to the previous release (primary — IDs are typically stable across versions)
2. If IDs changed (rare), fall back to section number + title text similarity matching
3. Compare requirement text to classify as `added`, `modified`, `removed`, or `unchanged`

### 6.3 Cross-Document Handling

The graph handles two distinct cross-document patterns:

#### Pattern (a): Fragmented Requirements — One Capability Across Multiple Documents

A single logical capability (e.g., device activation) is defined across multiple documents. No single document has the complete picture, and the documents may not explicitly cross-reference each other.

**Handled via Feature Nodes.** The feature taxonomy groups requirements from different documents under a common feature. A query about "device activation requirements" resolves to the `DEVICE_ACTIVATION` feature node, which maps to requirements across SIM, UI, Network, Entitlement, and Data Retry documents.

```
              ┌─────────────────────┐
              │  DEVICE_ACTIVATION  │  ← Feature Node
              └──────────┬──────────┘
     ┌──────────┬────────┼────────┬──────────┐
     ▼          ▼        ▼        ▼          ▼
 [SIM reqs] [UI reqs] [Network] [Entitle.] [DataRetry]
```

#### Pattern (b): Dependent Requirements — Behavior Depends on Another Document

A requirement in one document is functionally incomplete without understanding a requirement in another document.

**Handled via typed cross-document edges.** The `depends_on` edges directly link dependent requirements. Graph traversal follows these edges at query time.

```
VZ_REQ_LTEDATARETRY_41013 ──depends_on──> VZ_REQ_LTESMS_XXXX
   (Attach Reject behavior)                (SMS over IMS support)
```

### 6.4 Cross-MNO Handling

Cross-MNO queries are enabled through **shared Feature and Standards nodes**:

```
Query: "Compare IMS registration requirements of VZW and TMO"

                    ┌──────────────────┐
                    │ IMS_REGISTRATION │  ← Shared Feature Node
                    └────────┬─────────┘
               ┌─────────────┼─────────────┐
               ▼             ▼             ▼
        ┌──────────┐  ┌──────────┐  ┌──────────┐
        │ VZW IMS  │  │ TMO IMS  │  │ ATT IMS  │
        │ reqs     │  │ reqs     │  │ reqs     │
        │(2026_Feb)│  │(2026_Q1) │  │(2026_Q1) │
        └──────────┘  └──────────┘  └──────────┘
```

For standards-based comparison:
```
Query: "How do VZW and TMO differ in their T3402 handling vs 3GPP?"

VZW_REQ_7743 ──constrains──> [3GPP_24.301_T3402_R10]
TMO_REQ_XXXX ──overrides───> [3GPP_24.301_T3402_R15]

The graph reveals: VZW constrains (narrows) while TMO overrides (changes),
and they reference different 3GPP releases.
```

### 6.5 Cross-Release Handling

Version diff queries traverse `version_of` edges:

```
Query: "What changed in VZW eSIM requirements from Oct 2025 to Feb 2026?"

VZW/2025_Oct/LTEESIM reqs ──version_of──> VZW/2026_Feb/LTEESIM reqs
                             (per requirement, with change_type metadata)

Pipeline:
1. Find all LTEESIM requirements in both releases
2. Follow version_of edges to identify added/modified/removed
3. For modified requirements, present the delta
```

**Default release resolution:** When a query doesn't specify a release, the system defaults to the latest release for each MNO. The `is_latest` flag on Release nodes enables this. When a new release is ingested, the previous release's `is_latest` flag is cleared.

---

## 7. Query Pipeline

### 7.1 Query Analysis

**Input:** User's natural language query.

**Process:** The LLM analyzes the query to extract:

| Extracted Element | Example | Purpose |
|-------------------|---------|---------|
| **Named entities** | Requirement IDs, plan names, timer names, cause codes, test case IDs | Direct graph lookup |
| **Telecom concepts** | "Attach Reject", "IMS registration", "VoWiFi", "carrier aggregation" | Feature and concept matching |
| **Referenced standards** | "3GPP TS 24.301" | Standards node lookup |
| **MNO(s)** | "Verizon", "VZW", "T-Mobile", "TMO", "AT&T" | MNO scoping |
| **Release(s)** | "Feb 2026", "Oct 2025", "latest" | Release scoping |
| **Query type** | single_doc, cross_doc, cross_mno_comparison, release_diff, standards_comparison, traceability, test_case | Determines pipeline behavior |
| **Doc type scope** | Requirements only, test cases only, or both | Filters retrieval |

**Output:** A structured query intent object:

```json
{
  "entities": ["IMS registration"],
  "concepts": ["ims_registration"],
  "mnos": ["VZW", "TMO"],
  "releases": null,
  "query_type": "cross_mno_comparison",
  "doc_type_scope": "requirements",
  "standards_refs": [],
  "likely_features": ["IMS_REGISTRATION"]
}
```

### 7.2 MNO and Release Resolution

**Objective:** Resolve which MNO(s) and release(s) the query targets, applying defaults where unspecified.

**Resolution rules:**

| User specifies | Resolution |
|---------------|------------|
| MNO + release | Use exactly as specified |
| MNO only, no release | Default to latest release for that MNO (`is_latest = true`) |
| No MNO, no release | **Ambiguous** — either ask for clarification, or search across all MNOs (latest releases) depending on query type |
| Two MNOs (comparison) | Use specified MNOs; default each to latest release if not specified |
| Two releases (version diff) | Must specify or infer the MNO; use specified releases |
| "all MNOs" | Search across all MNOs, latest releases |

**Output:** Resolved scope:
```json
{
  "scoped_mnos": [
    {"mno": "VZW", "release": "2026_Feb"},
    {"mno": "TMO", "release": "2026_Q1"}
  ],
  "query_type": "cross_mno_comparison"
}
```

### 7.3 Graph Scoping

**Objective:** Use the knowledge graph to identify a candidate set of nodes that are potentially relevant, respecting MNO/release scope and spanning multiple documents as needed.

**Process:**

1. **Entity lookup (scoped):** Match extracted entities to graph nodes, filtered by resolved MNO(s) and release(s)

2. **Feature lookup:** Match extracted concepts to feature nodes, then follow `maps_to` edges — filtered by the resolved MNO(s) and release(s) — to find contributing requirements

3. **Edge traversal (scoped):** From the initial nodes found in steps 1–2:
   - `depends_on` → pull in dependent requirements (same MNO+release)
   - `defers_to` / `constrains` / `overrides` → pull in referenced standards sections
   - `parent_of` (upward) → include parent context
   - `shared_standard` → find related requirements (same MNO+release, or cross-MNO if comparison query)
   - `tested_by` → include test cases if query involves traceability or test cases
   - `version_of` → include previous/next version requirements if query is a version diff

4. **Cross-MNO expansion (for comparison queries only):** If the query is a cross-MNO comparison, traverse shared Feature/Standards nodes to pull requirements from the second MNO

5. **Scope limiting:** Configurable traversal depth limits (default: 2 hops). Edge type filtering based on query type.

**Output:** A candidate set of requirement/test case nodes with their associated standards sections and hierarchy paths, potentially spanning multiple documents, MNOs, and releases.

### 7.4 Targeted Vector RAG

**Objective:** Rank and select the most relevant chunks from the candidate set.

**Process:**
1. Embed the user's query
2. Compute similarity **only against chunks in the candidate set** (using metadata filters for `mno`, `release`, `doc_type` as needed)
3. Select top-K chunks, enforcing diversity:
   - Across documents (at least 1 chunk from each contributing document)
   - Across MNOs (for comparison queries — ensure balanced representation from each MNO)
   - Across doc types (if both requirements and test cases are relevant)

### 7.5 Context Assembly

**Objective:** Build the LLM prompt context from selected chunks, augmented with structural and relational information from the graph, and clearly annotated with MNO/release provenance.

**For each selected chunk, include:**

1. **MNO and release provenance** — clearly labeled
2. **The requirement/test case text** (from the chunk)
3. **Hierarchy path** — the full path from root to this requirement
4. **Parent context** — if the parent section provides essential framing
5. **Standards text** — if the requirement has a standards edge
6. **Cross-reference annotations** — noting relationships to other chunks in the context

**Context template (cross-MNO comparison):**

```
You are comparing MNO device requirements across operators.

=== VZW REQUIREMENT ===
MNO: Verizon | Release: Feb 2026 | Plan: LTE_IMS (v41)
Hierarchy: IMS REGISTRATION > INITIAL REGISTRATION > ...
Req ID: VZ_REQ_LTEIMS_1234
Standards: Constrains 3GPP TS 24.301 section 5.5.1 (Release 10)
Delta from 3GPP: VZW mandates registration within 5s of PDN connectivity

<requirement text>

=== TMO REQUIREMENT ===
MNO: T-Mobile | Release: Q1 2026 | Plan: TMO_IMS_REQ (v12)
Hierarchy: IMS > REGISTRATION PROCEDURES > ...
Req ID: TMO_IMS_REQ_567
Standards: Overrides 3GPP TS 24.301 section 5.5.1 (Release 15)
Delta from 3GPP: TMO specifies custom re-registration timer of 3600s

<requirement text>

=== REFERENCED STANDARD ===
Source: 3GPP TS 24.301, Section 5.5.1
[Release 10 text for VZW context]
[Release 15 text for TMO context, if differs]
```

**Context template (test case query):**

```
=== REQUIREMENT ===
MNO: Verizon | Release: Feb 2026
Req ID: VZ_REQ_LTEDATARETRY_7743
<requirement text>

=== TEST CASE ===
MNO: Verizon | Release: Feb 2026 | Test Plan: LTE_Data_Retry_TC
Test ID: TC_LTEDATARETRY_001
Tests Requirement: VZ_REQ_LTEDATARETRY_7743
Description: <...>
Preconditions: <...>
Steps: <...>
Expected Result: <...>
```

### 7.6 LLM Synthesis

**Objective:** Generate a comprehensive, accurate answer with proper citations and MNO/release awareness.

**The LLM is instructed to:**
1. Synthesize information across all provided requirements, test cases, and standards references
2. Cite specific requirement IDs and test case IDs for every factual claim
3. **Clearly attribute each piece of information to its MNO and release**
4. Note when requirements from different documents, MNOs, or releases interact or differ
5. Distinguish between MNO-specific requirements and standard 3GPP behavior
6. Flag if the provided context appears insufficient to fully answer the query

**For cross-MNO comparison queries:**
- Present a structured comparison (table format when appropriate)
- Highlight commonalities and differences
- Note where each MNO defers to, constrains, or overrides the relevant standard

**For version diff queries:**
- Present added, removed, and modified requirements
- For modified requirements, highlight what specifically changed
- Note implications of changes (e.g., "Timer T3402 value changed from X to Y, which affects retry aggressiveness")

---

## 8. Target Capabilities

### 8.1 Requirement Q&A Bot

#### 8.1.1 Single-Document Q&A

**Query example:** "What timer values are specified for T3402 in the VZW Data Retry requirements?"

**Pipeline:** Query analysis → MNO/release resolution (VZW, latest) → entity lookup (T3402, LTEDATARETRY) → graph scoping (single document) → RAG ranking → context assembly → synthesis.

#### 8.1.2 Cross-Document Q&A (Single MNO)

**Query example:** "What are the full VZW requirements for handling Attach Reject when the device doesn't support SMS over IMS?"

**Pipeline:** Query analysis → MNO/release resolution (VZW, latest) → entity lookup (Attach Reject, SMS over IMS) → graph scoping (traverses from LTEDATARETRY to LTESMS via `depends_on` edge) → RAG ranking across both docs → context assembly → synthesis.

#### 8.1.3 Cross-MNO Comparison

**Query example:** "Compare IMS registration requirements of Verizon and T-Mobile"

**Pipeline:** Query analysis (detects cross-MNO comparison) → MNO/release resolution (VZW latest, TMO latest) → feature lookup (IMS_REGISTRATION) → graph scoping (follow `maps_to` edges filtered by each MNO) → pull standards references from both → RAG ranking with MNO diversity → context assembly with MNO-labeled sections → synthesis as structured comparison.

**Query example:** "Compare carrier aggregation combinations needed by ATT and TMO"

**Pipeline:** Same pattern — feature lookup for CARRIER_AGGREGATION, retrieve requirements from both MNOs, present comparison with specific CA band combinations from each.

#### 8.1.4 Standards Comparison

**Query example:** "How does VZW's T3402 handling differ from the 3GPP specification?"

**Pipeline:** Query analysis → MNO/release resolution → entity lookup (T3402) → graph scoping (VZW requirement + standards nodes via `constrains`/`overrides` edge) → context assembly with both VZW and 3GPP text + pre-computed delta summary → synthesis as structured comparison.

#### 8.1.5 Release Diff (Version Comparison)

**Query example:** "What is the delta of Verizon eSIM requirements from Feb 2026 to Oct 2025?"

**Pipeline:** Query analysis (detects release diff) → MNO resolution (VZW) → release resolution (2026_Feb, 2025_Oct) → find eSIM plan in both releases → traverse `version_of` edges → classify each requirement as added/modified/removed/unchanged → for modified, compute text diff → context assembly → synthesis as structured diff report.

**Query example:** "What changed across all VZW requirements between Oct 2025 and Feb 2026?"

**Pipeline:** Same but across all plans — presents a summary of changes per plan, then details on request.

#### 8.1.6 Traceability

**Query example:** "What test cases cover the Attach Reject requirements?"

**Pipeline:** Query analysis → entity lookup → graph scoping (follow `tested_by` edges to test case nodes) → context assembly → synthesis listing requirements mapped to test cases.

### 8.2 Test Case Q&A

#### 8.2.1 Test Case Lookup by Requirement

**Query example:** "What is the test case description to check IMS registration?"

**Pipeline:** Query analysis (detects test case query) → MNO/release resolution → feature/entity lookup (IMS registration) → find matching requirement nodes → follow `tested_by` edges → retrieve test case nodes → context assembly with both requirement and test case content → synthesis.

#### 8.2.2 Test Case Content Queries

**Query example:** "What are the preconditions for testing T3402 timer behavior?"

**Pipeline:** Query analysis → search test case nodes directly (via vector RAG on test case chunks with `doc_type=testcase` filter) + graph scoping via requirement linkage → context assembly → synthesis.

#### 8.2.3 Test Coverage Analysis

**Query example:** "Which Data Retry requirements don't have corresponding test cases?"

**Pipeline:** Query analysis → find all requirements in the Data Retry plan → check which have `tested_by` edges → report requirements without test case coverage.

### 8.3 Requirement Compliance Agent

**Note:** The compliance agent is a more complex, agentic workflow. It is scoped for post-PoC development but the architecture is outlined here for completeness.

#### 8.3.1 Single Requirement Compliance Check

**Input:** A compliance sheet (Excel) containing requirement IDs, compliance status, and R&D comments.

**Process:**
1. Parse the compliance sheet into structured records
2. For each requirement marked as "compliant" or with R&D comments:
   - Look up the requirement in the knowledge graph (filtered by MNO + release)
   - Retrieve the full requirement text with context
   - Evaluate whether the R&D comment accurately reflects the requirement
   - Flag discrepancies (e.g., comment describes a behavior that contradicts the requirement text)

#### 8.3.2 Cross-Document Compliance Consistency

**Input:** Compliance sheets for multiple requirement documents (same MNO, same release).

**Process:**
1. Identify dependent requirement groups using the knowledge graph (e.g., Data Retry requirements that depend on SMS support)
2. For each dependency chain, check consistency:
   - If Requirement A in Doc 1 is marked "not supported", and Requirement B in Doc 2 depends on A and is marked "supported" — flag inconsistency
   - Use the typed dependency edges (`depends_on`, `conditional_on`) to determine which consistency checks to apply

#### 8.3.3 Auto-Fill from Module Documentation

**Input:** Compliance sheet + module/chipset documentation (e.g., Qualcomm modem spec).

**Process:**
1. For requirements tagged as modem-layer (identified via the `SOFTWARE ARCHITECTURE WITHIN THE UE` section in requirement docs)
2. Match requirement content against module documentation
3. Pre-fill compliance status with evidence citations

#### 8.3.4 Delta Compliance Sheet Generation

**Input:** Base compliance sheets (previous release) + delta compliance information (changes for new release).

**Process:**
1. Use version diff (Section 8.1.5) via `version_of` edges to identify changed requirements between releases
2. Carry forward compliance for unchanged requirements
3. Apply delta compliance information for changed requirements
4. Flag new/added requirements that need manual review

---

## 9. PoC Plan

### 9.1 Objective

Validate that the Knowledge Graph + RAG architecture can reliably answer cross-document questions that pure RAG cannot, using a small set of publicly available VZW OA requirement documents.

**Note:** The PoC focuses on single-MNO, single-release validation. Multi-MNO and multi-release capabilities are designed into the architecture but will be validated in a subsequent phase when additional MNO documents are available.

### 9.2 PoC Scope

| Dimension | PoC Scope | Production Scope |
|-----------|-----------|-----------------|
| MNOs | VZW only (publicly available) | VZW, TMO, ATT, and others |
| Releases | 1 release (Feb 2026) | Multiple releases per MNO (4+ per year) |
| Requirements | 5 VZW OA docs (LTEDATARETRY, LTESMS, LTEAT, LTEB13NAC, LTEOTADM) | All requirement docs across MNOs and releases |
| Test Cases | If available for PoC docs | All test case docs |
| Standards | Referenced 3GPP sections from PoC docs only | Full set of referenced standards, multiple releases |
| Features | Derived taxonomy from 5 docs | Unified taxonomy across all MNOs |
| Capabilities | Q&A bot (single-doc, cross-doc, standards comparison) | Q&A + test case Q&A + compliance agent + cross-MNO comparison + release diff |
| LLM | Claude or Gemini | Proprietary LLM |
| Infrastructure | Local Python environment | On-premise servers |
| Vector store | FAISS or ChromaDB | Production vector DB |
| Graph store | NetworkX (in-memory) | Neo4j or similar |
| Parsers | VZW parser only | Per-MNO parser registry |

### 9.3 PoC Steps

| Step | Description | Validates | Deliverable |
|------|-------------|-----------|-------------|
| **1** | Set up folder structure; document content extraction for all 5 VZW docs (PDF for PoC; extraction layer supports DOCX/XLS) | Clean structured text, tables, and images from VZW docs? | Normalized intermediate representation per document |
| **2** | Build VZW structural parser for requirements | Reliable extraction of IDs, hierarchy, section text? | Requirement tree JSON per document |
| **3** | Build VZW test case parser (if test case docs available) | Test case extraction and requirement linkage? | Test case JSON per document |
| **4** | Cross-reference extraction | Inter-document and standards references identified? | Cross-reference manifest per document |
| **5** | LLM-driven feature taxonomy derivation | Bottom-up taxonomy produces sensible groupings? | Feature taxonomy JSON + human review notes |
| **6** | Download referenced 3GPP specs; selective section extraction | Can we obtain and parse referenced standards sections? | Standards sections JSON |
| **7** | Knowledge graph construction (with MNO/release metadata) | Graph structure correct and complete? Architecture supports future multi-MNO expansion? | NetworkX graph, visualizable, queryable |
| **8** | Vector store construction with contextualized, metadata-tagged chunks | Baseline retrieval quality with metadata filtering? | FAISS/ChromaDB index |
| **9** | Query pipeline implementation (MNO/release resolution → graph scoping → targeted RAG → context assembly → LLM synthesis) | **Core validation: can the system answer cross-document questions?** | Working query pipeline |
| **10** | Evaluation on test questions | Quantified performance | Evaluation report |

### 9.4 PoC Evaluation

**Test question set (15–20 questions across categories):**

| Category | Example | Count | What It Tests |
|----------|---------|-------|---------------|
| Single-doc factual | "What is the T3402 timer behavior?" | 3–4 | Baseline: single document retrieval |
| Cross-doc dependency | "How does Data Retry handle devices without SMS over IMS?" | 4–5 | **Core test:** graph traversal across documents |
| Feature-level | "What are all the requirements related to device activation?" | 3–4 | Feature taxonomy retrieval |
| Standards comparison | "How does VZW T3402 differ from 3GPP?" | 3–4 | Standards edges and delta summaries |
| Traceability | "What test cases cover EMM cause code handling?" | 2–3 | Traceability edges (if test docs available) |

**Evaluation criteria:**

| Metric | Definition | Target |
|--------|-----------|--------|
| **Completeness** | Did the answer include information from ALL relevant documents? | > 80% of cross-doc questions |
| **Accuracy** | Are cited requirement IDs correct? Are factual claims supported by the source text? | > 90% |
| **Citation quality** | Are specific requirement/test case IDs cited for each claim? | 100% of answers include citations |
| **Standards integration** | When the answer depends on 3GPP text, is it correctly incorporated? | > 80% of standards questions |
| **No hallucination** | Does the system avoid fabricating requirements or misrepresenting text? | 0 hallucinated requirements |

### 9.5 PoC Success Criteria

The PoC is considered successful if:

1. The system correctly answers cross-document questions that require information from 2+ documents, with completeness > 80%
2. Graph scoping demonstrably improves retrieval over pure vector RAG (A/B comparison on the same test questions)
3. Standards integration enables the system to provide complete answers for requirements that defer to 3GPP
4. The architecture is demonstrably portable — no Claude/Gemini-specific features that can't be replicated with the proprietary LLM
5. The graph and vector store metadata schema supports future expansion to multi-MNO and multi-release without structural changes

---

## 10. Risks and Mitigations

| # | Risk | Severity | Likelihood | Mitigation |
|---|------|----------|-----------|------------|
| 1 | **Document extraction quality** — tables, diagrams, embedded objects, and complex formatting may not extract cleanly across all formats (PDF, DOCX, XLS) | High | Medium | DOCX provides better structural access than PDF (headings, native tables). Prefer DOCX when both formats are available. For PDF, evaluate multiple libraries (pymupdf, pdfplumber). For embedded OLE objects, use recursive extraction with `olefile` fallback. Test extraction quality per format early. |
| 2 | **Cross-document edge completeness** — implicit dependencies between documents may not be detected, leading to incomplete answers | High | Medium | Three-layer extraction strategy (deterministic + standards + LLM-based). Measure edge recall during PoC. Add missing edges from evaluation failures. |
| 3 | **Feature taxonomy quality** — LLM-derived taxonomy may incorrectly group or miss features, especially cross-MNO alignment | Medium | Medium | Human review step is mandatory. Start simple (fewer, broader features) and refine. Cross-MNO alignment verified by domain expert. |
| 4 | **Graph traversal explosion** — traversing too many edges returns an unmanageably large candidate set, especially in unified graph with multiple MNOs | Medium | Medium | Configurable depth limits (default: 2 hops). Edge type filtering based on query type. MNO/release metadata filtering at every traversal step. |
| 5 | **Embedding quality for telecom terminology** — standard models may not handle 3GPP acronyms and terminology well | Medium | Medium | Contextualized chunks (hierarchy path + MNO/release prepended) mitigate partially. Evaluate retrieval quality. If insufficient, explore domain-adapted embedding models. |
| 6 | **Standards section availability and versioning** — obtaining the correct version/release of referenced 3GPP sections may be complex; different MNOs reference different releases | Medium | Medium | 3GPP specs are publicly available at 3gpp.org. Pre-download and organize by spec+release in `/Standards/` folder. Standards nodes are versioned by release — the same section at different releases becomes separate nodes. |
| 7 | **LLM portability** — patterns that work with Claude/Gemini may not transfer to the proprietary LLM | Medium | Medium | Avoid Claude/Gemini-specific features (tool use, system prompts). Use simple, well-documented prompt patterns. Test prompt portability early. |
| 8 | **Unified graph scale** — single graph spanning all MNOs × releases could become very large | Low (PoC) / High (Prod) | High | PoC validates architecture with single MNO+release. Production uses Neo4j with indexing on `mno` + `release` attributes. Monitor query latency as graph grows. Physical partitioning (sharding by MNO) is a fallback if needed. |
| 9 | **Relationship type classification errors** — LLM may misclassify defer/constrain/override relationships with standards | Medium | Medium | Include representative examples in classification prompts. Validate classification on a sample during PoC. |
| 10 | **Cross-release requirement matching** — structural changes across versions (renumbered sections, reorganized requirements) may break ID-based matching for `version_of` edges | Medium | Medium | Match by requirement ID first (typically stable across versions). Fall back to section number + text similarity for reorganized sections. Flag low-confidence matches for human review. |
| 11 | **Per-MNO parser maintenance** — each MNO requires a separate parser; format changes across releases require parser updates | Medium | High | Parser design isolates format-specific logic. Version the parsers alongside MNO releases. Detect format changes early by validating extraction output structure. |
| 12 | **Test case document format variability** — test case formats may vary more than requirement formats, even within an MNO | Medium | Medium | Build test case parsers incrementally. Start with the most structured format. Fall back to LLM-based extraction for less structured test docs. |
| 13 | **MNO/release resolution ambiguity** — user queries may not specify MNO or release, leading to incorrect scoping | Low | Medium | Default to latest release. For ambiguous MNO, ask for clarification or search across all (with clear attribution in response). Log resolution decisions for transparency. |
| 14 | **Embedded object extraction failures** — OLE-embedded documents within DOCX may use proprietary/uncommon formats or be corrupted | Medium | Medium | Implement recursive extraction with graceful degradation — if an embedded object can't be extracted, log it and flag for manual review. Track extraction success rate per object type. |
| 15 | **Image/diagram information loss** — diagrams containing requirement-relevant information (call flows, state machines) are opaque to text-based processing | Medium | High | PoC: store images with captions and surrounding text as proxy. Production: use multimodal LLM to extract structured descriptions. Prioritize call flow and state machine diagrams which carry highest-value information. |

---

## Appendix A: VZW OA Document Structure Reference

Based on analysis of `LTEDATARETRY.pdf` (VZW OA, Feb 2026):

```
Document Format:
  - Header: Plan Name, Plan Id, Version Number, Release Date
  - Requirement ID format: VZ_REQ_{PLANID}_{NUMBER}
  - Section numbering: Hierarchical, up to 6+ levels (e.g., 1.4.3.1.1.10)
  - 136 pages

Section Zones:
  1.1    INTRODUCTION (applicability, specs, acronyms, req language)
  1.2    HARDWARE SPECIFICATIONS (mechanical, electrical)
  1.3    SOFTWARE SPECIFICATIONS
    1.3.1  Assumptions (device modes, CSFB, CSG)
    1.3.2  Software Architecture Within the UE
    1.3.3  Generic Throttling Algorithm (rules, details, examples)
    1.3.4–1.3.12  Timers and Specific Behaviors (T3402, T3346, T3245, T3396, etc.)
  1.4    SCENARIOS
    1.4.1  RRC Error scenarios
    1.4.2  EMM Common Procedure scenarios
    1.4.3  EMM Specific Procedure scenarios (Attach, TAU, Service Request)

Cross-Reference Patterns Observed:
  - 3GPP TS 24.301 (NAS protocol) — most frequent
  - References to specific sections and releases (e.g., "Release 10")
  - References to other VZW plan concepts (e.g., "SMS over IMS")

Referenced Standards Release:
  - Captured in Introduction/References section of each document
  - Maps to specific 3GPP/GSMA release (e.g., Release 10)
  - Used to resolve correct standards version during ingestion
```

---

## Appendix B: Technology Stack (PoC)

| Component | Technology | Notes |
|-----------|-----------|-------|
| Language | Python 3.10+ | |
| PDF extraction | pymupdf / pdfplumber | Evaluate both; choose based on table extraction quality |
| DOCX extraction | python-docx | Native access to headings, tables, images, OLE parts |
| XLS/XLSX extraction | openpyxl / pandas | Structured tabular data; merged cell handling |
| OLE object handling | olefile | Fallback for complex embedded objects |
| Image handling | Pillow (extraction); multimodal LLM (production) | PoC: extract and store; production: LLM description |
| Structural parsing | Custom Python (regex + rules) | VZW OA format-specific; extensible parser registry |
| LLM (PoC) | Claude API or Gemini API | For feature extraction, concept tagging, query synthesis |
| Knowledge graph | NetworkX | In-memory, sufficient for single MNO+release PoC |
| Vector store | FAISS or ChromaDB | Local, with metadata filtering support |
| Embedding model | text-embedding-3-large (or similar) | Evaluate retrieval quality; consider domain adaptation if needed |
| Orchestration | Python scripts | No framework needed for PoC |

## Appendix C: Technology Considerations (Production)

| Component | Considerations |
|-----------|---------------|
| LLM | Proprietary LLM with thinking mode; context window TBD |
| Knowledge graph | Neo4j or equivalent graph database; indexed on `mno` + `release` for fast filtered traversal |
| Vector store | Production-grade vector DB (Milvus, Weaviate, or similar on-premise option) with metadata filtering |
| Embedding model | Evaluate proprietary LLM's embedding capability; otherwise on-premise open-source model (e.g., BGE, E5) |
| Parser registry | Per-MNO parser implementations; versioned alongside MNO release format changes |
| Orchestration | Agentic workflow framework for compliance agent; RAG orchestration layer |
| Document pipeline | Automated ingestion pipeline with folder-structure-driven metadata; incremental ingestion for new releases |
| API layer | REST API for Q&A bot, test case queries, and compliance agent interfaces |
| Version management | Release lifecycle management; `is_latest` flag maintenance; `version_of` edge construction |
