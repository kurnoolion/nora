# Telecom Requirements AI System — PoC

AI system for intelligent querying, cross-referencing, and compliance analysis of US MNO device requirement specifications. Uses a Knowledge Graph + RAG hybrid architecture.

**Current status:** PoC Steps 1, 2, 3, 5, 6 implemented. Steps 4, 7-11 pending.

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
| **Total** | **147** | | |

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
│   └── taxonomy/                          # Step 6: Feature taxonomy
├── tests/                                 # 147 tests across 6 test files
├── data/
│   ├── extracted/                        # Step 1 output: IR JSON files
│   ├── parsed/                           # Step 3 output: RequirementTree JSON files
│   ├── resolved/                         # Step 5 output: Cross-reference manifests
│   └── taxonomy/                         # Step 6 output: Feature taxonomy JSON files
└── *.pdf                                 # Source VZW OA specification PDFs
```
