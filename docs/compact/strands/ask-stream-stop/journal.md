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
