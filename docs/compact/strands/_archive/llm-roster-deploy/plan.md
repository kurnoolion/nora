# Implementation plan — llm-roster-deploy (#18 + #19)

Source: manager brief, GitHub issue #19 comment 4869854 (2026-09-03). That brief
is authoritative; this file is the verified translation of it into this codebase.
Branch `llm-roster-deploy` off `main` @ c1a6d8c.

## Context

The Ask page has an optional named LLM provider roster (`providers` in
`config/llm.json`). Two problems:

1. `config/llm.json` is committed and baked into the web image, so no deployment
   can supply a roster. Commit 1a3575f reverted an attempt to ship one.
2. When a roster IS present, the roster branch bypasses the Config-page DB and
   loses refusal-fallback coverage.

## Verified against the code

Every location the brief cites exists and behaves as described (line numbers
drifted 2-7 in places). Four things the brief states loosely or not at all:

| # | Finding |
|---|---|
| a | **Item 8 conflicts with itself.** It says reuse `maybe_wrap_with_refusal_fallback` AND source the fallback endpoint from the `fallback_provider` roster entry. That function takes its endpoint only from env vars, via `build_fallback_provider` (`refusal.py:185-190`). Resolved below. |
| b | **Item 8 reverses a deliberate decision.** `query.py:265-267` documents the roster provider as intentionally unwrapped ("silently rerouting would defeat the choice the asker just made"). Item 9's disclosure is what answers that objection. The docstring is rewritten in this change, and the reversal is recorded as a decision. |
| c | **Item 4 names an endpoint that does not exist.** NORA's web app has `/api/health` (`app.py:295`), not `/healthz`; the `/healthz` refs in `playground.py` fetch the external SIRA service. Target is `/api/health`. |
| d | **MODULE.md scope is wider than stated.** The brief names `core/src/env/MODULE.md`. `core/src/web/MODULE.md:546` documents `_build_llm_from_env_or_default`, whose signature item 10 changes, and `/api/health` is a web surface. Both ship here. |

## Settled

- Refusal wiring (item 8a): add an optional `fallback=None` parameter to
  `maybe_wrap_with_refusal_fallback`. When supplied it skips
  `build_fallback_provider`. Markers, the idempotence check and the mock guard
  stay in one place, and the roster concept never enters `core/src/llm/`.
  `NORA_LLM_REFUSAL_MARKERS` stays an env var — hand-synced twin of
  `sandbox/llm_refusal.py` across the D-111 boundary.
- Reroute disclosure (item 9): shown on the **answer card**, visible to every
  asker — not buried in the engineering-details fold. A silent reroute is the
  thing being fixed.

## A. Deployable config location (#18)

**A1-A2.** `core/src/env/config.py`
- Add `LLM_CONFIG_PATH_ENV_VAR = "NORA_LLM_CONFIG"` beside the other
  `LLM_*_ENV_VAR` constants (~line 581).
- `LLMConfigFile.load()` (line 167): when `path is None`, consult that env var
  first, else `DEFAULT_LLM_CONFIG_PATH` (line 47). Explicit `path=` arguments —
  every existing test uses them — keep winning, so no test churn.

**A3.** Env var naming a missing or unreadable file: `logger.warning` loudly and
fall through to the default path. Never raise. A typo must not degrade silently
to `providers == []`.

Scope note: the existing `load()` returns `cls()` for a nonexistent DEFAULT path
with no warning, and that stays untouched. A missing `config/llm.json` is the
normal no-roster case, not an error — only the env-var path gets the warning,
because only there did someone explicitly name a file. Adding a warning to the
default case would fire on every deployment that never configures a roster.

**A4.** `/api/health` (`app.py:295`) gains roster diagnostics. Note this is
`/api/health`, not `/healthz` — see finding (c). It is a JSON endpoint with no
UI; its audience is an operator running `curl` to find out why a deployment
picked up the wrong roster file. Its only current consumer is the navbar status
dot (`app.js` `refreshStatus()`), which reads `status` and `ollama` and ignores
anything new.

New keys:
- `llm_config_source`: `"env"` | `"default"` — was `NORA_LLM_CONFIG` honoured?
- `llm_config_file`: basename only, e.g. `"llm.json"`.
- `roster_size`: int.
- `effective_provider`: the resolved default entry id, or `""`.
- `roster_keys`: `[{"id": ..., "api_key": "set" | "unset"}]` — PRESENCE only,
  never the value. This is what makes B6's env-var-only credential path
  debuggable; without it a missing `api_key_env` export is invisible until the
  endpoint 401s.

No absolute path is emitted, so no admin gating and no `request: Request`
injection is needed — `/api/health` is team-allowlisted (`team_mode.py:31`) and
a basename plus counts leak nothing a gated user should not see.

## B. Roster semantics (#19)

**B5.** `default_provider` / `fallback_provider` — new top-level keys in
`llm.json`, ids into `providers`.
- Parse in `LLMConfigFile.load()`; validate against parsed provider ids in
  `_parse_providers`' style — unknown id warns and degrades, never raises.
- `resolve_provider(None)` (line 758) returns the `default_provider` entry
  instead of `providers[0]`. Falls back to `providers[0]` when the key is
  absent, preserving today's behavior for existing rosters.
- **Behavior change to state plainly:** `query.py:533` (cached pipeline default)
  resolves through `resolve_provider(None)`, so which endpoint the cached
  default uses changes when `default_provider` is set. Intended.

**B6.** Roster entry is INDEPENDENT of the Config-page DB.

**This reverses a DECIDED item in the brief.** Item 6 says "Config-page DB
values win over the roster entry"; the DECIDED section says "Roster sits BELOW
the Config-page DB — do not relitigate." Hanif took the call on 2026-09-03: a
selected roster entry owns its own configuration end to end, so a named endpoint
always means that endpoint. Goes in the FIRST section of the PR body, not only
in decisions-draft.md, so the manager sees it before merge.

Implementation is a deletion, not an addition: the roster branch currently
consults the DB for `llm_timeout` (`query.py:288`). That read is removed; B7
supplies the timeout from the entry. Nothing else in the roster branch reads the
DB today, so no override plumbing is added.

Consequence to handle, not to accept silently: `LLMProviderEntry.api_key` is a
property over `os.getenv(self.api_key_env)` (`config.py:82-84`) — there is no
key field in the file, by design. With independence the Config page can never
supply a roster key either, so a deployment must ship the roster file AND export
every `api_key_env` it names. A missing var yields `""` -> `None` -> a probable
401 from the endpoint with nothing in NORA explaining it. Mitigated in A4 by
reporting per-entry key PRESENCE (never the value).

**B7.** Move `timeout` into `LLMProviderEntry` (`config.py:51`) — a 130B
endpoint and a small one do not want the same ceiling.

Unset falls back to `DEFAULT_LLM_TIMEOUT` (600), NOT to `resolve_llm_timeout()`.
Falling back to the resolver would reintroduce the DB and `NORA_LLM_TIMEOUT`
tiers that B6 just removed, leaving the entry independent for every field except
this one. Nothing depends on the old behavior: no deployment can ship a roster
today, which is the bug #18 fixes.

**B8.** Refusal wrap on the roster path.
- `core/src/llm/refusal.py`: `maybe_wrap_with_refusal_fallback(llm, timeout=600,
  fallback=None)`.
- `query.py:297-304`: build the fallback provider from the `fallback_provider`
  roster entry, pass it in. Rewrite the docstring at `query.py:265-267` — it
  currently documents the opposite intent.

**B9.** Surface the endpoint that answered. `llm_provider_id` is already
recorded (`playground.py:310`). Add the *answering* endpoint, which differs from
the *chosen* one after a reroute; `RefusalFallbackProvider.last_model` already
tracks it. Render on the answer card.

**B10.** `core/src/web/routes/golden_eval.py:589` (Eval Studio curation chat)
bypasses the roster: `_build_llm_from_env_or_default(use_roster=False)`, a new
keyword-only param defaulting `True`. Passing no `provider_id` will not work —
after B5 `resolve_provider(None)` returns the default entry.
`query.py:533` STAYS on the roster: it is the Ask flow's cached default, which
`query.py:669` overrides per request.

**B11.** `core/src/web/config_schema.py:57-88` — extend the `help` text on
`llm_provider` / `llm_model` / `llm_base_url` / `llm_api_key` to say these do
not affect Ask synthesis when a roster is configured.

## Non-goals (from the brief)

- Do NOT unify the chain into the roster, or deprecate `llm_provider` /
  `llm_model` / `llm_base_url` / `llm_api_key`. Pipeline, taxonomy,
  profile_miner, llm_debug and eval depend on them.
- No Config-page form for provider selection. No eval / `golden_cli` changes
  (#20). No hot reload — config is cached per process
  (`_LLM_CONFIG_CACHE`, `config.py:205-212`); adding an endpoint means editing
  the file and restarting.
- Do NOT commit real endpoints into `config/llm.json`.

## MODULE.md (ships in this branch)

- `core/src/env/MODULE.md` — `DEFAULT_LLM_CONFIG_PATH` and `resolve_provider()`
  both change meaning; `LLMProviderEntry` gains `timeout`; new
  `default_provider` / `fallback_provider` keys.
- `core/src/web/MODULE.md:546` — `_build_llm_from_env_or_default` signature
  gains `use_roster`; `/api/health` response keys.
- `core/src/llm/MODULE.md` — `maybe_wrap_with_refusal_fallback` signature.

## Tests

**The known gap.** Three references to `_build_llm_from_env_or_default` exist.
`test_ask_reasoning.py:308,319` already call the real function (the
post-UnboundLocalError regressions). `test_web_eval_studio.py:317,339` and
`test_playground_helpers.py:646` patch it away with `lambda: _FakeLLM()` — and
that is how a latent `UnboundLocalError` on the no-roster path reached
production. Those two eval-studio sites patch exactly the seam B10 changes.

Every new test calls the REAL function in BOTH roster and no-roster configs.
`test_env_config.py:86-91` already has the pattern — write a temp `llm.json`,
point the resolver at it, `_reset_llm_config_cache()` before and after. Reuse it
rather than inventing a fixture.

Cases:
1. Env var beats the default path.
2. Env var unset = behavior unchanged.
3. Env var naming a missing file warns and falls through (not `providers == []`).
4. `default_provider` chosen over positional-first; absent key keeps positional.
5. Unknown `default_provider` / `fallback_provider` id warns and degrades.
6. DB values do NOT override a selected roster entry (independence).
7. Per-entry timeout used; unset falls back to DEFAULT_LLM_TIMEOUT (600), not
   to the DB or NORA_LLM_TIMEOUT.
8. Refusal wrap ACTIVE on the roster path, fallback endpoint from
   `fallback_provider`.
9. `use_roster=False` ignores a configured roster.
10. Answering endpoint recorded and differs from the chosen one after a reroute.
11. `/api/health` reports api_key presence per entry, and never the value.

Suites: `core/tests/test_env_config.py`, `test_ask_reasoning.py`,
`test_web_config_db.py`.

## Build order (each gate passes before the next step starts)

1. A1-A2 env var + `load()` resolution → verify: new tests 1-2 pass; full
   `test_env_config.py` green; no-roster behavior unchanged.
2. A3 missing-file warning → verify: test 3 asserts the warning fires AND that
   the roster is not silently empty.
3. A4 `/api/health` keys → verify: test with the gate ON and OFF; admin sees the
   path, gated user does not.
4. B5 `default_provider` / `fallback_provider` → verify: tests 4-5; confirm the
   `query.py:533` cached-default change is the only behavior delta.
5. B7 per-entry timeout → verify: test 7 (set and unset).
6. B6 roster independent of DB (remove the DB timeout read) → verify: test 6
   calls the real function in both configs and asserts DB values do NOT reach a
   selected roster entry.
7. B8 refusal wrap + docstring rewrite → verify: test 8; assert the wrap is
   active on the roster path, which nothing asserts today.
8. B9 answering-endpoint disclosure → verify: test 10; manual check on the
   answer card.
9. B10 `use_roster=False` → verify: test 9; the two `test_web_eval_studio.py`
   monkeypatch sites moved to (or paired with) real-function tests.
10. B11 help text + all three MODULE.md → verify: `/close-session` audit clean.

## Verification

1. Full suite. Baseline on `main` is 8 pre-existing failures
   (`test_web_config.py` x6, `test_embedding_ollama.py`,
   `test_enrich_overlay_store.py`) — confirm the count does not grow.
2. Any new/changed route checked against `team_mode.py:31-46` and verified with
   the gate **ON**, not only in an ungated dev run.
3. Manual: run with `NORA_LLM_CONFIG` pointing at a temp roster; confirm
   `/api/health` reports the resolved path, roster length and effective
   provider; ask a question and confirm the answer card names the answering
   endpoint.
4. No-roster run: confirm behavior is byte-identical to `main`.

## Decisions to draft (decisions-draft.md, per CLAUDE.md)

1. **Roster entry is independent of the Config-page DB — reverses the brief's
   DECIDED ordering.** Taken by Hanif 2026-09-03 over the manager's "roster
   sits BELOW the Config-page DB": a named endpoint must always mean that
   endpoint, and independence makes the roster label unable to lie. Record the
   consequence (credentials env-var-only) and the mitigation (key presence in
   /api/health). Still an addition scoped to the Ask flow, not a unification of
   the provider chain.
2. Refusal fallback restored on the roster path — reverses the deliberate
   no-wrap choice at `query.py:265-267`; disclosure on the answer card is what
   resolves the original objection.
3. `fallback=` parameter on `maybe_wrap_with_refusal_fallback`, over overrides
   on `build_fallback_provider` or constructing `RefusalFallbackProvider`
   inline. Keeps roster shapes out of `core/src/llm/` and the marker parse in
   one place.
4. Endpoints file-owned, no creation UI, no hot reload.
5. `default_provider` replaces positional-first resolution — changes which
   endpoint the cached pipeline default uses.
6. `/api/health` reports a source flag, basename and counts rather than the
   resolved absolute path, because the endpoint is team-allowlisted — avoids
   both the leak and the admin-gating machinery.
7. Entry `timeout` falls back to DEFAULT_LLM_TIMEOUT rather than the resolver
   chain, so independence holds for every field including this one.
