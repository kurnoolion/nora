# llm-model-discovery

**Status:** in-flight
**Opened:** 2026-09-17
**Landed:**
**Assignees:** Hanif
**Target modules:** env, llm, web
**Active phase:**

## Summary

Let the Ask page choose a model within a roster provider. A provider entry keeps
its configured `model` as the default. The model list is discovered server-side
from the provider's `GET /v1/models`, cached in memory for
`model_discovery_ttl_s` (default 6h, top-level in `llm.json`), and the last good
list is kept if a refresh fails. The Ask page gets a Model dropdown between
Provider and Fast/Think. Fast/Think stays a provider setting that applies to all
its models. The submitted model is checked on the server against the discovered
list plus the default. Eval, per-model reasoning overrides, a refresh button and
the Config page are out of scope; `golden_cli --llm-model` already covers eval.
Driver: manager feedback to choose the model per question on the internal
endpoint, which serves several models.

## Notes

