## 2026-09-17 — Ask-page model discovery: design → 7-task implementation

### Done this session
- Cleared a stale session binding (`llm-roster-docs`, already archived).
- Brainstormed the manager's "choose the model" feedback against the existing
  roster. Found that one-entry-per-model already works with no code; Hanif chose
  discovery instead (less config coupling). Six choices recorded in `plan.md`
  §2; decisions drafted in `decisions-draft.md` (D-DRAFT-1..6).
- Wrote `plan.md` (design) and `implementation-plan.md` (7 TDD tasks), then
  executed them with one implementer + one reviewer subagent per task:
  1. `env`: `model_discovery_ttl_s` (default 21600) + resolver.
  2. `llm`: `list_models()` for `GET {base_url}/models`.
  3. `web/model_discovery.py`: per-provider cache, last-good on failure,
     default always present, per-provider lock.
  4. Route `GET /api/test/providers/{id}/models`; tested through the real app
     with team mode ON (proven meaningful by temporarily removing `/api/test`
     from the allowlist → 302).
  5. `model` threaded through every Ask path; honoured only if cached.
  6. Model select on `/test` + render tests.
  7. `env`/`llm`/`web` MODULE.md + `docker/README.md` operator note.
- Review loops caught: a 404 test that passed with no route; team-mode verified
  only by a path-list unit test; a code comment describing disabling the select
  that the code never did; one implementer's TDD "RED" evidence that was
  restated from the brief rather than run (re-captured for real).
- Browser-verified locally against Ollama's `/v1` (17 models listed; asked with
  `gemma4:e2b`, log confirms `provider=local model=gemma4:e2b`; unreachable
  provider degrades to its default with a warning). Hanif: "looks great".
- Final whole-branch review ("with fixes") → one fix wave, re-reviewed clean:
  `list_models` now also maps `http.client.HTTPException` (e.g. `IncompleteRead`)
  to `RuntimeError` (previously escaped → route 500, no last-good list, refetch
  every load); web MODULE.md Deferred bullet no longer lists `llm`; the Model
  select resets to the selected provider's default while its list loads
  (`data-default-model` on provider options); provider select aria-label is now
  "Which endpoint answers".
- Full suite: 8 failed / 1962 passed / 112 skipped — the 8 failures are
  pre-existing on `main` (test_embedding_ollama ×1, test_enrich_overlay_store ×1,
  test_web_config ×6).

### Known gaps carried forward
- The disambiguation "Synthesize from this group" form posts no provider/mode
  (pre-existing), so it also carries no model.
- No test asserts `model` is threaded through each lane function; the guard is
  the builder-level tests plus a grep check.
