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
| `sira_multi.py` | Multi-MNO batch orchestrator — enumerates the `(mno, release)` cells under a db_root and runs SIRA's `run_pipeline.py` once per cell (writes each cell's `configs/data/<cell>.yaml` from the `nora.yaml` template first); `--verify` sweeps the same cells with read-only verify-run reports instead of building. See "Running — multi-MNO" below. | ✅ |
| `sira_query/service.py` | Cell-aware FastAPI per-query service (`/sira-query`, `/healthz`) — loads every cell at startup; resolves query scope → retrieve per cell → merge → rerank. Query-time config in the multi-mno-sira runbook. | ✅ |
| `sira_cells.py` | Cell-identity helpers (`cell_dirname`, `parse_cell_dirname`, `enumerate_cells`); release ordering re-exported from `core.extraction.release_key`. | ✅ |
| `sira_enrich_inspect.py` | Doc-enrichment inspector CLI — prints the phrases SIRA attached to a given `req_id` (from `best.jsonl` + the latest run's `enrichments.kept.jsonl`), multi-cell, with `--text` / `--trace`; `--failed` inverts it into a triage listing (failed reqs per cell grouped status → plan; local-only, ids redacted before sharing). | ✅ |
| `verify_tables.py` | Verifies parsed tables are inlined into NORA parse text (fails on any `tables`-field req missing its inline table) and reached the per-cell SIRA corpus, with a cross-check. `--parse` and/or `--db-root`. | ✅ |
| `run_stack.sh` | Launch one isolated SIRA-service + NORA-web stack (Path-B) from args — own ports, own LLM, own web state DBs — so several can run in parallel to A/B different LLMs / ingestions. `--dry-run` to preview, `--stop <label>` to tear down. | ✅ |
| `sira_incremental.py` | Content-hash resume helper (`prune` / `commit` / `promote` / `retry-failed` / `heal-torn` / `verify-run`) so a re-parse that changes a doc's text re-enriches it instead of being wrongly skipped by SIRA's doc_id resume. | ✅ |
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


## 1. Setup

First-time install (Python 3.12 venv, `uv`, Rust toolchain for `bm25x`,
trimmed vs. full dependency set) is step-by-step in [`SETUP.md`](SETUP.md)
— do that once; this section is what every ingest run assumes.

**Code + configs.** Clone SIRA and install NORA's configs, prompts, and
patches into the clone (idempotent; re-run after editing any config/prompt
or after `git -C sandbox/sira pull`):

    git clone --depth 1 https://github.com/facebookresearch/sira.git sandbox/sira
    bash sandbox/install_configs.sh

**Directory concepts** — the three paths every command below uses:

    ENV=/path/to/env_dir        # NORA env dir: $ENV/out/parse/<mno>/<rel>/*_tree.json
    DB=/path/to/db_root         # SIRA db_root: one <MNO>__<MMMYYYY>/ cell dataset each
    CLONE=sandbox/sira          # the SIRA clone (run_pipeline.py under scripts/)
    RUN=enrich-stable           # pinned run name — pick once, reuse forever

`$ENV` comes from NORA's extract + parse stages (per-cell layout, D-096);
`$DB` cells (e.g. `VZW-OA__Feb2026`) are what the adapter emits and what
`sira_multi` + the query service consume (D-107 / D-109). Pinning `$RUN`
is what makes resume, prune, and retry work.

**Venv gotcha.** `sira_multi` shells out to SIRA's `run_pipeline.py`, which
needs `bm25x` — run it (and the query service) under the SIRA venv:
`source sandbox/activate.sh` (also sets `PYTHONPATH`, localhost `NO_PROXY`,
HF-offline vars).

**Ingestion-time dependency services.** Enrichment is LLM-driven. The
recommended path is per-stage env routing (the
`sira_patches/per-stage-routing.patch`, applied by `install_configs.sh`) —
export these before any enrich run, then sanity-probe:

    export NORA_SIRA_ENRICH_LLM_URL=http://127.0.0.1:PORT/v1
    export NORA_SIRA_ENRICH_LLM_MODEL=<model-name>
    export NORA_SIRA_ENRICH_LLM_TIMEOUT=300     # ≥3× measured per-call latency
    export NORA_SIRA_RERANK_LLM_URL=http://127.0.0.1:PORT/v1
    export NORA_SIRA_RERANK_LLM_MODEL=<model-name>

    python -m sandbox.sira_patches.test.probe_per_stage_endpoints

Expect two `SPK ... OK` lines. The **shim** (`shim/openai_shim.py`,
`uvicorn sandbox.shim.openai_shim:app --port 8030`) is the fallback when
env routing can't reach your endpoint: it exposes `/v1/chat/completions`
and either forwards to `NORA_LLM_BASE_URL` (pass-through — injects
`Authorization: Bearer $NORA_LLM_API_KEY`, rewrites the model name to
`$NORA_LLM_MODEL`) or calls `customizations/llm/proprietary_provider.py`
(adapter mode, non-OpenAI APIs). Point `NORA_SIRA_*_LLM_URL` at the shim
and the same pipeline commands work. Full env-var reference, timeout
sizing, and troubleshooting: `SETUP.md` §5 and `sira_patches/README.md`.

## 2. Scenario matrix

Every scenario assumes §1 is done (SIRA venv active, `$ENV/$DB/$CLONE/$RUN`
set, enrichment env vars exported). Flags are explained once in §4 —
scenarios are copy-paste.

### 2.1 First-ever ingest — single MNO, single release

Preconditions: `$ENV/out/parse/<MNO>/<REL>/` has `*_tree.json`; `$DB` is
empty or absent.

    python -m sandbox.sira_preflight --env-dir "$ENV"
    python -m sandbox.adapter.nora_to_beir --env-dir "$ENV" --output "$DB" --multi-cell
    python -m sandbox.sira_multi --db-root "$DB" --sira-clone "$CLONE" --run-name "$RUN" --dry-run
    python -m sandbox.sira_multi --db-root "$DB" --sira-clone "$CLONE" --run-name "$RUN"
    python -m sandbox.sira_incremental commit --dataset "$DB/<MNO>__<REL>" --run-name "$RUN" --full

One cell is still a cell (D-107) — always use `--multi-cell`. Verify: the
adapter prints per-cell row/skip counts; run §2.12's checks; then start the
query service — §3.

### 2.2 Full batch — multiple MNOs × releases

Preconditions: every cell parsed under `$ENV/out/parse/<mno>/<rel>/`.
Identical to §2.1 — the adapter and `sira_multi` enumerate all cells; only
the baseline commit becomes a loop:

    python -m sandbox.sira_preflight --env-dir "$ENV"
    python -m sandbox.adapter.nora_to_beir --env-dir "$ENV" --output "$DB" --multi-cell
    python -m sandbox.sira_multi --db-root "$DB" --sira-clone "$CLONE" --run-name "$RUN"
    for c in "$DB"/*__*/; do
        python -m sandbox.sira_incremental commit --dataset "$c" --run-name "$RUN" --full
    done

Cells run sequentially, continue-on-error (D-110). Verify: `sira_multi`'s
per-cell summary; `curl /healthz` lists every cell after starting — §3.

### 2.3 Add a new release of an existing MNO (incremental)

Preconditions: the new cell is parsed in `$ENV`; existing cells in `$DB`
must stay untouched.

    python -m sandbox.adapter.nora_to_beir \
        --env-dir "$ENV" --output "$DB" --multi-cell \
        --only <MNO>__<NEWREL> --wipe-stale-index
    python -m sandbox.sira_multi --db-root "$DB" --sira-clone "$CLONE" \
        --run-name "$RUN" --only <MNO>__<NEWREL>
    python -m sandbox.sira_incremental commit \
        --dataset "$DB/<MNO>__<NEWREL>" --run-name "$RUN" --full

A new cell's first commit takes `--full` (sets its drift baseline). Verify:
only the new cell's docs hit the LLM; restart the query service — §3 —
and MNO-only queries now route to the new release (D-106).

### 2.4 Add a new MNO, one or many releases (incremental)

Same flow as §2.3 — `--only` takes a comma-separated cell list:

    python -m sandbox.adapter.nora_to_beir \
        --env-dir "$ENV" --output "$DB" --multi-cell \
        --only <MNO-C>__<REL1>,<MNO-C>__<REL2> --wipe-stale-index
    python -m sandbox.sira_multi --db-root "$DB" --sira-clone "$CLONE" \
        --run-name "$RUN" --only <MNO-C>__<REL1>,<MNO-C>__<REL2>
    # then commit each new cell with --full, as in §2.3

Verify: restart the query service — §3; `/healthz` `cells` includes the new
MNO's cells; a cross-MNO query shows its provenance badges (D-108).

### 2.5 Re-ingest after a content change (re-parse / profile edit)

Preconditions: the affected cell was re-parsed in `$ENV`; a prior `commit`
baseline exists for it (no baseline → prune can't diff; use §2.6).

    python -m sandbox.adapter.nora_to_beir \
        --env-dir "$ENV" --output "$DB" --multi-cell \
        --only <MNO>__<REL> --wipe-stale-index
    python -m sandbox.sira_incremental prune --dataset "$DB/<MNO>__<REL>" --run-name "$RUN"
    python -m sandbox.sira_multi --db-root "$DB" --sira-clone "$CLONE" \
        --run-name "$RUN" --only <MNO>__<REL>
    python -m sandbox.sira_incremental commit --dataset "$DB/<MNO>__<REL>" --run-name "$RUN"

Needed because SIRA's resume is doc_id-keyed and would wrongly skip changed
text. Verify: the enrich log shows only changed/new docs calling the LLM;
heed prune's CORPUS DRIFT WARNING if printed; restart the service — §3.

### 2.6 Enrichment prompt or model change (full re-enrich)

Preconditions: you edited `prompts/doc_requirement_v01.txt` (or friends) or
changed `NORA_SIRA_ENRICH_LLM_MODEL`. This is the slow (~13h) path.

    bash sandbox/install_configs.sh
    python -m sandbox.adapter.nora_to_beir \
        --env-dir "$ENV" --output "$DB" --multi-cell --wipe-all-derived
    python -m sandbox.sira_multi --db-root "$DB" --sira-clone "$CLONE" --run-name "$RUN"
    # then the §2.2 commit loop (--full)

Verify: `sira_enrich_inspect` (§2.12) shows phrases in the new prompt's
style; restart the query service — §3.

### 2.7 Crash / partial-run recovery

Preconditions: a `sira_multi` run died mid-flight (power, OOM, ^C).
Finished cells are intact; docs interrupted mid-enrich are in *neither*
trace, so a plain re-run with the **same** `--run-name` picks them up:

    python -m sandbox.sira_multi --db-root "$DB" --sira-clone "$CLONE" --run-name "$RUN"

Completed cells re-run `prepare`/`bm25` in seconds and skip all enriched
docs (no LLM calls); add `--only <remaining cells>` to skip them entirely.
If the crash also *recorded* failed docs, chase with §2.8. If a later
resume enriches 0 docs and `enrich_query` logs "No doc enrichments found",
run `sira_incremental promote` — see §4 and `SETUP.md` §"Recovering a
resume". Verify: resume log lines show skipped counts ≈ prior progress.

### 2.8 Retry failed docs only

Preconditions: a finished run left rows in `trace.failed.jsonl` (inspect
`$DB/<MNO>__<REL>/runs/doc-enrich/$RUN/trace.failed.jsonl` first — retry
genuine errors; `status: all_filtered` rows are NOT worth retrying).

    python -m sandbox.sira_incremental retry-failed \
        --dataset "$DB/<MNO>__<REL>" --run-name "$RUN" --stage doc-enrich
    python -m sandbox.sira_multi --db-root "$DB" --sira-clone "$CLONE" \
        --run-name "$RUN" --only <MNO>__<REL>

One-command form via the lane (evict + re-run; add `--heal-torn` too if
the previous run was killed mid-write — power loss, SIGKILL):

    python -m sandbox.sira_lane --env-dir "$ENV" --db-root "$DB" \
        --run-name "$RUN" --only <MNO>__<REL> --wipe-stale-index --retry-failed

Add `--max-reqs 1` to run the retried reqs in single-req mode (one LLM
call per req, same batched prompt incl. taxonomy block) — a req that
failed inside a large batch gets a clean solo shot. Exports
`NORA_SIRA_BATCH_MAX_REQS` to the build; >1 caps reqs/batch (never above
the response-budget-derived limit).

Coarse `doc:`/`section:` corpus rows are skipped by default (traced as
`skipped_doc_chunk` / `skipped_section_chunk`, benign) — `retry-failed`
leaves those rows alone. To enrich them after the fact, combine
`--include-skipped` (evicts the skipped rows) with the build opt-ins
`--enrich-doc-chunks` / `--enrich-section-chunks` in the same lane pass.

Verify: `retry-failed` prints per-file eviction counts; the resume
re-enriches exactly those docs; `trace.failed.jsonl` shrinks.

### 2.9 Retire / remove a cell

Preconditions: the cell exists in `$DB`; you no longer want it served.

    rm -rf "$DB/<MNO>__<REL>"
    # restart the query service — §3
    curl -s http://127.0.0.1:8040/healthz | python3 -m json.tool

Verify: `/healthz` `cells` no longer lists it and `corpus_size` dropped;
queries scoped to it now return it under `unresolved`.

### 2.10 Rebuild the BM25 index only (no re-enrichment)

Preconditions: index is stale/corrupt (e.g. the `doc_id out of range`
panic) but enrichments are good. No LLM needed — `--stages prepare,bm25`
skips enrichment and `--wipe-stale-index` keeps the `runs/` cache:

    python -m sandbox.adapter.nora_to_beir \
        --env-dir "$ENV" --output "$DB" --multi-cell --wipe-stale-index
    python -m sandbox.sira_multi --db-root "$DB" --sira-clone "$CLONE" \
        --run-name "$RUN" --stages prepare,bm25

Verify: each cell has a fresh `index/best`; restart the query service — §3.

### 2.11 Nuke-and-rebuild everything

Preconditions: you accept a from-scratch re-enrich of every cell.

    rm -rf "$DB"
    python -m sandbox.adapter.nora_to_beir --env-dir "$ENV" --output "$DB" --multi-cell
    python -m sandbox.sira_multi --db-root "$DB" --sira-clone "$CLONE" --run-name "$RUN"
    # then the §2.2 commit loop (--full)

Verify: as §2.2; restart the query service — §3.

### 2.12 Audit / verify an ingest

Preconditions: an ingest finished (any of §2.1–§2.6). To audit the
adapter's filtering, re-run your last adapter command with the two print
flags added (follow with a §2.10 index rebuild since it re-wipes):

    python -m sandbox.adapter.nora_to_beir --env-dir "$ENV" --output "$DB" \
        --multi-cell --only <MNO>__<REL> --wipe-stale-index --print-skips --print-noid

    # tables inlined into parse text AND present in the per-cell corpus (D-119):
    python -m sandbox.verify_tables --parse "$ENV/out/parse" --db-root "$DB"

    # what enrichment attached to one requirement:
    python -m sandbox.sira_enrich_inspect <req_id> --db-root "$DB" --text

    # corpus row counts per cell / per granularity:
    wc -l "$DB/<MNO>__<REL>/raw/corpus.jsonl"
    grep -c '"_id": "doc:'     "$DB/<MNO>__<REL>/raw/corpus.jsonl"
    grep -c '"_id": "section:' "$DB/<MNO>__<REL>/raw/corpus.jsonl"

Skip-count interpretation: the adapter always prints counts of rows
dropped as **sections** (`is_requirement=False` — the parser's type
discriminator, D-125; ID-labeled definitions are the requirements, bare
ids are references, D-126) and as **duplicates**; both are expected, and
`--print-skips` lists the ids so you can confirm nothing real was dropped.
`--print-noid` samples id-less nodes for the same check. Enrichment
completeness = trace.kept ∪ trace.failed; `all_filtered` rows WERE
processed (see `SETUP.md` §"Verifying enrichment completeness").

## 3. Running the serving stack

### SIRA query service

    source sandbox/activate.sh              # SIRA venv (bm25x) + NO_PROXY + HF-offline
    export NORA_SIRA_DB_ROOT="$DB"
    export NORA_SIRA_DOC_ENRICH_RUN="$RUN"  # must match the ingest --run-name
    uvicorn sandbox.sira_query.service:app --port 8040
    curl -s http://127.0.0.1:8040/healthz | python3 -m json.tool

The service loads **every** cell under `$DB` at startup (RAM grows with
cell count) and never re-scans — **any added / removed / re-ingested cell
requires a service restart**. `/healthz` is the truth: expect
`"mode": "multi-cell"`, the full `cells` list, `cells_load_error: null`.
Per query: resolve scope → retrieve per cell → merge → rerank the pooled
candidates (D-105, D-110), balanced fusion (D-118) with the top_k cut
scaled per cell (D-121); an MNO-only query hits that MNO's latest release
(D-106). Query enrichment goes to `NORA_LLM_SHIM_URL` (default
`http://127.0.0.1:8030` — the shim; any OpenAI-compatible base without
`/v1` works). Rerank backend: `NORA_SIRA_RERANK_BACKEND` = `chat`
(default, pointwise LLM-as-judge) | `tei` | `openai-dedicated` (bulk
cross-encoder calls, D-116; resilience D-117), routed by
`NORA_SIRA_RERANK_LLM_URL/_MODEL/_API_KEY`. Full env list in §4.

### NORA web

    python -m core.src.web.app --port 8080

Static config in `config/web.json` (host, port, `env_dir`, path mappings);
CLI `--host/--port/--env-dir` override it. The web reaches the SIRA
service via `NORA_SIRA_QUERY_URL` (default `http://127.0.0.1:8040`).
Which cells answer a query is decided **per query on the SIRA side**
(`resolve_cells` over the MNOs/releases detected in the query text) — the
web env config's `mnos`/`releases` lists only feed the corpus label shown
in the UI, they are cosmetic, not a filter.

### Parallel A/B stacks — `run_stack.sh` (D-120)

One stack = one SIRA service (rerank off — Path-B) + one NORA web, pinned
at launch to one db_root + one LLM, with isolated state DBs. Run it from
the **full NORA venv** (the web inherits it); the service auto-sources
`sandbox/activate.sh`:

    source /path/to/nora-venv/bin/activate
    sandbox/run_stack.sh stackA "$DB_A" 8040 8080 http://127.0.0.1:PORT1 <model-a>
    sandbox/run_stack.sh stackB "$DB_B" 8041 8081 http://127.0.0.1:PORT2 <model-b> <api-key>

    sandbox/run_stack.sh --dry-run stackA "$DB_A" 8040 8080 http://127.0.0.1:PORT1 <model-a>
    sandbox/run_stack.sh --stop stackA

`--dry-run` prints the exact env + commands without launching; `--stop
<label>` tears a stack down (a registry remembers each label's state dir).
For attributable A/B, pool feedback across stacks with a shared
`--feedback-db` (rows carry `llm_model`); keep config DBs per-stack (the
default). Logs are self-describing (args + `NORA_*`/`SIRA_*` env, keys
redacted) under each stack's state dir.

## 4. Command reference

### `sandbox.sira_lane` — the sira lane in one command

    python -m sandbox.sira_lane --env-dir "$ENV" --db-root "$DB" \
        [--run-name enrich-stable] [--only <MNO>__<REL>,...] \
        [--wipe-stale-index | --wipe-all-derived] \
        [--heal-torn] [--retry-failed [--include-all-filtered] [--include-skipped]] \
        [--enrich-doc-chunks] [--enrich-section-chunks] \
        [--max-reqs N] [--verify] \
        [--stages prepare,bm25,enrich_corpus] [--dry-run]

Runs the adapter (`nora_to_beir --multi-cell`) then `sira_multi`, threading
`--only` and the wipe flag through both. `--max-reqs N` exports
`NORA_SIRA_BATCH_MAX_REQS=N` to the build (caps reqs per enrichment batch;
`1` = single-req mode — the typical pairing with `--retry-failed`).
`--enrich-doc-chunks` / `--enrich-section-chunks` opt coarse `doc:` /
`section:` corpus rows into enrichment (default: skipped, traced as
`skipped_*`; forwarded to `sira_multi`, which exports the matching
`NORA_SIRA_BATCH_ENRICH_*` env vars). `--include-skipped` extends
`--retry-failed`'s eviction to those skipped rows — pair it with the
opt-ins to enrich coarse chunks after a default-skip build.
`--verify` appends a read-only per-cell health sweep after the lane
(`sira_multi.verify_cells` over the same `--only` scope); non-zero exit
when any cell FAILs. `--heal-torn` / `--retry-failed`
first run the matching `sira_incremental` repairs over each cell's
`runs/doc-enrich/<run-name>/` (heal before retry) — resume-after-power-loss
and retry-old-failures in one command. Incremental runs only: under
`--wipe-all-derived` the adapter wipes `runs/`, so both are skipped with a
note (the full rebuild supersedes them) — the single entrypoint for the sira
retrieval lane, symmetric with `run_cli --lane ingestion|nora` (docker-distro
lane model). The adapter stamps `$DB/SOURCE.json` (env_dir + repo git sha +
timestamp) so every build is traceable to its ingestion. The individual
commands below remain valid for surgical use.

### `sandbox.adapter.nora_to_beir` — parse trees → per-cell BEIR datasets

- `--env-dir PATH` (required) — NORA env dir; reads
  `out/parse/**/*_tree.json` recursively.
- `--output PATH` (required) — in `--multi-cell` mode this is the
  **db_root**; one dataset lands at `<output>/<MNO>__<REL>/raw/` per cell
  (D-096 layout; composite `(MNO, release, doc_id)` identity D-108).
  Without `--multi-cell` it's a single dataset dir (legacy path, kept by
  D-114).
- `--multi-cell` — partition trees by `(mno, release)`; release must match
  `MMMYYYY` or the run **fails loud** (D-109).
- `--name NAME` — dataset name in `metadata.json` (single-dataset mode
  only; defaults to the `--output` basename; ignored with `--multi-cell`).
- `--only <MNO>__<REL>[,...]` — emit only these cells (case-insensitive;
  fails loud on a cell with no parsed trees). Without it every cell under
  `$ENV` is re-emitted — and wiped, if a wipe flag is set.
- `--wipe-stale-index` vs `--wipe-all-derived` — both clear the
  corpus-size-dependent `index/` + `enrichments/` (+ stale `eval/` /
  `retrieval/` caches); `--wipe-all-derived` **also** clears `runs/` (the
  enrichment cache) forcing a from-scratch re-enrich. Rule of thumb:
  stale-index for growth with unchanged prompts, all-derived for
  prompt/model changes or corpus-composition shifts.
- `--print-skips` — list req_ids dropped as sections
  (`is_requirement=False`, D-125/D-126) and as duplicates (counts always
  print).
- `--print-noid` — sample the id-less nodes (section_number + title).
- `--section-max-depth N` — max section-prefix depth for section-level
  multi-granularity rows (default 2: `5` and `5.1`, not `5.1.1`).

### `sandbox.sira_multi` — run SIRA's pipeline once per cell (D-110)

- `--db-root PATH` (required) — parent of the `<MNO>__<REL>/` cells.
- `--sira-clone PATH` (required) — the SIRA clone
  (`<clone>/scripts/run_pipeline.py`).
- `--stages LIST` — default `prepare,bm25,enrich_corpus` (corpus-only;
  cells have no real queries, the service enriches/reranks live).
- `--only CELLS` — comma-separated subset (same format as the adapter's).
- `--run-name NAME` — pins each cell's doc-enrich run dir
  (`<cell>/runs/doc-enrich/<name>/`) so resume accumulates across runs;
  without it SIRA timestamps the run and **a re-run can't resume**. Match
  the service's `NORA_SIRA_DOC_ENRICH_RUN`.
- `--sglang-port N` — `sglang.port` override; omit under per-stage env
  routing.
- `--dry-run` — print the per-cell commands (each cell gets a generated
  `configs/data/<cell>.yaml` from the `nora.yaml` template, D-115).

### `sandbox.sira_incremental` — content-hash resume helper

All subcommands take `--dataset <db_root>/<MNO>__<REL>` and
`--run-name NAME` (both required; run-name must match the ingest's).

- `prune` — pre-enrich: evict changed/removed doc_ids from the resume
  trace by content-hash diff against the last `commit` baseline.
  `--max-growth-ratio R` (default 1.5) prints a DRIFT WARNING past
  cumulative growth R; `--strict-growth` turns it into a hard exit-2.
- `commit [--full]` — post-enrich: record current corpus hashes as the new
  baseline. `--full` marks a full rebuild (after `--wipe-all-derived` or a
  cell's first ingest) and resets the drift baseline.
- `promote` — reconstruct `enrichments/doc/<run>.jsonl` + `best.jsonl`
  after a full-resume run left a dangling symlink (the "No doc enrichments
  found" recovery).
- `retry-failed [--stage doc-enrich|rerank|both] [--include-all-filtered]`
  — evict *recorded-failed* entries so the next resume reprocesses them.
  Default stage `both`; default scope errors-only (keeps `all_filtered` —
  include them only after a prompt/LLM change).
- `heal-torn [--stage doc-enrich|rerank|both]` — after a hard interruption
  (power loss, SIGKILL): drop torn half-written trailing lines from every
  `*.jsonl` in the run dir, then repair the doc-enrich resume invariant —
  a `trace.kept` row whose enrichment row was lost is evicted (re-enrich),
  an enrichment row whose kept row was lost is dropped (no duplicate
  phrases on re-enrich). `sira_lane --heal-torn` runs it inline per cell
  before the lane.
- `verify-run [--compare-run NAME] [--strict]` — READ-ONLY health report
  for ONE cell's doc-enrich run, paste-safe (counts/statuses only, never
  ids or content). Single-cell like every other subcommand here —
  multi-cell sweeps live at the orchestrator layer: `sira_multi
  --verify` (sweep instead of build) and `sira_lane --verify`
  (post-build gate), both looping this per cell. Batch stats are scoped
  to the LATEST invocation (batches files append across every
  resume/retry; a `history:` line counts what's excluded) so verdicts
  reflect current state, not accumulated eras; failed rows also get a
  sanitized top-errors histogram (URLs/IP:port redacted). Sections: `[batches]` status/closed_by/round histograms +
  reqs-per-batch stats + single-req-mode detection; `[trace]` kept/failed
  reconciliation (granularity split, duplicates, kept∩failed);
  `[coverage]` vs `raw/corpus.jsonl`; `[invariant]` torn lines +
  kept↔enrichment both directions (all zeros on a healthy run).
  `--compare-run` diffs per-doc phrase SETS against a second run of the
  same cell (e.g. batch mode vs `--max-reqs 1`) as Jaccard buckets — the
  strongest no-cross-contamination check. Exit 1 on FAIL (structural
  breaks); `--strict` also fails on WARN (quality signals) for CI-style
  gating. Run after every build; run before trusting a heal/retry pass.

### Query-service env vars (`sandbox/sira_query/service.py`)

- `NORA_SIRA_DB_ROOT` — the cell db_root (required for multi-cell mode).
- `NORA_SIRA_DOC_ENRICH_RUN` — pinned doc-enrich run to load phrases from.
- `NORA_LLM_SHIM_URL` / `NORA_LLM_MODEL` / `NORA_LLM_SHIM_API_KEY` (falls
  back to `NORA_LLM_API_KEY`) — query-enrichment endpoint, base **without**
  `/v1` (the service appends `/v1/chat/completions`).
- `NORA_SIRA_RERANK_BACKEND` — `chat` | `tei` | `openai-dedicated`
  (D-116); bulk backends post once to `{URL}/rerank` / `{URL}/v1/rerank`
  and ignore the rerank prompt.
- `NORA_SIRA_RERANK_LLM_URL/_MODEL/_API_KEY` — rerank endpoint override,
  base **without** `/v1`. (Caution: the *batch-pipeline* env var of the
  same name takes the base **with** `/v1` — see §1.)
- `NORA_SIRA_RERANK_MAX_TOKENS` (256), `NORA_RERANK_BATCH_SIZE` (0 =
  per-call; batch failures zero the whole batch — D-117 covers bulk-backend
  resilience), `NORA_SIRA_RERANK_ENABLED` (`false` → round-robin balance).
- `NORA_SIRA_FUSION_BALANCED` (D-118) + `NORA_SIRA_SCALE_TOPK_BY_CELLS`
  (default on, D-121) — balanced cross-cell cut, top_k scaled per cell.
- `NORA_SIRA_TOP_K` (10), `NORA_SIRA_RERANK_TOP_N` (20),
  `NORA_SIRA_MAX_DF_RATIO` (0.05), `NORA_SIRA_EXPANSION_WEIGHT` (0.5),
  `NORA_SIRA_QUERY_ENRICH_ENABLED` (true).

Web-side: `NORA_SIRA_QUERY_URL` (default `http://127.0.0.1:8040`),
`NORA_SIRA_QUERY_TIMEOUT` (1200s).

### `run_stack.sh` args (D-120)

    run_stack.sh [OPTIONS] <label> <db_root> <svc_port> <web_port> <llm_base_url> <llm_model> [api_key]
    run_stack.sh --stop <label>

`<llm_base_url>` is the base **without** `/v1` (used as-is for the
service's query-enrich shim var, `/v1` appended for web synthesis).
Options: `--state-dir` / `--log-dir`; `--feedback-db` / `--config-db` /
`--jobs-db` / `--metrics-db` (flag > env var `$NORA_FEEDBACK_DB` etc. >
default `<state>/<name>.db`); `--service-activate` / `--web-activate` and
`--service-python` / `--web-python` (venv wiring; a preflight
import-checks `uvicorn`/`fastapi`/`bm25x` per role); `--reasoning-sentinel`
(web-only final-answer-marker mode for thinking LLMs); `--dry-run`.

### Verification CLIs

- `sandbox.sira_preflight --env-dir PATH` — fail loud on any non-`MMMYYYY`
  release dir before extraction (D-109).
- `sandbox.verify_tables [--parse DIR] [--db-root DIR]` — tables inlined in
  parse text and present in each cell's corpus (D-119); `--db-root`
  defaults to `$NORA_SIRA_DB_ROOT`.
- `sandbox.sira_enrich_inspect <req_id> [--db-root DIR] [--cell NAME]
  [--run NAME] [--text] [--trace]` — the enrichment phrases (and
  optionally corpus text / raw trace row) for one requirement.
- `sandbox.sira_enrich_inspect --failed [--db-root DIR] [--cell NAME]
  [--run NAME] [--limit N]` — triage listing: per cell, the failed reqs
  from `trace.failed*.jsonl` grouped status → plan, `--limit` ids per
  group (default 10, 0 = all). LOCAL-ONLY (real req ids / plan codes) —
  the paste-safe counterpart is `sira_multi --verify`. Triage flow:
  `--verify` (is anything wrong?) → `--failed` (what, where?) →
  `<req_id> --trace` (why this one?) → `--retry-failed [--max-reqs 1]`.

Deeper narrative (design rationale, cross-cell query checks, Path-B
synthesis) lives in the archived runbook:
`docs/compact/strands/_archive/multi-mno-sira/multi-cell-runbook.md`.
