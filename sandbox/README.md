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
