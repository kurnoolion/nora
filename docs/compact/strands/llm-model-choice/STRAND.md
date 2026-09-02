# llm-model-choice

**Status:** in-flight
**Opened:** 2026-09-01
**Landed:**
**Assignees:** Hanif
**Target modules:** llm, web, env, query
**Active phase:**

## Summary

Make LLM provider/model/reasoning a user-selectable choice instead of one
process-wide resolved provider. Driver: RAG synthesis takes ~15s because the
Qwen3 30B endpoint runs in reasoning mode. Settings gets a horizontal provider
roster; ASK page gets per-question model + reasoning-level selection, with
capability declaration so the UI only offers knobs the model supports. Later
extends to eval and enrichment.

## Notes

- 2026-09-01: Deliverable for this phase of the strand is a **plan/proposal
  document to discuss with the manager** — not implementation. Design
  decisions get drafted, not built.
- 2026-09-01: Design constraint — karpathy-guidelines apply to this plan.
  Minimum design that solves the stated problem. No speculative
  configurability, no abstraction for single-use code, no DIY where an
  existing seam (resolver chain, config_db/config_schema, `GET /v1/models`,
  `list_available_ollama_models()`) already does the job. Every proposed
  change must trace to the ask: pick model + reasoning per question.
- 2026-09-01: Plan published as a private artifact —
  https://claude.ai/code/artifact/10e4b8ba-947c-4eb5-9dc9-44b469d7113b
  Source of record is `plan.md`; `plan.html` is its rendering and the file to
  republish to that same URL (pass the URL as `url`). Edit both together.
