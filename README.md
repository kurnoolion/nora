# NORA — Network Operator Requirements Analyzer (PoC)

AI system for intelligent querying, cross-referencing, and compliance analysis of US MNO device requirement specifications. Uses a Knowledge Graph + RAG hybrid architecture.

**Current status:** PoC Steps 1, 2, 3, 5, 6, 7, 8, 9, 10, 11 implemented. Step 4 pending. Local LLM (Ollama + Gemma 3 12B / Gemma 4 E4B) integrated. Citation quality improved with few-shot prompting and context-based fallback. Automated pipeline runner, multi-user environment system, and collaboration tooling added for team workflows. **Web UI** (FastAPI + Bootstrap 5 + HTMX, vendored assets for offline/proxy environments) provides browser-based access for team members who primarily work on Windows PCs, now including in-browser **Corrections UI** (profile + taxonomy editing with compact FIX reports). Metrics and observability instrumentation with persistent SQLite storage.

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

### Local LLM (optional, recommended)

The system supports local LLM inference via [Ollama](https://ollama.com) for real answer synthesis (as opposed to mock keyword-based responses).

```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull the recommended model (Gemma 4 E4B, ~9.6 GB)
ollama pull gemma4:e4b

# Verify
ollama list
```

**Model selection rationale** (for 16GB RAM, CPU-only):

| Model | Size (Q4) | RAM needed | Recommendation |
|-------|-----------|------------|----------------|
| `gemma4:e4b` | 9.6 GB | ~11 GB | **Recommended** — 4B effective params via PLE, 128K context, good reasoning |
| `gemma4:e2b` | 3.3 GB | ~4 GB | Fallback if RAM is tight |
| `gemma3:4b` | 3 GB | ~4 GB | Alternative fallback, older architecture |
| `gemma4:26b` | 18 GB | ~20 GB | Won't fit on 16GB alongside pipeline |

CPU inference runs at ~10-13 tok/s on Intel Core Ultra 9 185H, producing answers in ~2-4 minutes per query.

### Offline Install (proxy-restricted environments)

If `curl` HTTPS downloads fail (e.g., corporate proxy with self-signed certificates):

1. Download files listed in `download_urls.txt` on a machine with browser access
2. Transfer to the target machine
3. Run: `./setup_env.sh --download-dir /path/to/downloads`

For full offline setup on a Work PC with no internet — Ollama runtime, Gemma model weights (gemma3:12b / gemma4:e4b), and the HuggingFace sentence-embedding model — see `SETUP_OFFLINE.md`.

### System Dependencies (optional)

- **LibreOffice** — required for automatic DOC→DOCX conversion of 3GPP spec downloads. Install with `apt install libreoffice` or equivalent. If not available, the system will log a warning; you can manually convert DOC files.

## Quick Start — Automated Pipeline Runner

The pipeline runner chains all stages with a single command, generates compact reports, and supports multi-user environment configs.

```bash
# Run the full pipeline on documents in current directory
python -m core.src.pipeline.run_cli --docs . --start extract --end eval

# Run specific stages (by name or number)
python -m core.src.pipeline.run_cli --docs . --start profile --end parse
python -m core.src.pipeline.run_cli --docs . --start 1 --end 3

# Run using an environment config (see Environment Management below)
python -m core.src.pipeline.run_cli --env profiler-review

# Use mock LLM (no Ollama required)
python -m core.src.pipeline.run_cli --docs . --model mock --start taxonomy --end graph

# Continue past failed stages
python -m core.src.pipeline.run_cli --docs . --continue-on-error

# List available stages
python -m core.src.pipeline.run_cli --list-stages

# Detect hardware and get model recommendation
python -m core.src.pipeline.run_cli --detect-hw

# Show quality check / correction feedback templates
python -m core.src.pipeline.run_cli --qc-template profile
python -m core.src.pipeline.run_cli --fix-template taxonomy
```

**Pipeline output** includes a compact report block (paste in chat for collaborative debugging):
```
RPT standalone 2026-04-15T15:53
HW CPU=Intel Core Ultra 9 185H(22c) RAM=15G GPU=none
MDL gemma4:e4b
PRF OK 0s lvl=4 rpat=1 zone=9
PRS OK 0s req=711 dep=11 docs=5
RES WARN 0s int=301 xp=1 std=320
ERR none
```

## Web UI (Browser-Based Access)

For team members who prefer a browser-based interface over the CLI:

```bash
# Start the web server
python -m core.src.web.app

# Access at http://localhost:8000
# Or behind a reverse proxy at the configured root_path (e.g., /nora)
```

**Features:**
- **Dashboard** — System status, Ollama connectivity, GPU info, recent jobs
- **Pipeline** — Submit pipeline runs via form (select stages, model, document path)
- **Jobs** — Monitor running jobs with real-time log streaming (SSE)
- **Query** — Submit questions against the knowledge graph, view results
- **Test** — Free-form Q&A with thumbs-up/down + comment capture (logged to `<env_dir>/state/nora_test_feedback.db`). Surfaces two citation views — *Cited by LLM* (extracted from the answer text) and *Returned by RAG* (full Stage-4 retrieval) — each clickable to expand the underlying chunk text inline.
- **Corrections** — In-browser profile and taxonomy editing with compact FIX reports (see below)
- **Environments** — Create/manage team member environments
- **Files** — Browse shared network folders (Windows paths auto-mapped to Linux)
- **Metrics** — Observability dashboard with request timing, LLM stats, resource usage

**Offline-friendly assets:** Bootstrap 5, Bootstrap Icons, and HTMX are vendored under `src/web/static/vendor/`, so the UI renders correctly on proxy-restricted machines with no CDN access.

**Configuration** (`config/web.json`):
```json
{
    "host": "0.0.0.0",
    "port": 8000,
    "root_path": "/nora",
    "ollama_url": "http://localhost:11434",
    "default_model": "gemma3:12b",
    "env_dir": "",
    "path_mappings": [
        {
            "windows": "\\\\SERVER\\OADocs",
            "linux": "/mnt/oa_docs",
            "label": "OA Documents"
        }
    ]
}
```

**Path mappings** translate Windows UNC paths (used by team members on Windows PCs) to Linux mount points on the server. Team members can paste Windows paths in forms; the server resolves them automatically.

**Reverse proxy:** Set `root_path` to match your reverse proxy prefix (e.g., `/nora`). All URLs in the UI will be prefixed accordingly.

### Configuration — 3-tier resolution

Every runtime setting below resolves through the same chain (highest priority wins): **CLI flag → environment variable → config file**. Empty / unset values at any tier fall through. Config files under `config/` are tracked templates with empty defaults; fill in locally.

| Module       | Setting              | CLI flag                                                  | Env var                  | Config file                                         |
|--------------|----------------------|-----------------------------------------------------------|--------------------------|-----------------------------------------------------|
| **env**      | env_dir              | `--env-dir <path>` (web + pipeline)                       | `ENV_DIR`                | `config/web.json:env_dir` ¹ / `config/env.json:env_dir` |
| **web db**   | jobs_db              | `--jobs-db <path>`                                        | `NORA_JOBS_DB`           | `config/env.json:jobs_db`                           |
| **web db**   | metrics_db           | `--metrics-db <path>`                                     | `NORA_METRICS_DB`        | `config/env.json:metrics_db`                        |
| **web db**   | feedback_db          | `--feedback-db <path>`                                    | `NORA_FEEDBACK_DB`       | `config/env.json:feedback_db`                       |
| **web**      | host                 | `--host <ip>`                                             | —                        | `config/web.json:host`                              |
| **web**      | port                 | `--port <n>`                                              | —                        | `config/web.json:port`                              |
| **llm**      | llm_provider         | `--llm-provider {ollama,openai-compatible,mock}`          | `NORA_LLM_PROVIDER`      | `config/llm.json:llm_provider` ²                    |
| **llm**      | llm_model            | `--model <name>`                                          | `NORA_LLM_MODEL`         | `config/llm.json:llm_model` ²                       |
| **llm**      | llm_timeout          | `--model-timeout <sec>`                                   | —                        | `config/llm.json:llm_timeout`                       |
| **llm**      | llm_base_url         | —                                                         | `NORA_LLM_BASE_URL`      | `config/llm.json:llm_base_url`                      |
| **llm**      | llm_api_key          | —                                                         | `NORA_LLM_API_KEY`       | `config/llm.json:llm_api_key`                       |
| **llm**      | embedding_provider   | `--embedding-provider {sentence-transformers,huggingface,ollama}` | `NORA_EMBEDDING_PROVIDER` | `config/llm.json:embedding_provider` ²              |
| **llm**      | embedding_model      | `--embedding-model <name>`                                | `NORA_EMBEDDING_MODEL`   | `config/llm.json:embedding_model` ²                 |
| **llm**      | ollama_url           | —                                                         | —                        | `config/llm.json:ollama_url`                        |
| **llm**      | ollama_timeout_s     | —                                                         | `NORA_OLLAMA_TIMEOUT_S`  | `config/llm.json:ollama_timeout_s`                  |
| **pipeline** | skip_taxonomy        | `--skip-taxonomy`                                         | `NORA_SKIP_TAXONOMY=1`   | `config/llm.json:skip_taxonomy`                     |
| **pipeline** | skip_graph           | `--skip-graph`                                            | `NORA_SKIP_GRAPH=1`      | `config/llm.json:skip_graph`                        |
| **pipeline** | rag_only             | `--rag-only` (sets both above)                            | `NORA_RAG_ONLY=1`        | (set both `skip_taxonomy` + `skip_graph`)           |
| **pipeline** | standards_source     | `--standards-source {huggingface,3gpp}`                   | `NORA_STANDARDS_SOURCE`  | `environments/<name>.json:standards_source`         |

¹ For `env_dir` only, `config/web.json` wins over `$ENV_DIR` (back-compat with pre-existing single-machine setups). The order is **`config/web.json` > `--env-dir` > `$ENV_DIR` > `config/env.json`**. Web app: leaves `web.json:env_dir` empty if you don't want to commit a machine-specific path.

² Legacy fields on `environments/<name>.json` (`model_provider`, `model_name`, `model_timeout`, `embedding_provider`, `embedding_model`) are still honored as a back-compat fallback **below** `config/llm.json`. Deprecated; prefer migrating settings to `config/llm.json`.

**RAG-only mode**: skip the taxonomy + graph stages and run on pure RAG retrieval. The query path builds a stub MNO/Release/Plan graph from vectorstore metadata at runtime; Stage 3 (graph scoping) is bypassed; Stage 4 falls back to MNO/release metadata filtering. Eval stage tolerates the missing graph the same way.

```bash
NORA_RAG_ONLY=1 ENV_DIR=/home/me/work/env_vzw \
  python -m core.src.pipeline.run_cli --start extract --end eval
```

## Corrections UI

Requirement engineers can correct generated artifacts directly in the browser. Each environment gets its own `corrections/` directory that the pipeline auto-detects as an override on the next run.

**Workflow:**

1. Go to **Corrections** in the sidebar — see per-environment status (no output / output only / corrected).
2. Click **Edit profile** or **Edit taxonomy** for an environment.
3. Click **Start correction from output** to seed a copy from the generated artifact.
4. Edit fields in the form:
   - **Profile:** heading numbering pattern, requirement ID pattern + components, header/footer patterns, zone definitions (add/remove/edit), cross-reference patterns, body-text thresholds.
   - **Taxonomy:** searchable feature list — add/remove features, rename, edit keywords and descriptions inline.
5. Click **Save correction**. The file lands at `<doc_root>/corrections/profile.json` or `<doc_root>/corrections/taxonomy.json`.
6. Re-run the pipeline — `src/pipeline/stages.py` picks the correction up automatically.

**Compact FIX report:** Click **FIX report** from either editor to get a pasteable summary of what changed, with **no proprietary document content** (no body text, no feature descriptions, no sample req IDs). Only field names, regex patterns, feature IDs, keyword tokens, and counts. Example:

```
FIX alice-demo taxonomy
feat_total=16 added=1 removed=1 renamed=1 kw_edits=1 desc_edits=0
add: VOLTE_HANDOVER(kws: handover,ho,mobility,srvcc)
remove: IMS_REGISTRATION
rename: LTE Data Retry->LTE Data Retry (Renamed) [DATA_RETRY]
kw: DATA_RETRY +newkw
```

Available also as plain text at `GET /api/corrections/report/<env>` (optional `?artifact=profile|taxonomy|both`).

Files still editable via JSON if preferred — the UI and CLI workflows read/write the same files.

## Environment Management

Environments define scoped workspaces for team members to run specific pipeline stages against specific documents.

```bash
# Create an environment for a team member
python -m core.src.env.env_cli create \
    --name profiler-review \
    --member alice \
    --doc-root /data/vzw-new-batch \
    --stages extract:parse \
    --scope VZW/Feb2026 \
    --objectives "Verify heading detection" "Check table extraction" \
    --created-by mohan

# Create a full-pipeline environment with multiple MNOs
python -m core.src.env.env_cli create \
    --name eval-review \
    --member bob \
    --doc-root /data/multi-mno \
    --stages 1:9 \
    --scope VZW/Feb2026 ATT/Oct2025

# List all environments
python -m core.src.env.env_cli list

# Show environment details and directory status
python -m core.src.env.env_cli show profiler-review

# Initialize directory structure at document_root
python -m core.src.env.env_cli init profiler-review

# Run the pipeline for an environment
python -m core.src.pipeline.run_cli --env profiler-review
```

**Document root layout** (created by `init`):
```
<document_root>/
├── documents/        # Place source documents here (PDF, DOCX, XLS, XLSX)
├── corrections/      # Place corrected artifacts here (profile.json, taxonomy.json)
├── eval/             # Place Q&A eval pairs here (*.xlsx)
├── output/           # Pipeline outputs (auto-generated per stage)
└── reports/          # Pipeline reports (auto-generated)
```

**Correction workflow:** Run pipeline → review artifacts → copy generated file to `corrections/` → edit it → re-run pipeline (corrections auto-detected as overrides).

**User-supplied eval Q&A:** Place an Excel file in `eval/` with columns: `question_id`, `category`, `question`, `expected_plans`, `expected_req_ids`, `expected_features`, `expected_standards`, `expected_concepts`, `min_plans`, `min_chunks`. See `CONTRIBUTING.md` for details.

## Model Selection

The system auto-detects hardware and selects the best Ollama model that fits:

```bash
python -m core.src.pipeline.run_cli --detect-hw
```

| Model | Size (Q4) | RAM/VRAM | GPU-only | Description |
|-------|-----------|----------|----------|-------------|
| `gemma4:27b-it-qat` | 18 GB | >=20GB VRAM | Yes | Best quality, needs large GPU |
| `gemma3:12b` | 8 GB | 12GB+ VRAM or 16GB RAM | No | Strong quality, fits most setups |
| `gemma4:e4b` | 9.6 GB | 11GB+ | No | Good quality, 128K context, CPU-viable |
| `gemma3:4b` | 3 GB | 4GB+ | No | Lighter fallback, fast on CPU |
| `gemma3:1b` | 1.5 GB | 2GB+ | No | Minimal, runs anywhere |

Auto-selection prefers models already pulled on Ollama. Override with `--model gemma4:e4b`.

## Setup (New Machine)

```bash
# One-command setup: Python deps, Ollama, model, verification
./setup_env.sh

# Or step by step:
./setup_env.sh --deps-only    # Python deps only
./setup_env.sh --check        # Verify without installing
```

## Quick Start — Run Individual Steps

Run steps individually for more control:

```bash
# Step 1: Extract document content → data/extracted/
python -m core.src.extraction.extract *.pdf --output data/extracted

# Step 2: Create document profile → profiles/vzw_oa_profile.json
python -m core.src.profiler.profile_cli create \
    --name VZW_OA \
    --docs data/extracted/LTEDATARETRY_ir.json data/extracted/LTEB13NAC_ir.json \
    --output profiles/vzw_oa_profile.json

# Step 3: Parse all documents → data/parsed/
python -m core.src.parser.parse_cli \
    --profile profiles/vzw_oa_profile.json \
    --docs-dir data/extracted \
    --output-dir data/parsed

# Step 5: Resolve cross-references → data/resolved/
python -m core.src.resolver.resolve_cli \
    --trees-dir data/parsed \
    --output-dir data/resolved

# Step 6: Extract feature taxonomy → data/taxonomy/
python -m core.src.taxonomy.taxonomy_cli \
    --trees-dir data/parsed \
    --output-dir data/taxonomy

# Step 7: Ingest referenced 3GPP standards → data/standards/
# (downloads specs from 3GPP FTP — requires internet access)
python -m core.src.standards.standards_cli \
    --manifests-dir data/resolved \
    --trees-dir data/parsed \
    --output-dir data/standards

# Step 8: Build knowledge graph → data/graph/
python -m core.src.graph.graph_cli --verify

# Step 9: Build vector store → data/vectorstore/
python -m core.src.vectorstore.vectorstore_cli

# Step 10: Query the system
python -m core.src.query.query_cli --query "What is the T3402 timer behavior?"

# Step 11: Evaluate the pipeline
python -m core.src.eval.eval_cli                       # Run all 18 test questions
python -m core.src.eval.eval_cli --ab                  # A/B: graph-scoped vs pure RAG
python -m core.src.eval.eval_cli --output data/eval/report.json  # Save report
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
| `test_query.py` | 60 | Schema models, analyzer, resolver, graph scoper, RAG retriever, context builder (few-shot, reminder), synthesizer (citation fallback), pipeline orchestration, integration | `networkx` |
| `test_eval.py` | 36 | Question set structure, metric scoring, report serialization, A/B comparison, runner integration with synthetic graph | `networkx` |
| `test_web_path_mapper.py` | 19 | PathMapper Windows↔Linux translation, security checks, resolve, list_roots | None |
| `test_web_jobs.py` | 24 | JobQueue CRUD, status transitions, log streaming, cancel, cleanup, async | `aiosqlite` |
| **Total** | **426** | | |

## Step-by-Step Details

### Step 1 — Document Content Extraction

Extracts text, tables, and images from PDFs into a normalized intermediate representation (DocumentIR).

```bash
# Extract a single document
python -m core.src.extraction.extract LTEDATARETRY.pdf --output data/extracted

# Extract all PDFs in a directory
python -m core.src.extraction.extract /path/to/pdfs/ --output data/extracted
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
python -m core.src.profiler.profile_cli create \
    --name VZW_OA \
    --docs data/extracted/LTEDATARETRY_ir.json data/extracted/LTEB13NAC_ir.json \
    --output profiles/vzw_oa_profile.json

# Validate profile against a held-out document
python -m core.src.profiler.profile_cli validate \
    --profile profiles/vzw_oa_profile.json \
    --doc data/extracted/LTESMS_ir.json

# Update profile with additional docs
python -m core.src.profiler.profile_cli update \
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
python -m core.src.parser.parse_cli \
    --profile profiles/vzw_oa_profile.json \
    --doc data/extracted/LTEDATARETRY_ir.json \
    --output data/parsed/LTEDATARETRY_tree.json

# Parse all documents in a directory
python -m core.src.parser.parse_cli \
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
python -m core.src.resolver.resolve_cli \
    --trees-dir data/parsed \
    --output-dir data/resolved

# Resolve specific trees
python -m core.src.resolver.resolve_cli \
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
python -m core.src.taxonomy.taxonomy_cli \
    --trees-dir data/parsed \
    --output-dir data/taxonomy

# Verbose mode
python -m core.src.taxonomy.taxonomy_cli --trees-dir data/parsed -v
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
python -m core.src.standards.standards_cli \
    --manifests-dir data/resolved \
    --trees-dir data/parsed \
    --output-dir data/standards

# Collect references only (no download)
python -m core.src.standards.standards_cli --collect-only

# Process only specific specs
python -m core.src.standards.standards_cli --specs 24.301 36.331

# Skip download (use already-cached specs)
python -m core.src.standards.standards_cli --no-download

# Limit specs to process (useful for testing)
python -m core.src.standards.standards_cli --max-specs 3
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
python -m core.src.graph.graph_cli --verify

# Build graph without diagnostics
python -m core.src.graph.graph_cli
```

**Output:**
- `data/graph/knowledge_graph.json` — full graph (node-link JSON)
- `data/graph/graph_stats.json` — summary statistics

### Step 9 — Vector Store Construction

Creates embeddings for requirement chunks and stores them in a vector store with metadata for filtered retrieval. All settings (embedding provider, model, vector DB backend, distance metric) are configurable. Two embedding backends ship: `sentence-transformers` (HuggingFace, default) and `ollama` (via `/api/embeddings`); selection is via `VectorStoreConfig.embedding_provider` and `--embedding-provider` on the pipeline runner.

```bash
# Build with defaults (sentence-transformers + all-MiniLM-L6-v2, ChromaDB, cosine)
python -m core.src.vectorstore.vectorstore_cli

# Use a different sentence-transformers model
python -m core.src.vectorstore.vectorstore_cli --model all-mpnet-base-v2

# Use Ollama instead (must `ollama pull <model>` first; offline by construction)
python -m core.src.vectorstore.vectorstore_cli --provider ollama --model nomic-embed-text

# Use a config file for reproducible experiments
python -m core.src.vectorstore.vectorstore_cli --config configs/experiment1.json

# Override distance metric
python -m core.src.vectorstore.vectorstore_cli --metric l2

# Force rebuild (clear existing data)
python -m core.src.vectorstore.vectorstore_cli --rebuild

# Inspect existing store
python -m core.src.vectorstore.vectorstore_cli --info

# Test query against the store
python -m core.src.vectorstore.vectorstore_cli --query "T3402 timer behavior"
python -m core.src.vectorstore.vectorstore_cli --query "attach reject" --filter-plan LTEDATARETRY --n-results 5

# Save config alongside results for reproducibility
python -m core.src.vectorstore.vectorstore_cli --save-config configs/baseline.json
```

**Output:**
- `data/vectorstore/` — ChromaDB persistent data
- `data/vectorstore/build_stats.json` — build statistics
- `data/vectorstore/config.json` — config used for this build

**Configurable parameters (via JSON config or CLI flags):**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `embedding_model` | `all-MiniLM-L6-v2` | HuggingFace model name |
| `embedding_provider` | `sentence-transformers` | Embedding backend (`sentence-transformers`, `huggingface` (alias), or `ollama`) |
| `embedding_batch_size` | `64` | Batch size for encoding |
| `embedding_device` | `cpu` | Device (`cpu`, `cuda`, `mps`) — sentence-transformers only |
| `extra.ollama_url` | `http://localhost:11434` | Ollama HTTP endpoint (only used when `embedding_provider=ollama`) |
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
# Single query (mock synthesizer — fast, no LLM needed)
python -m core.src.query.query_cli --query "What is the T3402 timer behavior?"

# With real LLM via Ollama (requires ollama running + model pulled)
python -m core.src.query.query_cli --llm ollama --query "What is the T3402 timer behavior?"

# Specify a different Ollama model
python -m core.src.query.query_cli --llm ollama --llm-model gemma4:e2b --query "..."

# Increase timeout for slow CPU inference (default: 300s)
python -m core.src.query.query_cli --llm ollama --llm-timeout 600 --query "..."

# Verbose mode (shows all pipeline stages)
python -m core.src.query.query_cli --query "T3402 timer" --verbose

# Interactive mode
python -m core.src.query.query_cli --interactive

# Custom settings
python -m core.src.query.query_cli --query "..." --top-k 15 --max-depth 3

# Save response to JSON
python -m core.src.query.query_cli --query "..." --output response.json
```

**Output:** Query response with answer, citations, and pipeline statistics.

**Pipeline stages:**

| Stage | Module | Description |
|-------|--------|-------------|
| 1. Query Analysis | `analyzer.py` | Extracts entities, concepts, MNOs, features, plan IDs from natural language |
| 2. MNO/Release Resolution | `resolver.py` | Maps MNO names to available graph scopes (explicit, latest, or all) |
| 3. Graph Scoping | `graph_scope.py` | Traverses knowledge graph to find candidate requirement nodes |
| 4. Targeted RAG | `rag_retriever.py` | Retrieves relevant chunks from vector store, scoped by graph candidates |
| 5. Context Assembly | `context_builder.py` | Enriches chunks with graph context (hierarchy, standards, cross-refs); few-shot citation example in system prompt |
| 6. LLM Synthesis | `synthesizer.py` | Generates answer with citations from assembled context; context-based citation fallback for small models |

**Graph scoping strategies:** Entity lookup (req IDs, timers), feature lookup (maps_to edges), plan lookup (all reqs in plan), title search (text matching). BFS edge traversal with configurable depth and score decay (0.7^depth).

**RAG retrieval:** Two strategies — scoped retrieval (filter vector store by graph candidate req_ids) and metadata retrieval (filter by MNO/release when no graph candidates). Diversity enforcement ensures minimum chunks per plan.

**Retrieval enhancements:** The retriever runs more than vector similarity:
- **BM25 hybrid (D-041)** — sparse BM25 over a telecom-aware tokenized index, fused with dense via Reciprocal Rank Fusion. Per-query-type weight (`_TYPE_BM25_WEIGHT`) — STANDARDS_COMPARISON / TRACEABILITY / SINGLE_DOC = 0.5, CROSS_DOC / FEATURE_LEVEL = 0.0.
- **Per-type top_k (D-040)** — list/breadth queries widen retrieval to 25; lookups stay at 10.
- **Query rewriting** — optional pre-retrieval LLM expansion (3 paraphrases concatenated for embedding+BM25); on for concept-shaped types, off for SINGLE_DOC where D-039 entity priority handles it.
- **Cross-encoder reranker** — optional reorder of the post-fusion pool with a small cross-encoder; degrades to passthrough when the model isn't cached. Default model: `cross-encoder/ms-marco-MiniLM-L6-v2`.
- **Acronym expansion in chunks (D-032 / D-038)** — first occurrence of every known acronym is inline-expanded `SDM → SDM (Subscriber Device Management)` at chunk-build time so queries that use either form match.
- **Glossary lookup chain (D-043)** — definitional queries ("What is X?", "Define X", "Meaning of X", …) hard-pin the matching glossary chunk to the top of retrieval. Three layers: parser recovers misclassified table-header rows so `definitions_map` is complete; chunk_builder emits one short chunk per acronym; retriever detects the query pattern and prepends.

See [`core/src/query/RETRIEVAL.md`](core/src/query/RETRIEVAL.md) for the end-to-end retrieval design including per-query-type policy maps, the glossary lookup architecture, and ADR cross-references.

**Mock implementations:** `MockQueryAnalyzer` uses keyword matching and regex patterns. `MockSynthesizer` returns structured summaries grouping results by plan with req IDs and standards references. Both exercise the full pipeline without requiring LLM API keys.

### Step 11 — Evaluation

Evaluates the query pipeline on 18 test questions across 5 categories, measuring completeness, accuracy, citation quality, standards integration, and hallucination avoidance. Supports A/B comparison between graph-scoped and pure RAG retrieval.

```bash
# Run all 18 evaluation questions (mock synthesizer — fast)
python -m core.src.eval.eval_cli

# Run with real LLM via Ollama
python -m core.src.eval.eval_cli --llm ollama

# Run A/B comparison (graph-scoped vs pure RAG)
python -m core.src.eval.eval_cli --ab
python -m core.src.eval.eval_cli --ab --llm ollama   # with real LLM

# Run a specific category only
python -m core.src.eval.eval_cli --category cross_doc

# Save report to JSON
python -m core.src.eval.eval_cli --output data/eval/report.json

# Verbose mode (shows pipeline stage details)
python -m core.src.eval.eval_cli --verbose
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

## LLM and Embedding Providers

NORA decouples model selection from code: both the LLM and the embedding model are picked at runtime through the same precedence chain — **CLI flag > env var > environment-config file > built-in default**.

### LLM providers

| Provider | Where it runs | When to use |
|---|---|---|
| `ollama` (default) | Local Ollama runtime | Work PC; air-gapped or proxy-restricted environments |
| `openai-compatible` | Cloud (OpenRouter, Together, DeepInfra, Groq, OpenAI) | Personal PC; access to a strong frontier model |
| `mock` | In-process | Tests; deterministic keyword responses |

CLI:
```bash
# Local Ollama (auto-pick a model that fits the hardware)
python -m core.src.pipeline.run_cli --env-dir ~/env-vzw \
    --llm-provider ollama --model auto

# Cloud via OpenRouter
export NORA_LLM_BASE_URL=https://openrouter.ai/api/v1
export NORA_LLM_API_KEY=sk-or-...
python -m core.src.pipeline.run_cli --env-dir ~/env-vzw \
    --llm-provider openai-compatible --model anthropic/claude-haiku
```

Env vars: `NORA_LLM_PROVIDER`, `NORA_LLM_MODEL`, `NORA_LLM_BASE_URL`, `NORA_LLM_API_KEY`. All three providers satisfy the `LLMProvider` Protocol — see `core/src/llm/base.py`. Custom providers only need a `complete(prompt, system, temperature, max_tokens) -> str` method.

### Embedding providers (local-only in v1)

| Provider | Aliases | Where it runs | When to use |
|---|---|---|---|
| `sentence-transformers` (default) | `huggingface`, `hf`, `st` | Local, HuggingFace cache (`~/.cache/huggingface`) | Personal PC where the HF cache is already populated |
| `ollama` | — | Local Ollama runtime | Work PC where Ollama is already set up — skips the HF cache entirely |

CLI:
```bash
# HuggingFace sentence-transformers (default)
python -m core.src.pipeline.run_cli --env-dir ~/env-vzw \
    --embedding-provider huggingface --embedding-model all-MiniLM-L6-v2

# Ollama embeddings (must `ollama pull <model>` first)
python -m core.src.pipeline.run_cli --env-dir ~/env-vzw \
    --embedding-provider ollama --embedding-model nomic-embed-text
```

Env vars: `NORA_EMBEDDING_PROVIDER`, `NORA_EMBEDDING_MODEL`. Recommended Ollama embedding models: `nomic-embed-text` (768d, ~270 MB), `mxbai-embed-large` (1024d, ~670 MB), `all-minilm` (384d, ~45 MB).

### Two-PC workflow

Capture per-machine choices in `environments/<name>.json` and select with `--env`:

```json
// environments/personal.json — OpenRouter LLM, local HF embeddings
{ "model_provider": "openai-compatible", "model_name": "anthropic/claude-haiku",
  "embedding_provider": "huggingface", "embedding_model": "all-MiniLM-L6-v2", ... }

// environments/work.json — local Ollama for both
{ "model_provider": "ollama", "model_name": "auto",
  "embedding_provider": "ollama", "embedding_model": "nomic-embed-text", ... }
```

## Tuning

The system exposes tunable parameters at four layers. Most live in
JSON config files that ride alongside their stage's outputs (so a
re-run from that stage onwards picks them up); a few are Python
constants that take effect on the next process start.

### 1. Document profile — corpus-specific structural rules

**Location**: `<env_dir>/corrections/profile.json` (per-environment override; takes precedence over `<env_dir>/out/profile/profile.json` which the profile stage auto-generates).

**When to tune**: when the parser misclassifies headings, drops legitimate content, or retains content the source struck through. This is the primary correction surface for new MNO corpora.

**Re-run**: `python -m core.src.pipeline.run_cli --env-dir <env> --start profile` (the profile stage copies your override to `out/`; downstream stages then pick it up).

| Field | Default | What it does |
|---|---|---|
| `heading_detection.numbering_pattern` | `^(?:(\d+)(?=\s)|(\d+(?:\.\d+)+)(?=\s|[A-Z]))` | Section-number regex. Multi-dot variant accepts whitespace OR uppercase letter as the next char (matches OA's `1.2.3.4Title` no-space convention). [D-033] |
| `heading_detection.priority_marker_pattern` | `""` | Optional regex to extract a priority marker from heading text (FR-31). Empty disables. |
| `heading_detection.definitions_section_pattern` | `(?i)acronym|definition|glossary` | Title pattern to identify the glossary section. (FR-35 [D-032]) |
| `requirement_id.pattern` | corpus-derived | Req-id regex; profile-driven so adding a new MNO needs no parser change. |
| `toc_detection_pattern` | `.*\.{3,}\s*\d+\s*$` | TOC entry regex (leader-dot-page-number suffix). Empty disables TOC drop. (FR-34) |
| `toc_page_threshold` | `0.7` | Fraction of paragraph blocks on a page that must match the TOC pattern before the whole page is dropped. `1.0` disables page-level drop, keeps line-level. |
| `revision_history_heading_pattern` | `(?i)^\s*(revision|change|version|document)\s+(history|log)\s*$` | Revhist heading regex; profiler narrows to corpus-observed phrasing. Drops the heading + all subsequent table/image blocks until the next paragraph. (FR-34 [D-035]) |
| `definitions_entry_pattern` | `^([A-Z][A-Z0-9/-]{1,15})\s*[—–:\-]\s*(.+?)$` | Per-line term→expansion regex for body-text glossaries. Empty disables. Table-anchored definitions are extracted regardless. [D-038] |
| `ignore_strikeout` | `True` | Drop content marked struck-through. Flip to `False` for corpora that abuse strikethrough as emphasis. (FR-33 [D-031]) |
| `enable_table_anchored_extraction` | `True` | Allow req_ids found only in table cells to become Requirements. Set `False` for paragraph-only-requirement corpora (e.g., VZW OA) to avoid phantom duplicates. [D-027, D-034] |

**Profile stage detects the patterns automatically from your corpus**; you typically only edit `corrections/profile.json` after seeing parse errors in `<env_dir>/reports/audit/<doc>_audit.csv`.

### 2. Vectorstore — chunk content + embedding

**Location**: `<env_dir>/out/vectorstore/config.json` (auto-saved by the vectorstore stage; edit and re-run the stage).

**When to tune**: chunk-text composition (what's prepended to chunk text before embedding) directly affects retrieval recall — adding hierarchy paths and req-ids materially helps lookup queries; the children-titles augmentation is corpus-dependent.

**Re-run**: `--start vectorstore --end eval` (rebuilds the index, then evaluates).

| Field | Default | What it does |
|---|---|---|
| `embedding_provider` | `sentence-transformers` | Or `ollama`, `huggingface` (alias). [D-029] |
| `embedding_model` | `all-MiniLM-L6-v2` | Provider-specific. Ollama default is `qwen3-embedding-q8-0:4b`. |
| `embedding_batch_size` | `64` | Per-call batch for embedding. |
| `distance_metric` | `cosine` | Or `l2`, `ip`. Pair with `normalize_embeddings=True` for cosine. |
| `include_mno_header` | `True` | Prepend `[MNO: X \| Release: Y \| Plan: Z \| Version: V]`. |
| `include_hierarchy_path` | `True` | Prepend `[Path: A > B > C]`. Big retrieval-recall win for path-style queries. |
| `include_req_id` | `True` | Prepend `[Req ID: ...]`. Required for entity-priority lookup. [D-039] |
| `include_tables` | `True` | Append tables as Markdown inside the chunk text. |
| `include_image_context` | `True` | Append `[Image: <caption>]` for inline figures. |
| `include_children_titles` | `False` | Append `[Subsections: t1; t2; ...]` to thin parent chunks. **Off by default** — empirical tuning showed +8pp single_doc accuracy at the cost of -10pp cross_doc on the OA corpus. Worth enabling on rich-bodied-parent corpora. |
| `children_titles_body_threshold` | `300` | Body-length gate for the above (in characters). Only parents with body shorter than this get augmented. |
| `max_children_titles` | `3` | Cap on the subsection list; overflow gets `(+N more)` appended. |

### 3. Query pipeline — retrieval breadth + hybrid fusion

**Location**: `core/src/query/pipeline.py` (Python constants — `_TYPE_TOP_K`, `_TYPE_BM25_WEIGHT`) and `core/src/query/rag_retriever.py` (`_DENSE_WEIGHT`, `_DEFAULT_BM25_WEIGHT`, `_HYBRID_FANOUT_MULT`).

**When to tune**: when retrieval consistently misses specific kinds of queries (lookup vs breadth), or when the BM25/dense balance needs adjustment for a new corpus.

**Re-run**: `--start eval --end eval` (no rebuild needed — these are runtime constants).

| Knob | Default | What it does |
|---|---|---|
| `_TYPE_TOP_K[QueryType]` | `{CROSS_DOC: 25, FEATURE_LEVEL: 25, STANDARDS_COMPARISON: 25, CROSS_MNO_COMPARISON: 25, TRACEABILITY: 20, RELEASE_DIFF: 20}` | Per-query-type top_k. Lookup queries (`SINGLE_DOC`) fall through to the constructor `top_k`. List/breadth queries widen because expected hits include parent/overview chunks that rank below richer leaves. [D-040] |
| `_TYPE_BM25_WEIGHT[QueryType]` | `{STANDARDS_COMPARISON: 0.5, TRACEABILITY: 0.5, SINGLE_DOC: 0.5}` | Per-query-type BM25 weight in RRF. Missing types default to `0.0` (pure dense) — empirically `CROSS_DOC` and `FEATURE_LEVEL` regress when BM25 is added because parent chunks are token-thin. [D-040] |
| `_DENSE_WEIGHT` | `1.0` | RRF weight for the dense side. Always 1.0; BM25 is the variable counterpart. |
| `_HYBRID_FANOUT_MULT` | `3` | Per-side fanout: each retriever pulls `top_k * 3` candidates before RRF fusion. Larger gives RRF more material to fuse; smaller cuts retrieval cost. |
| `QueryPipeline(top_k=...)` | `10` | Floor that the per-type map clips upward from. |
| `QueryPipeline(max_depth=...)` | per-type via `_DEFAULT_DEPTH` | Graph traversal depth from seed nodes. |
| `QueryPipeline(max_context_chars=...)` | `30000` | LLM context window cap; chunks are truncated highest-score-first. |
| `QueryPipeline(enable_bm25=...)` | `True` | Hard-disable BM25 hybrid retrieval (for perf-sensitive deploys or A/B tests). |
| `RAGRetriever(diversity_min_per_plan=...)` | `1` | Minimum chunks per contributing plan before filling top-k from the ranked list. |

### 4. Environment / runtime — provider selection

**Location**: env vars or CLI flags. Precedence: **CLI > env var > config file > code default**.

**When to tune**: switching LLM/embedding backends across machines or environments (cloud LLM on dev box, local Ollama on work laptop, mock for offline tests).

| Variable / flag | Equivalent CLI flag | Purpose |
|---|---|---|
| `NORA_LLM_PROVIDER` | `--llm-provider` | `ollama` (default), `openai-compatible`, `mock` |
| `NORA_LLM_MODEL` | `--llm-model` | Model tag (e.g. `gemma4:e4b`, `qwen/qwen3-235b-a22b`). `auto` picks Ollama tag from detected hardware. |
| `NORA_LLM_BASE_URL` | — | OpenAI-compatible endpoint base (e.g. OpenRouter, Together, Groq). |
| `NORA_LLM_API_KEY` | — | OpenAI-compatible API key. |
| `NORA_LLM_TIMEOUT` | — | Per-call timeout (seconds; default 300). |
| `NORA_EMBEDDING_PROVIDER` | `--embedding-provider` | `sentence-transformers` (default), `ollama`. Aliases: `huggingface`, `hf`, `st`. |
| `NORA_EMBEDDING_MODEL` | `--embedding-model` | Model name; provider-specific defaults if unset. |
| `NORA_STANDARDS_SOURCE` | `--standards-source` | `huggingface` (default, DOCX-only) or `3gpp` (FTP, full coverage but heavier). [D-025] |
| `NORA_DOC_ROOT` (legacy) | `--env-dir` | Per-environment runtime directory containing `input/`, `out/`, `state/`, `corrections/`, `reports/`, `eval/`. [D-022, D-023] |

### Where to start tuning

| Symptom | First knob to try |
|---|---|
| Parser misclassifies headings on a new corpus | `numbering_pattern` and audit CSV at `<env_dir>/reports/audit/` |
| Specific reqs missing from retrieval (lookup) | `_TYPE_BM25_WEIGHT` for the query type, or chunk content flags (`include_hierarchy_path`, `include_req_id`) |
| Breadth queries miss expected reqs | Widen `_TYPE_TOP_K` for the type; consider `include_children_titles` if many parents are heading-only |
| Specific terms (TS numbers, codes) not surfacing | Confirm BM25 is enabled for that query type (`_TYPE_BM25_WEIGHT > 0`) |
| Eval citation_quality drop after model switch | LLM-side; tune the synthesizer prompt or rubric, not the retrieval knobs |
| Standards stage failing on specific specs | Try `--standards-source 3gpp` for fallback; check `STD-E002` in compact report |
