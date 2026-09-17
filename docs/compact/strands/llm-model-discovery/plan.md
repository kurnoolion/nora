# Plan — choose the model within a roster provider

**Strand:** llm-model-discovery · **Status:** design, awaiting review · **Date:** 2026-09-17

## 1. Ask

Manager feedback: let the asker choose the model per question. The internal
OpenAI-compatible endpoint serves several models; the DGX and the other box
serve one each. The provider roster (D-216, D-225) already lets the asker choose
the endpoint; this extends the same Ask flow to the model on that endpoint.

`llm-model-choice` plan §6 anticipated this: "If an endpoint later serves
several models, the picker is populated from `GET /v1/models` rather than typed
by hand."

## 2. Decisions taken in discussion

| # | Question | Choice | Rejected |
|---|---|---|---|
| 1 | Curated list vs discovery | Discover from `/v1/models` | One roster entry per model — works today with no code, but the list is hand-maintained |
| 2 | Where Fast/Think capability lives | Per provider, applies to all its models | Per-model override map (more config); detection (D-216: an endpoint can accept `reasoning_effort` and ignore it) |
| 3 | Fate of entry `model` | Kept, becomes the default model | Optional (default = server list order, the accident D-225 removed); removed (fallback/default resolution would need a network call) |
| 4 | When to discover | On provider select, cached server-side | At startup only (stale until restart); every page load (latency on every endpoint) |
| 5 | Cache lifetime | Configurable, default 6h | — |
| 6 | Scope | Ask page only | Eval `--provider` flag — eval has no roster and `golden_cli --llm-model` already switches models on the configured endpoint |

Assumptions stated in discussion and not objected to:
- TTL is one top-level key, not per provider, not on the Config page.
- A failed refresh keeps serving the last good list (logged), rather than
  shrinking the dropdown to the default.
- No refresh button; a restart clears the cache.
- The server checks the submitted model; an unlisted value degrades to the
  entry default with a warning, the same way an unknown provider id does.

## 3. Design

### Config (`env`)

`config/llm.json` gains one top-level key; entries are unchanged:

```json
"model_discovery_ttl_s": 21600
```

- Missing / 0 / invalid → 21600 with a warning on invalid, matching how
  `_parse_providers` degrades rather than raises.
- Resolver `resolve_model_discovery_ttl_s()` alongside `resolve_providers()`,
  same file source (`NORA_LLM_CONFIG` honoured). No env-var or CLI tier — same
  reasoning as the roster itself.
- `LLMProviderEntry.model` keeps its meaning and becomes documented as the
  entry's default model.
- `_comment` in `config/llm.json` names the new key.

### Discovery (`llm`)

`list_models(base_url, api_key, timeout) -> list[str]` next to
`OpenAICompatibleProvider`: `GET {base_url}/models` (roster `base_url` already
ends in `/v1`), Bearer key when set, parses `{"data": [{"id": ...}]}`. Raises on
transport or shape failure — caching policy is the caller's.

`llm_debug._summarize_models` is a truncating display formatter for the probe
CLI; it is left as is rather than refactored into a shared parser.

### Cache (`web`)

Module-level, in-process: `provider_id -> (fetched_at, models)`.

- Fresh (age < TTL) → return cached.
- Stale or absent → call `list_models`. Success replaces the entry. Failure
  keeps a previous list if one exists and logs a warning; with no previous list
  the result is empty and `discovered=false`.
- The entry's configured `model` is always present in the returned list
  (prepended if the server does not list it).
- A lock per provider id so concurrent askers trigger one fetch, not N.

### Route (`web`)

`GET /api/test/providers/{provider_id}/models` →
`{"models": [...], "default": "<entry.model>", "discovered": bool}`.

- Unknown id → 404.
- Under `/api/test`, already on the team-mode allowlist (`web/team_mode.py`);
  verified with `NORA_WEB_TEAM_MODE=1`, not only ungated.

### Ask page (`web`)

`templates/test/index.html`: a `name="model"` select between Provider and
Fast/Think, rendered only when a roster exists.

- On provider change (and on load): fetch the route, fill the select,
  preselect `default`. `discovered=false` → a quiet note that the list is the
  configured default only.
- While loading, the select is disabled showing the default, so a fast submit
  still sends a valid model.
- localStorage restore extends the existing `last.provider` / `last.mode`
  pattern: restore `last.model` only if the fetched list offers it.
- Re-run goes through `form.requestSubmit()` (D-243), so it picks up the model
  with no extra code.

### Answering (`web`)

- `playground.py` reads `model` from the form next to `_form_provider`, and
  threads it to `_build_llm_from_env_or_default(provider_id, mode, model)`
  for both lanes.
- There, `model` is honoured only if it is the entry default or in that
  provider's cached list; otherwise the entry default is used and a warning
  logged. The check reads the cache, never triggers a fetch on the answer path.
- The fallback provider keeps its own entry default model.
- Provenance: the answering model is already recorded (`lane_config`,
  `llm_model`); the recorded value becomes the chosen model. No migration.

## 4. Tests

- `test_env_config.py`: TTL key default / explicit / invalid; entry `model` unchanged.
- `test_openai_provider.py`: `list_models` parses the OpenAI shape, sends the key, raises on
  non-200 / bad JSON (stubbed HTTP, no network).
- `test_web_playground.py`: cache fresh / stale / failure-keeps-previous / default always
  present / one fetch under concurrency; route 200 + 404; submit with a listed
  model, an unlisted model (degrades to default), no model (default).
- `test_team_mode.py`: the route is reachable with team mode ON.
- Full `pytest core/tests/` before done; manual check at
  `http://127.0.0.1:8000/test` against `~/work/env_demo`.

## 5. Docs and contracts shipped on this branch

- `core/src/env/MODULE.md`, `core/src/llm/MODULE.md`, `core/src/web/MODULE.md`
  — new resolver, `list_models`, route, form field.
- `config/llm.json` `_comment`; operator note beside the existing
  `NORA_LLM_CONFIG` docs.
- `decisions-draft.md` via `/close-session`: D-216's one-model-per-entry
  relaxed; entry `model` = default; last-good list on refresh failure; server
  check of the submitted model; eval deliberately excluded.

## 6. Non-goals

- Eval / golden_cli changes.
- Per-model Fast/Think capability or wire form.
- Refresh button, Config-page editing of the TTL, persistent cache.
- Ollama native `/api/tags` discovery — the roster is OpenAI-compatible only.
- `LLMProvider` Protocol change.
