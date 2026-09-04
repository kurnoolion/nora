## 2026-09-03 — Re-running a question: recents, Re-run last, history re-run

### Done this session
- Discovered the idea was HALF ALREADY SHIPPED. Archived strand `ask-history`
  had built a History page with a localStorage question list and unlimited
  retention. What was missing — and missing deliberately — was re-run:
  `shared.html` states outright that opening a stored answer "does not re-run
  the query." Reframed the strand around that gap rather than rebuilding a
  list, and around on-page access instead of a separate page.
- Found the Ask page already persists everything about an ask EXCEPT the
  question (`ask-last-fields`: name, provider, mode, lanes, with the comment
  "the question is always fresh"). Re-run is mostly joining two stores that
  already existed.
- Two-phase recording (0052505): question written at SUBMIT time, enriched
  with share paths when an answer arrives, pending entry REPLACED not
  appended. Errored asks now enter history — previously dropped entirely,
  and they are the ones most worth re-running after a fix.
- Companion change forced by that: `history.html` called
  `entry.path.replace()` unconditionally, so the first errored ask would have
  broken the page with a TypeError. Pathless entries render unlinked with a
  "no saved answer" marker.
- Recents dropdown + Re-run last (9e467e8), deduped by question text,
  display-only cap of 20 over an unlimited store.
- Share button fix (f529bd5): the Edge "dialog with an OK button" was our own
  `window.prompt` fallback, firing because `navigator.clipboard` exists only
  in a secure context. Confirmed absent on the LAN address and present on
  127.0.0.1 — nothing Edge-specific. Also de-duplicated the copy logic, which
  existed twice with the same prompt fallback.
- History-page re-run (b90fa2f) via a one-shot localStorage baton.

### Machine configuration (outside the repo, not committed)
- `~/.nora/llm.json` — 3-entry local Ollama roster, so the provider picker and
  Fast/Think toggle are the default on this PC. `supports_reasoning_control`
  was VERIFIED empirically per D-216 rather than assumed: `reasoning_effort:
  "none"` makes the response's `reasoning` field disappear for qwen3 and
  deepseek-r1 and changes nothing for gemma4, so gemma4 is declared false.
- `NORA_LLM_CONFIG` plus the three older `NORA_LLM_*` vars now live in
  `~/.zshenv`, not `~/.zshrc`, so non-interactive shells (scripts, cron, agent
  tooling) get them too.

### In progress
- Nothing — pending PR.

### Next
- PR, then `/land-strand ask-rerun` after merge.

### Flags
- **The Share copy itself is UNVERIFIED.** Both `clipboard.writeText` and
  `execCommand` need transient user activation, which the automation cannot
  synthesize — a programmatic call fails even where the API is present, and a
  driven mouse click did not register the handler. Diagnosis, prompt removal
  and dedup are verified; the copy needs a human click in Edge. If Edge is
  reached over localhost there, the diagnosis is WRONG and this needs redoing.
- **A defect on `main`, not from this strand:** `NORA_LLM_CONFIG` (#18)
  outranks `DEFAULT_LLM_CONFIG_PATH`, silently defeating 9 `test_env_config.py`
  tests that isolate via that path. Green in CI and for anyone who has not
  adopted a roster; red for whoever adopts it first. Fixed here (dcd2332) and
  called out in the PR; the fix belongs on `main`.
- **Process miss:** the branch was pushed before `/close-session` ran. Caught
  by Hanif. The journal and decisions below were written after the push —
  no PR was opened first, so nothing merged undocumented.
- **I killed a server that was not mine:** `pkill -f "core.src.web.app"`
  matched a NORA instance on `--port 8010`. Match on the port next time.
- 8 pre-existing suite failures, unchanged.
