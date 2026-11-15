## 2026-11-14 — Early candidates, Stop, and the SIRA card tabs (#30)

### Done this session
- Early SIRA candidates: once the store returns, a `partial` shows "Searched: N candidates (the answer cites from these)" as the same tiles the card uses (`_sira_candidates.html`).
- Stop: SIRA asks get a `run_id` (History's cancel registry); Stop posts it to the unchanged `/api/test/ask-cancel`. The page shows the stopped view at once; the server stops the model in the background.
- Model-call cancel: `llm.openai_provider.CANCEL` (context variable) + socket shutdown → `LLMCancelled`. Verified live against Ollama /v1 (`context canceled` 40 ms after close).
- Stopped row: answer "_Stopped before the answer._" + `lane_config.stopped`; a late Stop keeps an answer that arrived.
- SIRA card tabs: Answer | Cited by LLM | Search candidates; stopped asks open on candidates.
- Tests: provider loopback cancel, lane, stream with the team gate on, History pane, tabs, ID-lookup and History lanes untouched. Commits 841ca33, f916b71.

### Next
- Push `ask-stream-stop`, open the PR (Closes #30) with the known limits.

### Flags
- A Stop during query expansion does not cancel the store's own expansion call (up to ~11 s on the shared endpoint).
- The Ollama-native provider frees the page, not the model.
- Shared page and History pane show no tabs: stored rows keep no candidates.

## 2026-11-15 — PR #33 review round

### Done this session
- Review 1: SIRA card tabs only in select-synth mode; the default `rerank-pin` card keeps its plain citation list (`1a45fac`).
- Review 2: Stop holds after early response headers (gateway case); `_TrackConns` records sockets at connect, the watcher shuts those (`07adb66`). Loopback test with early headers: 10 s before, <3 s after.
- Review 3: `test_playground_helpers` runs coroutines with `asyncio.run`; the 5 suite-order failures are gone (`d7cbc57`).
- Nits: stream cancels + forgets its run on any early exit (`c1b986e`); tile with no MNO pins the release only (`dde5788`); watcher race gone with review 2; stale `event_stream` comment fixed.
- MODULE.md: web (tabs gate, run cleanup), llm (socket recording). D-DRAFT-6 (sockets recorded at connect), D-DRAFT-7 (an early stream exit cancels the run).

### Next
- Push `ask-stream-stop`; reply on #33 mapping each review item to its commit.
- Whichever of #32 / #33 lands second: keep-both rebase in `web/MODULE.md`, `playground.py`, `MAP.md`.

### Flags
- Full suite: 46 failures in this checkout, identical on `main` (environmental: `test_cycle_driver`, `test_ingested_rows_*` reading the local env). None from this branch.
- The disconnect path of the stream cleanup is covered through the shared `finally` (raising-lane test), not by a disconnect test.
