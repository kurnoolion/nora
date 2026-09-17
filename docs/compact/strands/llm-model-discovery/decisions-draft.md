## D-DRAFT-1 — A roster provider offers every model its `/v1/models` lists; entry `model` becomes the default

**Context.** Manager feedback: let the asker choose the model per question. The
internal OpenAI-compatible endpoint serves several models. D-216 made each roster
entry exactly one endpoint + one model, and the `llm-model-choice` plan (§6)
deferred model choice until an endpoint served several.

**Decision (Hanif, 2026-09-17).** The Ask page gets a Model select filled from
the selected provider's `GET {base_url}/models`. The entry keeps `model`, now
documented as that provider's default: preselected, used when discovery fails,
and used by the fallback provider and the cached cold-start pipeline.

**Why.** Discovery keeps the list current without roster edits ("less coupling in
the configuration"). Keeping `model` means existing rosters load unchanged and
fallback/default resolution never needs a network call.

**Rejected alternatives.**
- *One roster entry per model* (same `base_url`, different `model`). Works today
  with zero code, but the list is hand-maintained and drifts from the endpoint.
- *`model` optional, default = first listed.* Makes the default an accident of
  server list order — the same accident D-225 removed for providers.
- *`model` removed.* Fallback and default resolution would each need a fetch.

**Consequences.** Relaxes D-216's one-model-per-entry shape for the Ask flow only.
Eval, pipeline, taxonomy and Eval Studio curation are unchanged.

## D-DRAFT-2 — Fast/Think capability stays declared per provider, not per model

**Context.** `supports_reasoning_control` / `reasoning_control` are declared per
entry (D-216, D-235). One entry can now answer with several models.

**Decision.** The provider's declaration applies to every model on it.

**Why.** Minimal config, which was the point of discovery.

**Rejected alternatives.** A per-model override map (more config to maintain);
detection (D-216 already showed an endpoint can accept `reasoning_effort` and
silently ignore it).

**Consequences.** If one model on an endpoint ignores the "don't think" wire
form, Fast silently does nothing for that model.

## D-DRAFT-3 — Discovery is cached in-process for `model_discovery_ttl_s` (default 6h); a failed refresh keeps the last good list

**Decision.** `web/model_discovery.py` caches per provider id. Fresh → cached;
stale → refetch; failure keeps the previous list (logged) and still stamps the
time, so a down endpoint is retried once per TTL, not per page load. TTL is one
top-level `llm.json` key, file-only like the roster.

**Rejected alternatives.** Fetch once at startup (stale until restart, empty if
the endpoint was down at boot); fetch on every page load (every Ask page waits
on every endpoint); per-provider TTL or Config-page knob (no stated need).

**Consequences.** A model added on the endpoint appears after the TTL or a
`nora-web` restart. No refresh button.

## D-DRAFT-4 — The server honours a submitted model only if the cache lists it; the answer path never fetches

**Decision.** `_build_llm_from_env_or_default` uses the form's `model` only when
it is the entry default or in `allowed_models(entry)` (cache read); anything else
degrades to the entry default with a warning, as an unknown provider id does.

**Why.** A hand-edited form must not send arbitrary model names to the internal
endpoint, and the answer path must not block on discovery.

**Consequences.** Right after a restart, before the page has fetched the list,
only the default is accepted — the page fetches on load, so this is not
user-visible in practice.

## D-DRAFT-5 — The Model list is shown unfiltered

**Decision (Hanif, 2026-09-17).** Show every model the endpoint lists; no
heuristic filtering of embedding/reranker models.

**Rejected alternative.** Name-based filtering — NORA would be guessing from
model names. The internal vLLM endpoint is expected to list chat models only.

**Consequences.** On an endpoint that also serves embedding/reranker models
(e.g. a local Ollama), picking one fails the question.

## D-DRAFT-6 — Model choice is not carried into eval

**Decision (Hanif, 2026-09-17).** Ask page only. `golden_cli` is unchanged.

**Why.** Eval has no roster, and `golden_cli --llm-model` already switches the
model on the configured endpoint. A `--provider` flag would also have raised
judge-drift questions (the judge follows the synthesis LLM unless named).
