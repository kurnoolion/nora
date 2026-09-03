# llm-roster-deploy

**Status:** in-flight
**Opened:** 2026-09-03
**Landed:**
**Assignees:** Hanif
**Target modules:** env, web, llm
**Active phase:**

## Summary

Make the Ask-page LLM roster deployable and correctly ordered — GitHub #18 + #19,
implemented as one strand per the manager brief of 2026-09-03.

#18: move the `llm.json` location behind a `NORA_LLM_CONFIG` env var so a
deployment can supply a roster that is not baked into the committed web image
(commit 1a3575f reverted an earlier attempt to ship one).

#19: fix roster semantics — explicit `default_provider` / `fallback_provider`
instead of positional-first, Config-page DB above the roster, per-entry timeout,
refusal-fallback coverage restored on the roster path with the answering
endpoint disclosed on the answer card, and the Eval Studio curation chat
bypassing the roster.

The roster stays an ADDITION scoped to the Ask flow. Pipeline, taxonomy,
profile_miner, llm_debug and eval keep the single-provider chain; the
`llm_provider` / `llm_model` / `llm_base_url` / `llm_api_key` keys are not
deprecated. Builds on D-214..D-217 (archived strands llm-model-choice and
llm-model-choice-eval).

## Notes

