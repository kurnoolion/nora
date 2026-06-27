# sandbox/

Scratch area for the `sira` strand (and similar future experiments).
**Not under `core/src/`** — the curated NORA module surface stays
unchanged by what lives here. Built artifacts and cloned upstream
repos are gitignored; only the glue code we write is committed.

> **Want to run SIRA?** See [`SETUP.md`](SETUP.md) for the step-by-step
> install + verify procedure (prereqs, clone, env, configs, smoke
> tests, troubleshooting). This README is the layout reference + Phase 0
> findings.

## Layout

| Path | What | Versioned |
|---|---|---|
| `sira/` | Cloned `facebookresearch/sira` (`--depth 1`). Pull fresh with `git -C sandbox/sira pull` to track upstream. | ❌ gitignored |
| `shim/openai_shim.py` | FastAPI service that exposes `/v1/chat/completions` and routes onto `customizations/llm/proprietary_provider.py`. **Optional** after the per-stage-routing patch — only needed when the LLM endpoint requires header injection (custom auth), model-name rewriting, or non-OpenAI adapter mode. The recommended path now routes SIRA directly via env vars (see `sira_patches/README.md`). | ✅ |
| `sira_patches/per-stage-routing.patch` | Adds `NORA_SIRA_{ENRICH,RERANK}_LLM_{URL,MODEL}` env vars to SIRA's batch pipeline, letting enrichment and rerank stages target different OpenAI-compatible endpoints without running the shim. Applied idempotently by `install_configs.sh`. See `sira_patches/README.md` for env-var reference and usage examples. | ✅ |
| `sira_patches/test/probe_per_stage_endpoints.py` | Stdlib sanity probe — POSTs a tiny chat-completion to each configured endpoint and reports compact `SPK` lines. Use before running the pipeline to confirm reachability + response shape. | ✅ |
| `adapter/nora_to_beir.py` | Converts NORA parse output (`<env_dir>/out/parse/**/*_tree.json` — recursive, covers the per-cell `<mno>/<rel>/` layout, NORA D-DRAFT-12) + the 18-Q eval set into BEIR-format `corpus.jsonl` + `queries.jsonl` + `qrels/test.tsv`. `--multi-cell` emits one dataset per `(mno, release)` cell. | ✅ |
| `sira_multi.py` | Multi-MNO batch orchestrator — enumerates the `(mno, release)` cells under a db_root and runs SIRA's `run_pipeline.py` once per cell (writes each cell's `configs/data/<cell>.yaml` from the `nora.yaml` template first). See "Running — multi-MNO" below. | ✅ |
| `sira_query/service.py` | Cell-aware FastAPI per-query service (`/sira-query`, `/healthz`) — loads every cell at startup; resolves query scope → retrieve per cell → merge → rerank. Query-time config in the multi-mno-sira runbook. | ✅ |
| `sira_cells.py` | Cell-identity helpers (`cell_dirname`, `parse_cell_dirname`, `enumerate_cells`); release ordering re-exported from `core.extraction.release_key`. | ✅ |
| `sira_enrich_inspect.py` | Doc-enrichment inspector CLI — prints the phrases SIRA attached to a given `req_id` (from `best.jsonl` + the latest run's `enrichments.kept.jsonl`), multi-cell, with `--text` / `--trace`. | ✅ |
| `verify_tables.py` | Verifies parsed tables are inlined into NORA parse text (fails on any `tables`-field req missing its inline table) and reached the per-cell SIRA corpus, with a cross-check. `--parse` and/or `--db-root`. | ✅ |
| `run_stack.sh` | Launch one isolated SIRA-service + NORA-web stack (Path-B) from args — own ports, own LLM, own web state DBs — so several can run in parallel to A/B different LLMs / ingestions. `--dry-run` to preview, `--stop <label>` to tear down. | ✅ |
| `sira_incremental.py` | Content-hash resume helper (`prune` / `commit` / `promote` / `retry-failed`) so a re-parse that changes a doc's text re-enriches it instead of being wrongly skipped by SIRA's doc_id resume. | ✅ |
| `prompts/doc_requirement_v01.txt` | Telecom-tuned doc-enrichment prompt (replaces SIRA's Wikipedia-tuned `doc_v07.txt`). | ✅ |
| `prompts/query_requirement_v01.txt` | Mirror query-enrichment prompt. | ✅ |
| `prompts/relevance_requirement_v01.txt` | LLM-reranker prompt. | ✅ |
| `sira_configs/data/nora.yaml` | SIRA hydra dataset config — `name=nora`, no HF fetch (adapter writes `metadata.json`, prepare-stage skips download). | ✅ |
| `sira_configs/enrich/nora.yaml` | Enrichment overrides — references `doc_requirement_v01.txt` / `query_requirement_v01.txt`. | ✅ |
| `sira_configs/rerank/nora.yaml` | Reranker overrides — references `relevance_requirement_v01.txt`. | ✅ |
| `install_configs.sh` | One-command installer that copies the 3 hydra configs + 3 prompts into the (gitignored) `sira/` clone's `scripts/configs/` tree **and applies patches under `sira_patches/`** (sentinel-grep idempotency). Re-run after editing any of those source files or after a fresh SIRA clone. | ✅ |

## Phase 0 findings (2026-05-16)

1. **LLM call shape**: SIRA hits `http://127.0.0.1:{port}/v1/chat/completions` with a standard OpenAI Chat Completions payload (`model`, `messages`, `max_tokens`, `temperature`, optional `seed` / `chat_template_kwargs`). No sglang-specific schema-constrained generation. → For deployments where the LLM is reachable directly, the per-stage routing patch (`sira_patches/per-stage-routing.patch`) is the recommended path: env vars override SIRA's URL/model per stage, no shim required. The shim remains available for header-injection / model-rewrite / non-OpenAI-adapter use cases.
2. **Generic prompts are Wikipedia-tuned**: `doc_v07.txt` examples are "Nicole Gale Anderson", "Tyrion 'The Imp'", "ice giant planet". `query_v07.txt` examples are "FIFA, football, host nation" for a World Cup query. `relevance_v04.txt` examples are "the Neptune Wikipedia article". Direct application to MNO device requirements would produce poor enrichment. → Telecom variants drafted as `v01`; iterate against real corpus output.
3. **Dependency stack is GPU-pinned**: `torch==2.9.1` + CUDA-13 indexes, `sglang==0.5.10.post1`, `flash-attn-4[cu13]`, `flashinfer-jit-cache`. Won't install on the no-GPU dev PC. **The scifact smoke run is blocked on a GPU env** (work laptop RTX A4600 or DGX Spark when up).
4. `bm25x` Rust crate has CPU mode (`default = []`, `cuda = ["dep:cudarc"]`), but the top-level `pyproject.toml`'s torch / sglang pins force a GPU install via `pip install -e .`.

## Phase 0 status

Done on dev PC (no GPU required):
- ✅ Repo cloned + inspected
- ✅ Generic prompts read; telecom variants drafted (`v01`)
- ✅ FastAPI shim built; smoke-tested with the stub provider (returns 501 as expected)
- ✅ Adapter built; smoke-tested with a synthetic `_tree.json`. Emits SIRA-internal `raw/` layout (corpus.jsonl, queries-test.jsonl, qrels-test.jsonl, metadata.json) so `prepare_mteb_data.py` early-returns and no HF download is triggered
- ✅ Three hydra configs (`data/nora.yaml`, `enrich/nora.yaml`, `rerank/nora.yaml`) + `install_configs.sh` to copy them + the prompts into the gitignored SIRA clone

Blocked until GPU env:
- ⏸ Run SIRA's `scifact` example end-to-end against the shim (to validate the shim under SIRA's actual request volume + the LLM-pipeline's enrichment behavior)
- ⏸ Run SIRA against the NORA-converted corpus (Phase 1 proper)

## Running

The recommended path uses **per-stage env-var routing** — no shim, no
local sglang. Shim is the fallback for header-injection / model-rewrite
cases (see `sira_patches/README.md` for when each applies).

**Step 1 — Adapter** (writes the SIRA-internal `raw/` layout):

    python -m sandbox.adapter.nora_to_beir \
        --env-dir /path/to/env_dir \
        --output sandbox/adapter/out/nora

**Step 2 — Install configs + patches into the SIRA clone**:

    bash sandbox/install_configs.sh

(Idempotent. Copies the 3 hydra configs + 3 prompts; applies any
patches under `sira_patches/`. Re-run after edits or fresh SIRA clones.)

**Step 3 — Configure LLM endpoints via env vars**:

    # Enrichment endpoint (corpus + query both use these vars)
    export NORA_SIRA_ENRICH_LLM_URL=http://<your-llm-host>:<port>/v1
    export NORA_SIRA_ENRICH_LLM_MODEL=<your-model-name>

    # Rerank endpoint (set to same as enrichment OR a faster local LLM)
    export NORA_SIRA_RERANK_LLM_URL=http://<your-llm-host>:<port>/v1
    export NORA_SIRA_RERANK_LLM_MODEL=<your-model-name>

    # Sanity probe before launching the pipeline
    python -m sandbox.sira_patches.test.probe_per_stage_endpoints

Expect two `SPK ... OK ...` lines (one per stage) before continuing.

**Step 4 — Run SIRA against NORA** (assumes SIRA's env is set up per
its own README and the bm25x crate is built):

    cd $REPO_ROOT
    source sandbox/activate.sh
    cd sandbox/sira
    python scripts/run_pipeline.py \
        data=nora \
        enrich=nora \
        rerank=nora \
        db_root=$(realpath ../adapter/out)

With the env vars set, the patched `run_pipeline.py` skips its
localhost-sglang reachability check and spawn — see the log line
`SIRA LLM stages routed via NORA_SIRA_*_LLM_URL env vars — skipping
localhost sglang reachability check and spawn.` Each stage also logs
which URL it resolved (`(env-routed via NORA_SIRA_*_LLM_URL)` vs.
`(sglang config)`).

> **Shim fallback** — when you genuinely need header injection
> (custom Bearer auth), model-name rewriting (SIRA sends one name,
> endpoint expects another), or adapter mode (non-OpenAI provider via
> `customizations/llm/proprietary_provider.py`): start the shim,
> point env vars at it, and the same pipeline command works. See
> `SETUP.md` for the shim setup.

## Running — multi-MNO (multi-cell)

The flow above handles **one** corpus. For a **multi-MNO / multi-release**
corpus, each `(mno, release)` is its own **cell** — a separate BEIR dataset
under a shared `db_root`, built and enriched independently. `sira_multi` is the
batch orchestrator that runs the single-cell pipeline once per cell. Full
operational detail (cross-cell query checks, rerank backends, Path-B synthesis)
lives in the **`multi-mno-sira` runbook**:
`docs/compact/strands/multi-mno-sira/multi-cell-runbook.md`.

Prereqs: NORA parse output exists for every cell
(`<env_dir>/out/parse/<mno>/<rel>/*_tree.json`); configs + patches installed
(`install_configs.sh`); enrichment LLM env vars set (Step 3 above).

**Step 1 — Emit one BEIR dataset per cell** (`--output` is the **db_root**):

    python -m sandbox.adapter.nora_to_beir \
        --env-dir /path/to/env_dir --output sandbox/adapter/out-db --multi-cell \
        --wipe-all-derived          # FIRST ingest: clears index/ enrichments/ runs/ per cell

Use `--wipe-stale-index` instead for an **incremental re-emit** — rebuilds the
BM25 index but KEEPS the `runs/` enrichment cache (so resume works). One dataset
lands at `<db_root>/<mno>__<release>/raw/` per cell (double-underscore).

**Step 2 — Build + enrich every cell** (batch orchestrator):

    python -m sandbox.sira_multi \
        --db-root sandbox/adapter/out-db --sira-clone sandbox/sira \
        --run-name enrich-stable --stages prepare,bm25,enrich_corpus
        # --only VZW__Feb2026,ATT__Nov2025   subset of cells
        # --dry-run                          print per-cell commands, don't run

**Pass `--run-name`** (e.g. `enrich-stable`) — it pins each cell's doc-enrich run
dir (`<cell>/runs/doc-enrich/<name>/`) so SIRA's resume can accumulate across
runs. Without it, `run_pipeline` uses a timestamped name and **a re-run can't
resume**. Point the service's `NORA_SIRA_DOC_ENRICH_RUN` at the same name.

Enrichment resumes by `doc_id`, so a new release re-enriches only its new docs.
**Caveat — content changes under an existing doc_id** (e.g. a re-parse that adds
tables) are NOT detected by SIRA's resume: the `bm25` stage re-indexes the new
text, but to *re-enrich* the changed docs run `sira_incremental prune` per cell
first, or re-emit with `--wipe-all-derived` for a full re-enrich (see
**Operational scenarios** below).

**Step 3 — Launch the cell-aware service**:

    uvicorn sandbox.sira_query.service:app --port 8040
    curl -s http://127.0.0.1:8040/healthz | python3 -m json.tool   # mode: multi-cell, cells: [...]

**Inspect / verify**:

    # tables inlined into NORA parse text + reached the SIRA corpus (one command):
    python -m sandbox.verify_tables --parse <env_dir>/out/parse --db-root sandbox/adapter/out-db

    # the enrichment phrases + full text (tables inline) for one req:
    python -m sandbox.sira_enrich_inspect <req_id> --text

`verify_tables` reports per-(mno/rel) parse counts (and fails if any req has a
`tables` field but no inline table — an inline regression), per-cell corpus
table counts, and a cross-check that tables reached the corpus.

## Operational scenarios (multi-MNO)

All of these assume a **pinned `--run-name`** (e.g. `enrich-stable`) so SIRA's
doc-enrich resume is stable. `$DB` = db_root, `$CLONE` = SIRA clone, `$ENV` =
NORA env dir.

### Continue after an abrupt stop / crash

`sira_multi` is per-cell sequential and continue-on-error, so finished cells are
intact. Just re-run the **same** command (same `--run-name`):

    python -m sandbox.sira_multi --db-root "$DB" --sira-clone "$CLONE" \
        --run-name enrich-stable --stages prepare,bm25,enrich_corpus

- Completed cells: `prepare`/`bm25` re-run in seconds; `enrich` resumes from the
  pinned trace and skips every done doc (no LLM calls).
- The cell that was mid-enrich: resumes — only not-yet-enriched docs hit the LLM.
- To skip finished cells entirely, add `--only <remaining cells>`.

If the crash left **failed** docs in a cell's trace (they'd otherwise count as
"seen" and be skipped), clear them first:

    python -m sandbox.sira_incremental retry-failed \
        --dataset "$DB/<cell>" --run-name enrich-stable --stage doc-enrich

### Add a new MNO and/or release (incremental)

1. Produce the new cell's NORA parse output (`$ENV/out/parse/<mno>/<rel>/`).
2. Re-emit — `--wipe-stale-index` rebuilds indexes but KEEPS every cell's
   enrichment cache, so existing cells don't re-enrich:

       python -m sandbox.adapter.nora_to_beir \
           --env-dir "$ENV" --output "$DB" --multi-cell --wipe-stale-index

3. Build + enrich **only the new cell(s)** (`--only`); existing cells untouched:

       python -m sandbox.sira_multi --db-root "$DB" --sira-clone "$CLONE" \
           --run-name enrich-stable --only <mno>__<rel> \
           --stages prepare,bm25,enrich_corpus

4. Restart the service — it enumerates + loads the new cell at startup
   (`curl /healthz` lists it). A new *release* of an existing MNO is the same
   flow; latest-release routing then picks the newest cell for an MNO-only query.

### Re-ingest after a corpus CONTENT change (re-parse / table fix / profile edit)

A re-parse changes existing docs' text under the same `doc_id`. SIRA's resume is
`doc_id`-keyed, so it would wrongly **skip** them. The `bm25` stage re-indexes
the new text regardless (so retrieval sees it), but to **re-enrich** the changed
docs:

- **Targeted** — evict changed/removed doc_ids from each cell's trace, then
  re-run step 2 (only changed docs re-hit the LLM):

      python -m sandbox.sira_incremental prune --dataset "$DB/<cell>" --run-name enrich-stable
      python -m sandbox.sira_multi --db-root "$DB" --sira-clone "$CLONE" --run-name enrich-stable ...

  `prune` needs a content-hash baseline from a prior `commit`; without one it
  can't tell what changed (then use the full path).
- **Full** — re-emit with `--wipe-all-derived` (clears the enrichment cache so
  everything re-enriches — the ~13h path; reserve for prompt/model changes).

After a successful enrich, record the new baseline for next time:

    python -m sandbox.sira_incremental commit --dataset "$DB/<cell>" --run-name enrich-stable
    #   add --full if this followed a --wipe-all-derived rebuild

### Change the enrichment prompt or model

Edit `prompts/doc_requirement_v01.txt` (or the model env var), re-run
`install_configs.sh`, then **force a re-enrich** — resume would otherwise skip
done docs. Use the **Full** path above (`--wipe-all-derived`), since the change
is corpus-wide.

### Run parallel stacks to A/B two LLMs / ingestions

Each stack is a SIRA service + NORA web pinned to one ingestion + one LLM
(config is read at import, so parallel stacks need separate processes).
`run_stack.sh` wires one from args; run it twice with different labels/ports:

    # shell 1 — Qwen3 stack on service :8040 / web :8080
    sandbox/run_stack.sh qwen "$DB_QWEN"  8040 8080 http://<qwen-host>:<port>  <qwen-model>
    # shell 2 — proprietary stack on service :8041 / web :8081
    sandbox/run_stack.sh prop "$DB_PROP"  8041 8081 http://<prop-host>:<port>  <prop-model> <api-key>

Then `:8080` = Qwen3, `:8081` = proprietary; same query in each, compare.
`--dry-run` prints the exact env + commands without launching; `--stop <label>`
kills a stack. Per-stack web state (jobs / metrics / feedback / config DBs) lives
under `$NORA_STACK_STATE_DIR/<label>/` (default `/tmp/nora-stacks/<label>/`) so the
two webs don't lock-contend or conflate Q&A logs, and each gets its own `/config`
page. `llm_base_url` is the base WITHOUT `/v1` (used as-is for the SIRA shim, `/v1`
appended for synthesis).

To point any of those DBs elsewhere, the web app also takes explicit flags
(`--jobs-db` / `--metrics-db` / `--feedback-db` / `--config-db`) or the matching
env vars (`$NORA_JOBS_DB` / `$NORA_METRICS_DB` / `$NORA_FEEDBACK_DB` /
`$NORA_CONFIG_DB`); `run_stack.sh` just wires them to `<state>/…` by default.
