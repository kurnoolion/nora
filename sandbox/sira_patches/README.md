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

## TODO — Future patches

### Support a dedicated `/rerank` endpoint for SIRA's rerank stage

**Status:** parked. Add when the per-stage routing has run long enough
in production to justify the latency-vs-flexibility tradeoff.

**Motivation.** SIRA's `llm_reranking.py` is LLM-as-judge — one chat
completion per (query, doc) pair, ~1-5s/call × 50-200 candidates per
query = 1-15 min/query just for rerank. A dedicated cross-encoder
reranker (TEI's `/rerank`, vLLM's `/v1/rerank`, or NORA's existing
`Reranker` Protocol implementations) bulk-scores N pairs in one HTTP
call at ~10-50ms per pair total — a 10-100× speedup.

**What this patch would do.** Add a new env var (e.g.
`NORA_SIRA_RERANK_BACKEND=chat|tei|openai-dedicated`, default `chat`
for backwards compatibility) and a corresponding code branch in
`llm_reranking.py` that:

1. **`backend=chat`** (current behavior, preserves the
   `NORA_SIRA_RERANK_LLM_URL` env-routed chat path).
2. **`backend=tei`** — POSTs to `{NORA_SIRA_RERANK_LLM_URL}/rerank`
   in Cohere shape (`{"query", "texts"}` → flat `[{"index", "score"}]`).
   Mirrors NORA's `TEIReranker` wire shape from
   `core/src/query/reranker.py`. Bulk-batched server-side; client-side
   chunking at `--max-client-batch-size` (default 32) per the same
   pattern as NORA.
3. **`backend=openai-dedicated`** — POSTs to
   `{NORA_SIRA_RERANK_LLM_URL}/rerank` in vLLM's OpenAI-style rerank
   shape (`{"query", "documents"}` → wrapped `{"results": [...]}`).
   Mirrors NORA's `OpenAIRerankDedicated`.

Each backend skips the per-pair fanout entirely — one bulk call replaces
the N chat-completion calls. The `relevance_requirement_v01.txt` prompt
becomes unused for non-chat backends (cross-encoders don't take prompts,
just scoring inputs).

**Design considerations to resolve before coding:**

- **Score scale alignment.** SIRA's LLM-as-judge prompt asks for 0-100
  scoring with a normative interpretation (the 5-band rubric in
  `relevance_requirement_v01.txt`). Cross-encoder rerankers emit raw
  similarity scores in arbitrary ranges (often negative for
  unrelated). Downstream stages (`NORA_SIRA_PIN_MIN_SCORE` default 30,
  `NORA_SIRA_PIN_REL_THRESHOLD` 0.5) are tuned to the 0-100 scale; a
  cross-encoder backend either needs the threshold knobs retuned per
  backend, or the scores normalized to 0-100 at the boundary. Decide
  before shipping.
- **Resume semantics.** Current rerank writes per-pair trace
  (`trace.kept.jsonl` / `trace.failed.jsonl`) keyed on `(query_id,
  doc_id)`. Bulk-call backends would need either per-call resume (one
  trace row per HTTP call, less granular) or per-pair resume after
  call success (more bookkeeping but matches current semantics).
- **Failure mode.** LLM-as-judge degrades gracefully (one bad call =
  one zero score). Bulk-call backends fail atomically (one bad call =
  N missing scores for the batch). Partial-batch-failure handling
  needs the same "score=None → fall through to unranked tail in input
  order" contract NORA's `TEIReranker` already implements.
- **Prompt retirement.** If non-chat backends are the default in a
  future iteration, `relevance_requirement_v01.txt` becomes
  chat-backend-specific. Document this rather than letting it confuse
  future readers.

**Scope estimate.** ~150-250 lines of code change in `llm_reranking.py`
+ ~50-100 lines of tests for each new backend's response-shape parsing
+ docs update in this README. The hardest part is score normalization
+ retuning `NORA_SIRA_PIN_*` thresholds against the new score range.

**Trigger to land.** When SIRA rerank latency becomes the dominant
contributor to eval-pass wall-clock AND the eval-pilot strand has
enough ground-truth scoring data to retune `NORA_SIRA_PIN_*` against
the new score distribution.
