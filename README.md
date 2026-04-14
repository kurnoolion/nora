# Telecom Requirements AI System — PoC

AI system for intelligent querying, cross-referencing, and compliance analysis of US MNO device requirement specifications. Uses a Knowledge Graph + RAG hybrid architecture.

**Current status:** PoC Steps 1, 2, 3, 5, 6, 7, 8, 9, 10, 11 implemented. Step 4 pending.

## Prerequisites

```bash
# Python 3.12+
python --version

# Install dependencies
pip install -r requirements.txt

# For tests
pip install pytest
```

### Source Documents

The following VZW Open Alliance PDFs must be present in the repo root:

- `LTESMS.pdf`
- `LTEAT.pdf`
- `LTEB13NAC.pdf`
- `LTEDATARETRY.pdf`
- `LTEOTADM.pdf`

### System Dependencies (optional)

- **LibreOffice** — required for automatic DOC→DOCX conversion of 3GPP spec downloads. Install with `apt install libreoffice` or equivalent. If not available, the system will log a warning; you can manually convert DOC files.

## Quick Start — Run the Full Pipeline

Run all steps sequentially to regenerate all output from source PDFs:

```bash
# Step 1: Extract document content → data/extracted/
python -m src.extraction.extract *.pdf --output data/extracted

# Step 2: Create document profile → profiles/vzw_oa_profile.json
python -m src.profiler.profile_cli create \
    --name VZW_OA \
    --docs data/extracted/LTEDATARETRY_ir.json data/extracted/LTEB13NAC_ir.json \
    --output profiles/vzw_oa_profile.json

# Step 3: Parse all documents → data/parsed/
python -m src.parser.parse_cli \
    --profile profiles/vzw_oa_profile.json \
    --docs-dir data/extracted \
    --output-dir data/parsed

# Step 5: Resolve cross-references → data/resolved/
python -m src.resolver.resolve_cli \
    --trees-dir data/parsed \
    --output-dir data/resolved

# Step 6: Extract feature taxonomy → data/taxonomy/
python -m src.taxonomy.taxonomy_cli \
    --trees-dir data/parsed \
    --output-dir data/taxonomy

# Step 7: Ingest referenced 3GPP standards → data/standards/
# (downloads specs from 3GPP FTP — requires internet access)
python -m src.standards.standards_cli \
    --manifests-dir data/resolved \
    --trees-dir data/parsed \
    --output-dir data/standards

# Step 8: Build knowledge graph → data/graph/
python -m src.graph.graph_cli --verify

# Step 9: Build vector store → data/vectorstore/
python -m src.vectorstore.vectorstore_cli

# Step 10: Query the system
python -m src.query.query_cli --query "What is the T3402 timer behavior?"

# Step 11: Evaluate the pipeline
python -m src.eval.eval_cli                       # Run all 18 test questions
python -m src.eval.eval_cli --ab                  # A/B: graph-scoped vs pure RAG
python -m src.eval.eval_cli --output data/eval/report.json  # Save report
```

## Running Tests

```bash
# Run all tests (excluding tests that require pymupdf)
python -m pytest tests/ --ignore=tests/test_pipeline.py -v

# Run all tests including pipeline integration (requires pymupdf)
python -m pytest tests/ -v

# Run tests for a specific step
python -m pytest tests/test_document_ir.py -v     # Step 1: IR data model
python -m pytest tests/test_profile_schema.py -v   # Step 2: Profile schema
python -m pytest tests/test_patterns.py -v          # Steps 2-3: Regex patterns
python -m pytest tests/test_pipeline.py -v          # Steps 1-3: End-to-end pipeline
python -m pytest tests/test_resolver.py -v          # Step 5: Cross-references
python -m pytest tests/test_taxonomy.py -v          # Step 6: Feature taxonomy
python -m pytest tests/test_standards.py -v          # Step 7: Standards ingestion
python -m pytest tests/test_graph.py -v              # Step 8: Knowledge graph
python -m pytest tests/test_vectorstore.py -v        # Step 9: Vector store
python -m pytest tests/test_query.py -v              # Step 10: Query pipeline
python -m pytest tests/test_eval.py -v               # Step 11: Evaluation framework
```

### Test Summary

| Test File | Count | Covers | Dependencies |
|---|---|---|---|
| `test_document_ir.py` | 10 | IR serialize/deserialize round-trip, block types, positions, metadata | None |
| `test_profile_schema.py` | 9 | Profile round-trip for all nested structures, loads real VZW profile | `profiles/vzw_oa_profile.json` |
| `test_patterns.py` | 39 | Section numbering, req IDs, plan ID extraction, 3GPP spec parsing, h/f patterns | None |
| `test_pipeline.py` | 30 | End-to-end extract, profile, parse on real PDFs, cross-ref consistency | `pymupdf`, source PDFs |
| `test_resolver.py` | 19 | Internal/cross-plan/standards resolution, manifest round-trip, pipeline integration | `data/parsed/` trees |
| `test_taxonomy.py` | 40 | LLM protocol, mock provider, extractor, consolidator, schema round-trips, pipeline | `data/parsed/` trees |
| `test_standards.py` | 35 | Spec resolver encoding/URLs, reference collector, spec parser, section extractor, schemas | `data/resolved/`, `data/parsed/`, downloaded spec DOCX |
| `test_graph.py` | 48 | Schema IDs, graph builders, serialization, full build, integration diagnostics | `networkx`, parsed/resolved/taxonomy/standards data |
| `test_vectorstore.py` | 57 | Config, protocols, chunk builder, deduplication, builder, integration with real data | `data/parsed/`, `data/taxonomy/` |
| `test_query.py` | 55 | Schema models, analyzer, resolver, graph scoper, RAG retriever, context builder, synthesizer, pipeline orchestration, integration | `networkx` |
| `test_eval.py` | 36 | Question set structure, metric scoring, report serialization, A/B comparison, runner integration with synthetic graph | `networkx` |
| **Total** | **378** | | |

## Step-by-Step Details

### Step 1 — Document Content Extraction

Extracts text, tables, and images from PDFs into a normalized intermediate representation (DocumentIR).

```bash
# Extract a single document
python -m src.extraction.extract LTEDATARETRY.pdf --output data/extracted

# Extract all PDFs in a directory
python -m src.extraction.extract /path/to/pdfs/ --output data/extracted
```

**Output:** `data/extracted/<name>_ir.json`

**Verify:** Each IR file contains blocks with text content, font metadata, positions, and block types (TEXT, TABLE, IMAGE). Tables include structured cell data.

```bash
# Check block counts
python -c "
from src.models.document import DocumentIR
ir = DocumentIR.load_json('data/extracted/LTEDATARETRY_ir.json')
print(f'Pages: {ir.page_count}, Blocks: {len(ir.blocks)}')
print(f'Text: {sum(1 for b in ir.blocks if b.block_type.value==\"text\")}')
print(f'Tables: {sum(1 for b in ir.blocks if b.block_type.value==\"table\")}')
"
```

### Step 2 — DocumentProfiler

Analyzes representative documents to derive a document structure profile. The profile captures heading styles, requirement ID patterns, section numbering, cross-reference patterns, and document zones — all without using an LLM.

```bash
# Create profile from representative docs
python -m src.profiler.profile_cli create \
    --name VZW_OA \
    --docs data/extracted/LTEDATARETRY_ir.json data/extracted/LTEB13NAC_ir.json \
    --output profiles/vzw_oa_profile.json

# Validate profile against a held-out document
python -m src.profiler.profile_cli validate \
    --profile profiles/vzw_oa_profile.json \
    --doc data/extracted/LTESMS_ir.json

# Update profile with additional docs
python -m src.profiler.profile_cli update \
    --profile profiles/vzw_oa_profile.json \
    --docs data/extracted/LTEOTADM_ir.json
```

**Output:** `profiles/vzw_oa_profile.json`

**Verify:** The profile should contain:
- `heading_detection` — font sizes and styles for heading levels
- `requirement_id` — regex pattern matching VZ_REQ IDs with components config
- `plan_metadata` — title, MNO, release extraction patterns
- `document_zones` — zone boundaries (meta, software specs, scenarios)
- `cross_references` — 3GPP citation patterns, req ID reference patterns

```bash
# Inspect profile
python -c "
import json
with open('profiles/vzw_oa_profile.json') as f:
    p = json.load(f)
print(f'Profile: {p[\"profile_name\"]}')
print(f'Heading levels: {len(p[\"heading_detection\"][\"heading_levels\"])}')
print(f'Req ID pattern: {p[\"requirement_id\"][\"patterns\"][0]}')
print(f'Zones: {len(p[\"document_zones\"][\"zones\"])}')
"
```

### Step 3 — Generic Structural Parser

Applies a document profile to parse extracted IR into a structured requirement tree. Each section becomes a Requirement node with cross-references, tables, and parent-child relationships.

```bash
# Parse a single document
python -m src.parser.parse_cli \
    --profile profiles/vzw_oa_profile.json \
    --doc data/extracted/LTEDATARETRY_ir.json \
    --output data/parsed/LTEDATARETRY_tree.json

# Parse all documents in a directory
python -m src.parser.parse_cli \
    --profile profiles/vzw_oa_profile.json \
    --docs-dir data/extracted \
    --output-dir data/parsed
```

**Output:** `data/parsed/<name>_tree.json`

**Verify:** Each tree contains plan metadata and a flat list of requirements with section hierarchy.

```bash
# Check parsed tree
python -c "
from src.parser.structural_parser import RequirementTree
tree = RequirementTree.load_json('data/parsed/LTEDATARETRY_tree.json')
print(f'Plan: {tree.plan_id}, MNO: {tree.mno}, Release: {tree.release}')
print(f'Requirements: {len(tree.requirements)}')
# Show first 5 requirements
for r in tree.requirements[:5]:
    xrefs = r.cross_references
    print(f'  {r.section_number} {r.title[:50]}')
    print(f'    Req ID: {r.req_id}, Internal refs: {len(xrefs.internal)}, '
          f'Standards: {len(xrefs.standards)}, External plans: {len(xrefs.external_plans)}')
"
```

**Expected output for LTEDATARETRY:** ~115 requirements, plan_id=LTEDATARETRY, mno=VZW.

### Step 5 — Cross-Reference Resolver

Resolves cross-references from parsed trees: internal refs (same document), cross-plan refs (other documents in corpus), and standards refs (3GPP TS citations with release info).

```bash
# Resolve all parsed trees
python -m src.resolver.resolve_cli \
    --trees-dir data/parsed \
    --output-dir data/resolved

# Resolve specific trees
python -m src.resolver.resolve_cli \
    --trees data/parsed/LTEDATARETRY_tree.json data/parsed/LTESMS_tree.json \
    --output-dir data/resolved
```

**Output:** `data/resolved/<name>_xrefs.json`

**Verify:** The CLI prints a summary table. Check that:
- Internal refs: most resolved, some broken (body-text req IDs that don't have their own section headings — expected)
- Cross-plan refs: resolved if the referenced plan is in the 5-doc corpus, unresolved otherwise (e.g., LTEOTADM references MMOTADM/ODOTADM which aren't in the corpus)
- Standards refs: resolved when release info is available (inline or doc-level), unresolved for LTEAT (no release metadata)

```bash
# Inspect a manifest
python -c "
import json
with open('data/resolved/LTEDATARETRY_xrefs.json') as f:
    m = json.load(f)
s = m['summary']
print(f'Plan: {m[\"plan_id\"]}')
print(f'Internal: {s[\"resolved_internal\"]}/{s[\"total_internal\"]} '
      f'(broken: {s[\"broken_internal\"]})')
print(f'Cross-plan: {s[\"resolved_cross_plan\"]}/{s[\"total_cross_plan\"]}')
print(f'Standards: {s[\"resolved_standards\"]}/{s[\"total_standards\"]}')
"
```

### Step 6 — Feature Taxonomy

Extracts telecom features from each document using an LLM (mock provider for testing), then consolidates into a unified taxonomy. The LLM abstraction layer supports swapping providers.

```bash
# Build taxonomy from all parsed trees
python -m src.taxonomy.taxonomy_cli \
    --trees-dir data/parsed \
    --output-dir data/taxonomy

# Verbose mode
python -m src.taxonomy.taxonomy_cli --trees-dir data/parsed -v
```

**Output:**
- `data/taxonomy/<plan_id>_features.json` — per-document feature extraction
- `data/taxonomy/taxonomy.json` — unified taxonomy

**Verify:** The CLI prints a summary table showing all features with primary/referenced counts. Check that:
- Each feature has a `feature_id`, `name`, `description`, and `keywords`
- `is_primary_in` lists plans where this feature is a main topic
- `is_referenced_in` lists plans that mention it without defining it
- `mno_coverage` maps MNO to plans containing the feature
- No duplicate `feature_id` values in the unified taxonomy

```bash
# Inspect unified taxonomy
python -c "
import json
with open('data/taxonomy/taxonomy.json') as f:
    t = json.load(f)
print(f'MNO: {t[\"mno\"]}, Release: {t[\"release\"]}')
print(f'Source docs: {len(t[\"source_documents\"])}')
print(f'Total features: {len(t[\"features\"])}')
print()
for f in t['features']:
    print(f'{f[\"feature_id\"]:<25s}  primary={len(f[\"is_primary_in\"])}  '
          f'ref={len(f[\"is_referenced_in\"])}  '
          f'plans={f[\"source_plans\"]}')
"

# Inspect per-document features
python -c "
import json
with open('data/taxonomy/LTEDATARETRY_features.json') as f:
    d = json.load(f)
print(f'Plan: {d[\"plan_id\"]}, MNO: {d[\"mno\"]}')
print(f'Primary features: {len(d[\"primary_features\"])}')
for f in d['primary_features']:
    print(f'  {f[\"feature_id\"]}: {f[\"name\"]} (confidence={f[\"confidence\"]})')
print(f'Referenced features: {len(d[\"referenced_features\"])}')
print(f'Key concepts: {d[\"key_concepts\"]}')
"
```

**Note on MockLLMProvider:** The current taxonomy uses a keyword-matching mock provider (no API keys needed). Results are approximate — e.g., IMS_REGISTRATION appears across all docs because many headings contain "registration". When a real LLM provider is configured, feature extractions will be more domain-accurate.

### Step 7 — Standards Ingestion

Collects all 3GPP standards references from MNO requirement documents, downloads the referenced spec versions from the 3GPP FTP archive, parses them into section trees, and extracts referenced sections with surrounding context. Fully generic — no hardcoded spec lists or MNO-specific logic. No LLM required.

```bash
# Full pipeline: collect refs, download, parse, extract
python -m src.standards.standards_cli \
    --manifests-dir data/resolved \
    --trees-dir data/parsed \
    --output-dir data/standards

# Collect references only (no download)
python -m src.standards.standards_cli --collect-only

# Process only specific specs
python -m src.standards.standards_cli --specs 24.301 36.331

# Skip download (use already-cached specs)
python -m src.standards.standards_cli --no-download

# Limit specs to process (useful for testing)
python -m src.standards.standards_cli --max-specs 3
```

**Output:**
- `data/standards/reference_index.json` — aggregated reference index
- `data/standards/TS_{spec}/Rel-{N}/` — per-spec per-release directory containing:
  - `{compact}-{code}.zip` — original 3GPP archive
  - `{compact}-{code}.docx` — extracted/converted spec document
  - `spec_parsed.json` — full section tree
  - `sections.json` — extracted referenced + context sections

**Verify:**

```bash
# Check reference index
python -c "
import json
with open('data/standards/reference_index.json') as f:
    idx = json.load(f)
print(f'Total refs: {idx[\"total_refs\"]}')
print(f'Unique specs: {idx[\"total_unique_specs\"]}')
print(f'Spec-release pairs: {len(idx[\"specs\"])}')
# Show specs with section-level detail
with_sec = [s for s in idx['specs'] if s['sections']]
print(f'Specs with section-level refs: {len(with_sec)}')
for s in with_sec[:5]:
    print(f'  TS {s[\"spec\"]} {s[\"release\"]}: sections={s[\"sections\"][:3]}')
"

# Check parsed spec
python -c "
from src.standards.schema import SpecDocument
doc = SpecDocument.load_json('data/standards/TS_24.301/Rel-11/spec_parsed.json')
print(f'TS {doc.spec_number} v{doc.version} ({doc.release})')
print(f'Total sections: {len(doc.sections)}')
sec = doc.get_section('5.5.1.2.5')
if sec:
    print(f'Section 5.5.1.2.5: {sec.title} ({len(sec.text)} chars)')
"

# Check extracted sections
python -c "
import json
with open('data/standards/TS_24.301/Rel-11/sections.json') as f:
    data = json.load(f)
print(f'Referenced: {len(data[\"referenced_sections\"])} sections')
print(f'Context: {len(data[\"context_sections\"])} sections')
print(f'Source plans: {data[\"source_plans\"]}')
"
```

**How it works:**
1. **Reference collection** scans cross-ref manifests for spec+release pairs, then scans requirement text for section-level references (e.g., "3GPP TS 24.301, section 5.5.3.2.5")
2. **Spec resolution** maps each (spec, release) to the 3GPP FTP URL by probing the archive directory listing for the latest version of that release
3. **Download** fetches the ZIP, extracts DOC/DOCX, auto-converts DOC→DOCX via LibreOffice headless
4. **Parsing** uses python-docx Heading styles (Heading 1-6) and section numbering to build a section tree
5. **Extraction** pulls referenced sections + parent/sibling/definitions context (typically 5-15% of the full spec)

### Step 8 — Knowledge Graph Construction

Builds a unified NetworkX DiGraph from all ingestion outputs: parsed trees, cross-reference manifests, feature taxonomy, and standards sections.

```bash
# Build graph with diagnostic queries
python -m src.graph.graph_cli --verify

# Build graph without diagnostics
python -m src.graph.graph_cli
```

**Output:**
- `data/graph/knowledge_graph.json` — full graph (node-link JSON)
- `data/graph/graph_stats.json` — summary statistics

### Step 9 — Vector Store Construction

Creates embeddings for requirement chunks and stores them in a vector store with metadata for filtered retrieval. All settings (embedding model, vector DB backend, distance metric) are configurable.

```bash
# Build with defaults (all-MiniLM-L6-v2, ChromaDB, cosine)
python -m src.vectorstore.vectorstore_cli

# Use a different embedding model
python -m src.vectorstore.vectorstore_cli --model all-mpnet-base-v2

# Use a config file for reproducible experiments
python -m src.vectorstore.vectorstore_cli --config configs/experiment1.json

# Override distance metric
python -m src.vectorstore.vectorstore_cli --metric l2

# Force rebuild (clear existing data)
python -m src.vectorstore.vectorstore_cli --rebuild

# Inspect existing store
python -m src.vectorstore.vectorstore_cli --info

# Test query against the store
python -m src.vectorstore.vectorstore_cli --query "T3402 timer behavior"
python -m src.vectorstore.vectorstore_cli --query "attach reject" --filter-plan LTEDATARETRY --n-results 5

# Save config alongside results for reproducibility
python -m src.vectorstore.vectorstore_cli --save-config configs/baseline.json
```

**Output:**
- `data/vectorstore/` — ChromaDB persistent data
- `data/vectorstore/build_stats.json` — build statistics
- `data/vectorstore/config.json` — config used for this build

**Configurable parameters (via JSON config or CLI flags):**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `embedding_model` | `all-MiniLM-L6-v2` | HuggingFace model name |
| `embedding_provider` | `sentence-transformers` | Embedding backend |
| `embedding_batch_size` | `64` | Batch size for encoding |
| `embedding_device` | `cpu` | Device (`cpu`, `cuda`, `mps`) |
| `normalize_embeddings` | `true` | L2-normalize vectors |
| `vector_store_backend` | `chromadb` | Vector store backend |
| `distance_metric` | `cosine` | `cosine`, `l2`, or `ip` |
| `collection_name` | `requirements` | Collection name |
| `include_mno_header` | `true` | Prepend MNO/release/plan header |
| `include_hierarchy_path` | `true` | Prepend section hierarchy path |
| `include_tables` | `true` | Include tables as Markdown |

### Step 10 — Query Pipeline

6-stage pipeline: Query Analysis → MNO/Release Resolution → Graph Scoping → Targeted RAG → Context Assembly → LLM Synthesis. Each stage has a mock implementation, so the full pipeline works without API keys.

```bash
# Single query
python -m src.query.query_cli --query "What is the T3402 timer behavior?"

# Verbose mode (shows all pipeline stages)
python -m src.query.query_cli --query "T3402 timer" --verbose

# Interactive mode
python -m src.query.query_cli --interactive

# Custom settings
python -m src.query.query_cli --query "..." --top-k 15 --max-depth 3

# Save response to JSON
python -m src.query.query_cli --query "..." --output response.json
```

**Output:** Query response with answer, citations, and pipeline statistics.

**Pipeline stages:**

| Stage | Module | Description |
|-------|--------|-------------|
| 1. Query Analysis | `analyzer.py` | Extracts entities, concepts, MNOs, features, plan IDs from natural language |
| 2. MNO/Release Resolution | `resolver.py` | Maps MNO names to available graph scopes (explicit, latest, or all) |
| 3. Graph Scoping | `graph_scope.py` | Traverses knowledge graph to find candidate requirement nodes |
| 4. Targeted RAG | `rag_retriever.py` | Retrieves relevant chunks from vector store, scoped by graph candidates |
| 5. Context Assembly | `context_builder.py` | Enriches chunks with graph context (hierarchy, standards, cross-refs) |
| 6. LLM Synthesis | `synthesizer.py` | Generates answer with citations from assembled context |

**Graph scoping strategies:** Entity lookup (req IDs, timers), feature lookup (maps_to edges), plan lookup (all reqs in plan), title search (text matching). BFS edge traversal with configurable depth and score decay (0.7^depth).

**RAG retrieval:** Two strategies — scoped retrieval (filter vector store by graph candidate req_ids) and metadata retrieval (filter by MNO/release when no graph candidates). Diversity enforcement ensures minimum chunks per plan.

**Mock implementations:** `MockQueryAnalyzer` uses keyword matching and regex patterns. `MockSynthesizer` returns structured summaries grouping results by plan with req IDs and standards references. Both exercise the full pipeline without requiring LLM API keys.

### Step 11 — Evaluation

Evaluates the query pipeline on 18 test questions across 5 categories, measuring completeness, accuracy, citation quality, standards integration, and hallucination avoidance. Supports A/B comparison between graph-scoped and pure RAG retrieval.

```bash
# Run all 18 evaluation questions (graph-scoped mode)
python -m src.eval.eval_cli

# Run A/B comparison (graph-scoped vs pure RAG)
python -m src.eval.eval_cli --ab

# Run a specific category only
python -m src.eval.eval_cli --category cross_doc

# Save report to JSON
python -m src.eval.eval_cli --output data/eval/report.json

# Verbose mode (shows pipeline stage details)
python -m src.eval.eval_cli --verbose
```

**Test question categories (18 questions):**

| Category | Count | What It Tests |
|----------|-------|---------------|
| Single-doc factual | 4 | Baseline single-document retrieval (T3402 timer, throttling, AT commands, FOTA) |
| Cross-doc dependency | 4 | Graph traversal across documents (SMS over IMS, PDN connectivity, IMS registration, detach) |
| Feature-level | 4 | Feature taxonomy retrieval (data retry, error handling, bearer management, PLMN selection) |
| Standards comparison | 3 | Standards edges and references (T3402 vs 3GPP, attach reject sections, TS 36.331) |
| Traceability | 3 | Entity lookup (req ID, IMS throttling, cause code 22) |

**Evaluation metrics (per TDD 9.4):**

| Metric | Definition | TDD Target |
|--------|-----------|------------|
| Completeness | Did the answer include information from ALL expected plans? | > 80% (cross-doc) |
| Accuracy | Are expected requirement IDs found in the results? | > 90% |
| Citation quality | Do answers include requirement and standards citations? | 100% |
| Standards integration | Are referenced 3GPP specs correctly incorporated? | > 80% |
| No hallucination | No fabricated requirement IDs from unknown plans | 100% |

**A/B comparison:** Runs all questions twice — once with graph scoping (normal pipeline) and once bypassing graph scoping (pure vector RAG with metadata filters only). Reports per-question and per-category deltas to demonstrate graph value.

## Swapping the LLM Provider

The LLM abstraction uses Python's Protocol (structural typing). To use a real LLM:

1. Create a class with a `complete()` method matching this signature:

```python
class YourProvider:
    def complete(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        # Call your LLM API, return the text response
        ...
```

2. Pass it to the extractor:

```python
from src.taxonomy.extractor import FeatureExtractor

provider = YourProvider(api_key="...", model="...")
extractor = FeatureExtractor(provider)
```

No base class inheritance required. See `src/llm/base.py` for full documentation.

## Project Structure

```
req-agent/
├── CLAUDE.md                              # Claude Code instructions
├── SESSION_SUMMARY.md                     # Session context for continuity
├── README.md                              # This file
├── TDD_Telecom_Requirements_AI_System.md  # Full technical design (v0.4)
├── requirements.txt                       # Python dependencies
├── profiles/
│   └── vzw_oa_profile.json               # VZW OA document profile
├── src/
│   ├── models/document.py                # Normalized IR data model
│   ├── extraction/                       # Step 1: PDF content extraction
│   ├── profiler/                          # Step 2: Document profiling
│   ├── parser/                            # Step 3: Structural parsing
│   ├── resolver/                          # Step 5: Cross-reference resolution
│   ├── llm/                               # LLM abstraction layer
│   ├── taxonomy/                          # Step 6: Feature taxonomy
│   ├── standards/                         # Step 7: 3GPP standards ingestion
│   ├── graph/                             # Step 8: Knowledge graph construction
│   ├── vectorstore/                       # Step 9: Vector store construction
│   ├── query/                             # Step 10: Query pipeline (6-stage)
│   └── eval/                              # Step 11: Evaluation framework
├── tests/                                 # 378 tests across 11 test files
├── data/
│   ├── extracted/                        # Step 1 output: IR JSON files
│   ├── parsed/                           # Step 3 output: RequirementTree JSON files
│   ├── resolved/                         # Step 5 output: Cross-reference manifests
│   ├── taxonomy/                         # Step 6 output: Feature taxonomy JSON files
│   ├── standards/                        # Step 7 output: Downloaded + parsed 3GPP specs
│   ├── graph/                            # Step 8 output: Knowledge graph JSON + stats
│   └── vectorstore/                      # Step 9 output: ChromaDB data + config + stats
└── *.pdf                                 # Source VZW OA specification PDFs
```
