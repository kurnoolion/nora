# SIRA patches

Patches applied on top of the upstream SIRA clone (`sandbox/sira/`,
gitignored) to add NORA-specific capabilities the upstream doesn't
provide. Idempotently applied by `sandbox/install_configs.sh`.

## `per-stage-routing.patch`

### What it does

Adds per-stage LLM endpoint routing via env vars. Eliminates the need
to run `sandbox/shim/openai_shim.py` when the LLM endpoint is reachable
directly (over the network) instead of via SIRA's hardcoded localhost.

Four SIRA scripts are patched:

| Script | Stage | Env var prefix |
|---|---|---|
| `scripts/add_doc_index_adapter.py` | corpus enrichment | `NORA_SIRA_ENRICH_LLM_*` |
| `scripts/enrich_query_and_retrieve.py` | query enrichment | `NORA_SIRA_ENRICH_LLM_*` (shared with corpus) |
| `scripts/llm_reranking.py` | reranking | `NORA_SIRA_RERANK_LLM_*` |
| `scripts/run_pipeline.py` | pre-flight sglang server spawn | reads both prefixes to decide whether to skip |

When the relevant env var is unset, behavior is unchanged — SIRA hits
`http://127.0.0.1:{sglang.port}/v1/chat/completions` exactly as before.

### Env vars

| Variable | Purpose | When unset |
|---|---|---|
| `NORA_SIRA_ENRICH_LLM_URL` | Base URL for corpus + query enrichment (e.g. `http://your-host:port/v1`) | Falls back to `sglang.port` localhost |
| `NORA_SIRA_ENRICH_LLM_MODEL` | Model name for enrichment payload | Falls back to `sglang.model` |
| `NORA_SIRA_RERANK_LLM_URL` | Base URL for reranking | Falls back to `sglang.port` localhost |
| `NORA_SIRA_RERANK_LLM_MODEL` | Model name for rerank payload | Falls back to `sglang.model` |

All values are optional. The URL is the OpenAI-style `/v1` base; the
patched code appends `/chat/completions` itself.

### Usage examples

After running `bash sandbox/install_configs.sh` (which applies this
patch), pick whichever combination matches your environment:

**Single OpenAI-compatible endpoint for everything** (e.g. existing
proprietary LLM gateway):

```bash
export NORA_SIRA_ENRICH_LLM_URL=http://<your-llm-host>:<port>/v1
export NORA_SIRA_ENRICH_LLM_MODEL=<your-model-name>
export NORA_SIRA_RERANK_LLM_URL=http://<your-llm-host>:<port>/v1
export NORA_SIRA_RERANK_LLM_MODEL=<your-model-name>
python scripts/run_pipeline.py data=nora db_root=...
```

**Split: high-quality enrichment + fast local rerank** (the canonical
use case — proprietary LLM is high-quality but per-call slow; local
small LLM is fast for the 50× rerank calls per query):

```bash
export NORA_SIRA_ENRICH_LLM_URL=http://<proprietary-host>:<port>/v1
export NORA_SIRA_ENRICH_LLM_MODEL=<large-model>
export NORA_SIRA_RERANK_LLM_URL=http://localhost:11434/v1   # Ollama
export NORA_SIRA_RERANK_LLM_MODEL=<small-fast-model>
python scripts/run_pipeline.py data=nora db_root=...
```

**vLLM for everything**:

```bash
export NORA_SIRA_ENRICH_LLM_URL=http://<vllm-host>:<port>/v1
export NORA_SIRA_ENRICH_LLM_MODEL=<served-model-name>
export NORA_SIRA_RERANK_LLM_URL=http://<vllm-host>:<port>/v1
export NORA_SIRA_RERANK_LLM_MODEL=<served-model-name>
python scripts/run_pipeline.py data=nora db_root=...
```

**Original behavior (no env vars)** — SIRA spawns or expects local
sglang on `cfg.sglang.port`, hits localhost. The patch is a no-op in
this case.

### Verification

After setting env vars, run:

```bash
python -m sandbox.sira_patches.test.probe_per_stage_endpoints \
    --enrich-url   "$NORA_SIRA_ENRICH_LLM_URL" \
    --enrich-model "$NORA_SIRA_ENRICH_LLM_MODEL" \
    --rerank-url   "$NORA_SIRA_RERANK_LLM_URL" \
    --rerank-model "$NORA_SIRA_RERANK_LLM_MODEL"
```

Reports compact `SPK` lines — one per endpoint — confirming reachability
and response shape before the SIRA pipeline runs. See
`test/probe_per_stage_endpoints.py` for details.

At pipeline runtime, each stage logs which URL it resolved:

```
LLM URL: http://your-host:port/v1/chat/completions (env-routed via NORA_SIRA_ENRICH_LLM_URL)
```

If env vars are set but the URL line still shows `(sglang config)`, the
patch wasn't applied — re-run `bash sandbox/install_configs.sh`.

### When env vars route, `run_pipeline.py` skips localhost spawn

If either `NORA_SIRA_ENRICH_LLM_URL` or `NORA_SIRA_RERANK_LLM_URL` is
set, the patched `run_pipeline.py` skips the `localhost:{port}/v1/models`
reachability check and the fallback `_start_server(cfg)` spawn. You'll
see this in the log:

```
SIRA LLM stages routed via NORA_SIRA_*_LLM_URL env vars — skipping
localhost sglang reachability check and spawn.
```

### Reverting

The SIRA clone (`sandbox/sira/`) is gitignored in NORA. To revert:

```bash
cd sandbox/sira
git checkout -- scripts/
```

Then re-run `bash sandbox/install_configs.sh` only if you also want the
hydra configs + prompts copied back in.
