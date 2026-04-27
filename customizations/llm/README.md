# customizations/llm/

AI-scaffolded boilerplate for LLM providers that vary per deployment.

## What lives here

- `proprietary_provider.py` — stub for production proprietary on-premise LLMs.
  Default implementation raises `NotImplementedError`; replace with a concrete
  endpoint call when deploying.

## What does NOT live here

- `LLMProvider` Protocol — defined in [`core/src/llm/base.py`](../../core/src/llm/base.py),
  shared infrastructure (D-006).
- `OllamaProvider`, `MockLLMProvider`, `model_picker` — also in `core/src/llm/`.
  Not deployment-specific; AI-generated and stable.

## How to fill in a proprietary provider

1. Open `proprietary_provider.py`.
2. Implement `complete(prompt, system, temperature, max_tokens) -> str` to call
   your deployment's LLM endpoint and return the response text.
3. Optionally implement `last_call_stats` (matching `OllamaProvider`) to feed
   the metrics middleware (NFR-11).
4. Wire the provider in `core/src/pipeline/runner.py::PipelineContext.create_llm_provider()`
   when `model_provider == "proprietary"` (or equivalent config key).

The `LLMProvider` Protocol is structural — no inheritance is needed; only the
method signature must match (NFR-6, D-006).

## References

- D-006: LLMProvider Protocol via structural typing
- D-019: Three-tier code organization (this directory is the AI-scaffolded
  human-completion zone)
- D-020: Bi-directional `core ↔ customizations` dependency
- D-024: `customizations/` initial seeding
- NFR-1: production runs fully on-premise; no external cloud AI on proprietary
  content
- NFR-6: all LLM calls flow through the `LLMProvider` Protocol
