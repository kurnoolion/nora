# Contributing Guide

This guide is for team members working on verifying and improving the NORA (Network Operator Requirements Analyzer) pipeline.

## Workflow Overview

```
Admin creates environment → Member runs pipeline → Reviews artifacts
→ Makes corrections → Re-runs → Reports results (compact format)
```

There are two ways to interact with NORA: the **Web UI** (recommended for most team members) and the **CLI** (for advanced users).

## Getting Started — Web UI (Recommended)

The web UI provides browser-based access to all pipeline features. No terminal experience required.

1. Open NORA in your browser: `http://<server>:<port>` (or the URL provided by your admin, e.g., `https://yourserver.com/nora`)

2. **Create/view your environment:**
   - Go to **Environments** in the sidebar
   - If your environment doesn't exist yet, ask your admin to create one, or click **New Environment**

3. **Run the pipeline:**
   - Go to **Pipeline** in the sidebar
   - Enter your document path (you can paste a Windows path like `\\SERVER\OADocs\alice\documents`)
   - Select which stages to run and which model to use
   - Click **Submit** — you'll be redirected to the job monitoring page

4. **Monitor your job:**
   - Go to **Jobs** in the sidebar to see all jobs
   - Click on your job to see real-time logs streaming as the pipeline runs
   - Progress bar shows completion percentage

5. **Query the knowledge graph:**
   - Go to **Query** in the sidebar
   - Type your question (e.g., "What is the T3402 timer behavior?")
   - Results include answer, citations, and pipeline stats

6. **Make corrections:**
   - Go to **Corrections** in the sidebar — one row per environment, with status badges (no output / output only / corrected).
   - Click **Edit profile** or **Edit taxonomy**, then **Start correction from output** to seed a copy from the generated artifact.
   - Edit inline: heading/req-ID patterns, zones, header/footer, body-text thresholds (profile); search and add/rename/remove features, edit keywords (taxonomy).
   - Click **Save correction** — file lands at `<doc_root>/corrections/profile.json` or `taxonomy.json` and the pipeline picks it up on the next run.
   - Click **FIX report** to get a compact, proprietary-content-free summary of your changes to paste into chat.

7. **Browse shared files:**
   - Go to **Files** in the sidebar
   - Browse the shared network folders to find documents or pipeline outputs

8. **Check system health:**
   - The **Dashboard** shows Ollama status, GPU info, and recent jobs
   - The **Metrics** page shows request timing, LLM stats, and resource usage

## Getting Started — CLI

For advanced users who prefer the terminal:

1. Clone the repo and run setup:
   ```bash
   git clone <repo-url>
   cd req-agent
   ./setup_env.sh
   ```

   If you're in a proxy-restricted environment where `curl` HTTPS fails:
   ```bash
   # Download files listed in download_urls.txt on another machine
   # Transfer them to a directory, then:
   ./setup_env.sh --download-dir /path/to/downloads
   ```

2. Ask the admin for your environment name, then check it:
   ```bash
   python -m src.env.env_cli show <your-env-name>
   ```

3. Initialize your workspace:
   ```bash
   python -m src.env.env_cli init <your-env-name>
   ```

4. Place your documents in `<document_root>/documents/`

5. Run the pipeline:
   ```bash
   python -m src.pipeline.run_cli --env <your-env-name>
   ```

## File Ownership

### Auto-generated (DO NOT edit directly)
These files are produced by the pipeline. Edits will be overwritten on the next run.

| Directory | Contents |
|---|---|
| `output/extracted/` | Document IR JSON files |
| `output/parsed/` | Requirement tree JSON files |
| `output/resolved/` | Cross-reference manifests |
| `output/taxonomy/` | Feature extraction (per-doc + unified) |
| `output/standards/` | Downloaded 3GPP specs + parsed sections |
| `output/graph/` | Knowledge graph JSON |
| `output/vectorstore/` | ChromaDB + embeddings |
| `output/eval/` | Evaluation reports |
| `reports/` | Pipeline run reports |

### Human-editable (your contribution goes here)

| File/Directory | What to put here |
|---|---|
| `documents/` | Source documents (PDF, DOCX, XLS, XLSX) |
| `corrections/profile.json` | Corrected document profile (copy from `output/profile/`, fix, place here) |
| `corrections/taxonomy.json` | Corrected taxonomy (copy from `output/taxonomy/taxonomy.json`, fix, place here) |
| `eval/*.xlsx` | Your Q&A evaluation pairs (see format below) |

## Correction Workflow

The web UI is the recommended way to make corrections. Both UI and CLI read/write the same files (`<doc_root>/corrections/profile.json` and `<doc_root>/corrections/taxonomy.json`), so you can mix the two.

### Correcting a Document Profile (UI)

1. Run the pipeline through the `profile` stage
2. Go to **Corrections → Edit profile** for your environment
3. Click **Start correction from output** to seed a copy from the generated profile
4. Edit heading numbering, req ID pattern + components, header/footer patterns, zones, cross-refs, body-text thresholds, and click **Save correction**
5. Re-run the pipeline — it will use your corrected profile automatically
6. Click **FIX report** and paste the compact block into chat (see Reporting Format below)

### Correcting the Taxonomy (UI)

1. Run the pipeline through the `taxonomy` stage
2. Go to **Corrections → Edit taxonomy** for your environment
3. Click **Start correction from output** to seed a copy from the generated taxonomy
4. Use the search box to filter features, then rename, remove, add, or edit keywords inline
5. Click **Save correction** — re-run the pipeline from the `graph` stage onward
6. Click **FIX report** and paste the compact block into chat

### Corrections via CLI (fallback)

If you prefer to edit JSON directly:

1. Copy the generated file:
   - `cp <doc_root>/output/profile/*.json <doc_root>/corrections/profile.json`
   - `cp <doc_root>/output/taxonomy/taxonomy.json <doc_root>/corrections/taxonomy.json`
2. Edit the copy with any text editor
3. Re-run the pipeline — corrections are auto-detected

To get a compact FIX report without the UI:
```bash
curl http://<server>:<port>/api/corrections/report/<env-name>
# Optional: ?artifact=profile or ?artifact=taxonomy
```

## Evaluation Q&A Format (Excel)

Create an `.xlsx` file in `eval/` with these columns:

| Column | Required | Description | Example |
|---|---|---|---|
| `question_id` | Yes | Unique ID | `Q_CUSTOM_01` |
| `category` | Yes | One of: `single_doc`, `cross_doc`, `feature_level`, `standards_comparison`, `traceability` | `cross_doc` |
| `question` | Yes | The question text | `What are the T3402 timer requirements?` |
| `expected_plans` | Yes | Comma-separated plan IDs | `LTEDATARETRY,LTEB13NAC` |
| `expected_req_ids` | No | Comma-separated req IDs | `VZ_REQ_LTEDATARETRY_7748` |
| `expected_features` | No | Comma-separated features | `DATA_RETRY,LTE_ATTACH` |
| `expected_standards` | No | Comma-separated specs | `3GPP TS 24.301` |
| `expected_concepts` | No | Comma-separated concepts | `T3402,timer,retry` |
| `min_plans` | No | Min plans expected (default: 1) | `2` |
| `min_chunks` | No | Min chunks expected (default: 1) | `3` |

## Reporting Results

After running the pipeline, report results using these compact formats.

### Pipeline Report
The pipeline outputs a compact report at the end of each run. Copy the block between the `───` lines and paste it.

Example:
```
RPT profiler-review 2026-04-15T14:30
HW CPU=Intel Ultra 9(22c) RAM=32G GPU=RTX4060(16G)
MDL gemma4:e4b
EXT OK 12s docs=8 blk=4521 tbl=234
PRF OK 3s lvl=4 rpat=2 zone=3
PRS OK 9s req=890 dep=7 docs=8
ERR none
```

### Quality Check (type this after reviewing artifacts)

**Profile QC:**
```
QC my-env profile
lvl=Y rpat=Y zone=Y body=Y hf=Y
miss: none
notes: heading level 3 threshold too high
```

**Taxonomy QC:**
```
QC my-env taxonomy
feat=16 correct=14 wrong=1 miss=3
wrong: IMS_REGISTRATION(appears in all docs, mock artifact)
miss: VOLTE_HANDOVER, SMS_RETRY, ATTACH_GUARD
notes: overall good except mock artifacts
```

**Eval QC:**
```
QC my-env eval
q=18 pass=14 fail=4
fail: Q_CD_01(missing LTEB13NAC), Q_STD_02(wrong spec version)
notes: cross-doc questions weakest
```

### Correction Feedback (type this after making corrections)

**Profile fix:**
```
FIX my-env profile
heading_threshold: 14.0 -> 13.0
req_pattern: added "ATT_REQ_\w+"
notes: B13 doc has different heading sizes
```

**Taxonomy fix:**
```
FIX my-env taxonomy
added=3 removed=1 renamed=2
add: VOLTE_HANDOVER(keywords: handover,ho,mobility)
remove: GENERIC_ATTACH
rename: DATA_RETRY->LTE_DATA_RETRY, SMS->LTE_SMS
notes: removed mock artifacts, added real features
```

## Show Templates

To see the QC or correction template for any stage:
```bash
python -m src.pipeline.run_cli --qc-template profile
python -m src.pipeline.run_cli --qc-template taxonomy
python -m src.pipeline.run_cli --fix-template taxonomy
```

## Environment Directory Structure

```
<document_root>/
├── documents/        # YOUR source documents go here
├── corrections/      # YOUR corrected artifacts go here
│   ├── profile.json  # Corrected document profile (optional)
│   └── taxonomy.json # Corrected taxonomy (optional)
├── eval/             # YOUR Q&A eval pairs go here
│   └── questions.xlsx
├── output/           # Auto-generated pipeline outputs
│   ├── extract/
│   ├── profile/
│   ├── parse/
│   ├── resolve/
│   ├── taxonomy/
│   ├── standards/
│   ├── graph/
│   ├── vectorstore/
│   └── eval/
└── reports/          # Auto-generated pipeline reports
```

## Common Commands (CLI)

```bash
# List available stages
python -m src.pipeline.run_cli --list-stages

# Run specific stages
python -m src.pipeline.run_cli --env my-env --start extract --end parse

# Re-run from taxonomy with corrections
python -m src.pipeline.run_cli --env my-env --start graph --end eval

# Check hardware and model recommendation
python -m src.pipeline.run_cli --detect-hw

# Show environment details
python -m src.env.env_cli show my-env

# List all environments
python -m src.env.env_cli list

# Start the web UI server
python -m src.web.app
```

## Web UI Admin Tasks

The admin can configure the web UI via `web/config.json`:

```json
{
    "host": "0.0.0.0",
    "port": 8000,
    "root_path": "/nora",
    "ollama_url": "http://localhost:11434",
    "default_model": "gemma3:12b",
    "path_mappings": [
        {
            "windows": "\\\\SERVER\\OADocs",
            "linux": "/mnt/oa_docs",
            "label": "OA Documents"
        }
    ]
}
```

**Path mappings** allow team members to use Windows network paths (e.g., `\\SERVER\OADocs\alice\`) in forms — the server translates them to Linux paths automatically.

**Reverse proxy:** Set `root_path` to your proxy prefix (e.g., `/nora`). All UI links will be prefixed correctly.
