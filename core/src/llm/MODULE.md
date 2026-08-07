# llm

**Purpose**
LLM abstraction layer. Defines the `LLMProvider` Protocol — a single-method structural-typing contract (`complete(prompt, system, temperature, max_tokens) -> str`) that every other module uses to call an LLM. No module outside `src/llm/` imports an LLM SDK directly; new providers are added by implementing `complete()` on a class, with no base-class inheritance. Serves FR-14 (LLM synthesis seam); covers NFR-5 (model picker auto-selects fitting model), NFR-6 (LLMProvider Protocol swap-by-instance per D-006).

**Public surface**
- `LLMProvider` (base.py) — `@runtime_checkable Protocol`; the only LLM interface the rest of the project sees
- `answering_model(llm)` (base.py) — name of the model that produced the last answer (`last_model` over static `model`; empty for mocks) — the provenance source for synthesis epilogues
- `OllamaProvider` (ollama_provider.py) — local Ollama HTTP client via stdlib `urllib`; `model`, `call_count`, `last_call_stats` (total_duration_s, eval_count, prompt_eval_count, tokens_per_second, model) for observability
- `OpenAICompatibleProvider` (openai_provider.py) — Chat-Completions client for any OpenAI-compatible cloud endpoint (OpenRouter, Together AI, DeepInfra, Groq, Fireworks, vLLM, OpenAI). Stdlib `urllib` only. Reads `NORA_LLM_BASE_URL` / `NORA_LLM_API_KEY` / `NORA_LLM_MODEL` env vars when constructor args are None. Same `model` / `call_count` / `last_call_stats` surface as `OllamaProvider`.
- `MockLLMProvider` (mock_provider.py) — deterministic keyword-based stub for tests and offline runs; `call_count`
- `HardwareInfo`, `ModelSpec`, `ModelChoice` (model_picker.py); `detect_hardware()`, `pick_model(hw, prefer=None)`, `list_available_ollama_models()`, `check_model_available()` — auto-select the best Ollama model that fits detected RAM/VRAM
- `parse_markers`, `is_permanent_refusal`, `RefusalFallbackProvider`, `maybe_wrap_with_refusal_fallback` (refusal.py) — permanent-refusal detection + provider decorator: a marker-prefixed, JSON-free response is retried once on a fallback endpoint (`NORA_LLM_REFUSAL_MARKERS` + `NORA_LLM_FALLBACK_BASE_URL/_MODEL/_API_KEY`). Applied inside `PipelineContext.create_llm_provider` (so every pipeline stage, the debug/miner CLIs, and the web query/chat lane inherit it) plus golden_cli's own builder; idempotent, so double-wrapping is safe. `used` counter for content-free visibility (NFR-8)
- Debug CLI (`llm_debug.py`):
  - `--probe <URL>` — probes the four interesting routes (`GET /api/tags`, `GET /v1/models`, `POST /api/chat`, `POST /v1/chat/completions`) and reports whether the endpoint speaks native Ollama, OpenAI-compatible, or both. Prints a copy-pasteable `NORA_LLM_*` env-var config recommendation. Use before configuring a new custom Ollama URL or proprietary-LLM proxy.
  - `--check` — resolves the active provider via the unified D-044 chain (CLI > env > config/llm.json > default) and sends a one-line "ping" completion. Reports the resolved provider class and whether construction landed on a mock fallback. Useful when web UI answers don't match what `NORA_LLM_*` says.
  - `--complete --text "..."` — runs a single completion on ad-hoc text via the active provider. Prints response, latency, and `last_call_stats`.

**Invariants**
- The `LLMProvider` Protocol is the single LLM seam. Changing its signature is a coordinated breaking change across every caller (taxonomy, query, eval).
- `complete()` returns plain text — JSON parsing is the caller's responsibility. Keeps the Protocol minimal and portable to providers without structured-output modes.
- Default `temperature=0.0` — all offline extraction paths want determinism.
- `OllamaProvider.__init__` pings `/api/tags` and raises `ConnectionError` on failure, and logs a warning if the requested model isn't pulled — fail fast with a diagnosable message.
- `OpenAICompatibleProvider.__init__` validates that model + base_url + api_key are all resolvable (constructor arg or env var) and raises `ValueError` with a precise message otherwise — fail before the first request, not on the first 401.
- `last_call_stats` schema is identical across providers (`total_duration_s`, `eval_count`, `prompt_eval_count`, `tokens_per_second`, `model`). Swapping providers does not change the metrics shape.

**Key choices**
- `Protocol` + structural typing over an ABC — lets a vendor SDK wrapper satisfy the interface with zero inheritance, which matters when work-laptop constraints force swapping providers.
- Stdlib `urllib` for the Ollama HTTP call — no `httpx` dependency, keeps the module installable on offline/locked-down hosts.
- Model selection is data-driven (`ModelSpec` list in `model_picker.py`), not wired into provider constructors — hardware detection runs once and the pipeline config records the chosen model.
- Refusal detection in `refusal.py` is a deliberate twin copy of `sandbox/llm_refusal.py` (same rules, same markers env var): core cannot import sandbox (D-111) and the sandbox copy must stay flat-copyable into the SIRA clone. A sync test (`test_llm_refusal.py::TestTwinSync`) fails loudly if the copies drift.

**Non-goals**
- Not a prompt library — every caller owns its prompts; we don't centralize them here.
- No streaming, tool-use, or function-calling surface — `complete()` is one-shot text in, text out. If a caller needs structured output, it parses JSON from the response.
- No retries, rate-limiting, or cost tracking at the Protocol layer — providers implement whatever is needed for their own backend.
- No embedding surface — embeddings live in [vectorstore](../vectorstore/MODULE.md) behind `EmbeddingProvider`.

<!-- BEGIN:STRUCTURE -->
_Alphabetical, regenerated by regen-map._


`base.py`
- `LLMProvider` — class — pub — Protocol for LLM providers.
  - `complete` — method — pub — Send a prompt to the LLM and return the text response.

`llm_debug.py`
- `_http_get` — function — internal — Return (status_code, raw_body_excerpt, parsed_json_or_None).
- `_http_post` — function — internal
- `_resolve_active_provider` — function — internal — Construct the LLM provider via the unified D-044 chain — same
- `_summarize_models` — function — internal — Best-effort extraction of a model list from a /models or /tags response.
- `cmd_check` — function — pub
- `cmd_complete` — function — pub
- `cmd_probe` — function — pub
- `logger` — constant — pub
- `main` — function — pub

`mock_provider.py`
- `MockLLMProvider` — class — pub — Mock LLM provider that extracts features using keyword matching.
  - `__init__` — constructor — internal
  - `_consolidate_features` — method — internal — Pass through — consolidation is done algorithmically, not by LLM.
  - `_extract_features` — method — internal — Match section headings against feature catalog.
  - `call_count` — property — pub
  - `complete` — method — pub
- `_FEATURE_CATALOG` — constant — internal
- `logger` — constant — pub

`model_picker.py`
- `HardwareInfo` — dataclass — pub
  - `compact` — method — pub — One-line summary for reports.
- `MODEL_CATALOG` — constant — pub
- `ModelChoice` — dataclass — pub
- `ModelSpec` — dataclass — pub
  - `fits` — method — pub — Check if model fits available memory.
- `check_model_available` — function — pub — Check if a model is available on the local Ollama server.
- `detect_hardware` — function — pub — Detect CPU, GPU, and RAM using Linux commands.
- `list_available_ollama_models` — function — pub — Query local Ollama server for pulled models.
- `logger` — constant — pub
- `pick_model` — function — pub — Select the best model for the detected hardware.

`ollama_provider.py`
- `OllamaProvider` — class — pub — LLM provider using a local Ollama server.
  - `__init__` — constructor — internal
  - `call_count` — property — pub
  - `complete` — method — pub — Send a prompt to the local Ollama model.
  - `last_call_stats` — property — pub — Performance stats from the most recent complete() call.
  - `model` — property — pub
- `_DEFAULT_BASE_URL` — constant — internal
- `logger` — constant — pub

`openai_provider.py`
- `ENV_API_KEY` — constant — pub
- `ENV_BASE_URL` — constant — pub
- `ENV_DEBUG_RAW` — constant — pub
- `ENV_MODEL` — constant — pub
- `ENV_REASONING_SENTINEL` — constant — pub
- `FINAL_ANSWER_MARKER` — constant — pub
- `OpenAICompatibleProvider` — class — pub — LLM provider for OpenAI-compatible chat completion APIs.
  - `__init__` — constructor — internal
  - `call_count` — property — pub
  - `complete` — method — pub — Send a prompt to {base_url}/chat/completions. Returns the assistant content.
  - `last_call_stats` — property — pub
  - `model` — property — pub
- `REASONING_SENTINEL_ENABLED` — constant — pub
- `_DEFAULT_TIMEOUT` — constant — internal
- `_THINK_BLOCK_RE` — constant — internal
- `_THINK_CLOSE_RE` — constant — internal
- `_THINK_TAGS` — constant — internal
- `_strip_reasoning` — function — internal — Remove reasoning from model output. Handles (1) a prompt-driven
- `logger` — constant — pub
<!-- END:STRUCTURE -->

**Depends on**
None at runtime (the `LLMProvider` Protocol + the three provider implementations are stdlib-only). The debug CLI (`llm_debug.py`) additionally imports [env](../env/MODULE.md) (`resolve_llm_provider` / `resolve_llm_model` / `resolve_llm_timeout`) and [pipeline](../pipeline/MODULE.md) (`PipelineContext.create_llm_provider`) at debug time — `--check` / `--complete` need the resolver chain to discover the active provider. Runtime callers don't pay this cost.

**Depended on by**
[taxonomy](../taxonomy/MODULE.md), [query](../query/MODULE.md), [eval](../eval/MODULE.md) (and transitively [pipeline](../pipeline/MODULE.md)).
