## 2026-09-03 — Deployable roster location (#18) + roster semantics (#19)

### Done this session
- Branched `llm-roster-deploy` off `main` (c1a6d8c); scaffolded and bound the
  strand. Verified every location the manager brief cited before building —
  line numbers had drifted 2-7, nothing material.
- **Four places the brief and the code disagreed**, all resolved in `plan.md`:
  item 8 conflicted with itself (reuse the env-var-driven wrap AND take the
  endpoint from the roster); item 8 also reversed a deliberate in-code decision;
  item 4 named `/healthz`, which does not exist on the NORA web app (it is
  `/api/health`; the `/healthz` calls hit the external SIRA service); MODULE.md
  scope was three files, not one.
- **Section A (#18), commit 99a2dd1.** `NORA_LLM_CONFIG` selects the config
  file; `DEFAULT_LLM_CONFIG_PATH` becomes the fallback. Bad value warns and
  falls through. `/api/health` gains roster diagnostics — source flag, basename,
  counts, effective provider, per-entry api_key presence.
- **Section B (#19), commit 48e8470.** `default_provider` / `fallback_provider`;
  roster entry independent of the Config-page DB; per-entry timeout; refusal
  fallback restored on the roster path with an explicit `fallback=` parameter;
  reroute disclosed through the existing synthesis epilogue; `use_roster=False`
  for Eval Studio curation; Config-page labels.
- Ran `/karpathy-guidelines` on the plan before building. It caught two real
  things: B6's "which fields does the DB win on" had been picked silently (that
  surfacing is what exposed the DECIDED-item conflict), and A4's admin-gating
  was more machinery than needed.
- Tests call the REAL `_build_llm_from_env_or_default` in both roster and
  no-roster configurations — the coverage gap that let a latent
  UnboundLocalError reach production. 1888 passing.

### Decisions
Nine drafts in `decisions-draft.md`. D-DRAFT-1 is the one that needs the
manager's eye: it reverses his DECIDED "roster sits BELOW the Config-page DB".

### In progress
- Nothing — pending commit of docs + PR.

### Next
- Push, PR (D-DRAFT-1's reversal leads the body), `/land-strand` after merge.

### Flags
- **A DECIDED item was reversed.** Roster independence was Hanif's call, taken
  knowing the brief forbade relitigating it. If the manager disagrees, the fix
  is small — restore DB precedence in `_build_llm_from_env_or_default`'s roster
  branch — but the label-honesty argument in D-DRAFT-1 goes with it.
- **Structure sections were hand-patched, not regenerated.** The four touched
  modules' mechanical lists total 992 lines; three entries were missing, so the
  entries were inserted in place rather than re-deriving everything. While doing
  it, pre-existing ordering drift showed up in `env/MODULE.md` —
  `resolve_requirements_dir` sits before `resolve_provider`. A real
  `/regen-map` would fix that and may surface more; it is owed.
- Item 4's brief text says `/healthz`. Implemented on `/api/health`, which is
  what exists. Worth confirming with the manager that no separate `/healthz` was
  intended.
- 8 pre-existing test failures, confirmed unrelated by stashing:
  `test_web_config.py` x6, `test_embedding_ollama.py`,
  `test_enrich_overlay_store.py`.
