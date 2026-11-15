### D-DRAFT-1 — 2026-11-14 — Stop cancels the model call by closing its connection

**Context.**
- Review of #30 asked that Stop free the shared model endpoint, not only the page.
- `OpenAICompatibleProvider.complete()` blocks in a worker thread on `urllib`.

**Decision.**
- `llm.openai_provider.CANCEL`, a context variable holding a `threading.Event`. When set, a watcher shuts the call's socket (connections tracked through a urllib handler mixin) and `complete()` raises `LLMCancelled`.

**Why.**
- OpenAI-compatible servers stop a generation on client disconnect; there is no cancel endpoint.
- Fewest lines; keeps the four-argument Protocol and the module's stdlib-only rule; passes through wrappers (refusal reroute) untouched.
- Rejected:
  - **Streaming (`stream: true`):** the standard chat-UI pattern, but about 50 lines plus an SSE parser.
  - **Async httpx with task cancellation:** breaks the stdlib-only rule.
  - **A `cancel` argument on `complete()`:** widens the Protocol, and every wrapper must forward it.

**Consequences.**
- Only `OpenAICompatibleProvider` cancels; the Ollama-native and proprietary providers free the page only.
- How fast generation stops depends on the server (vLLM and Ollama stop on disconnect; verified live on Ollama /v1).

### D-DRAFT-2 — 2026-11-14 — A stopped ask is marked in lane_config, with the note as its answer

**Context.**
- A stopped row must not read as an empty answer on the History page or in exports.

**Decision.**
- The stored answer is `_Stopped before the answer._` and `lane_config.stopped = true`.

**Why.**
- No schema change, and existing readers show "stopped" without code changes.
- Rejected:
  - **A `stopped` column:** a migration plus a change to every reader.

**Consequences.**
- Filtering stopped rows parses JSON (`json_extract(lane_config, '$.stopped')`).

### D-DRAFT-3 — 2026-11-14 — SIRA Stop reuses the History lane's cancel registry and route

**Decision.**
- SIRA select-synth asks get a `run_id` from `history_lane.new_run_id`; `POST /api/test/ask-cancel` is unchanged; the run is forgotten when the stream ends.

**Why.**
- One registry and one route; the route is already in the team tier, so no allowlist change.
- Rejected:
  - **A separate `/api/test/sira-stop` route and registry.**

**Consequences.**
- The registry lives in `history_lane` but serves both lanes.

### D-DRAFT-4 — 2026-11-14 — The SIRA card shows tabs over one candidate tile view

**Decision.**
- The SIRA card shows Answer | Cited by LLM | Search candidates. `templates/test/_sira_candidates.html` serves the early partial, both tabs and the legacy retrieval view. Cited comes before Candidates (narrow to wide). A stopped ask opens on Search candidates.

**Why.**
- Switching between the answer and the BM25 candidates has to be easy; one tile view keeps them the same before and after the answer.
- Rejected:
  - **Candidates only while waiting:** lost once the answer arrives.
  - **A separate id-list partial:** two views of the same data.

**Consequences.**
- The shared page and the History pane show no tabs: stored rows keep no candidates.
- The plain cited list is replaced on the SIRA card; NORA keeps its list.

### D-DRAFT-5 — 2026-11-14 — Stop is instant on the page; the server finishes in the background

**Context.**
- The first cut showed "Stopping…" until the store returned (about 12 s locally when Stop came during the search).

**Decision.**
- The browser shows the stopped view (note plus the candidates shown) at once and frees Ask. The server's card replaces it when the stream ends. A per-ask sequence number drops a superseded stream's events.

**Why.**
- Stop has to feel instant.
- Rejected:
  - **Racing the store call against the cancel on the server:** more code, and still not instant.

**Consequences.**
- The stopped view lacks the share link and feedback until the server card arrives.

### D-DRAFT-6 — 2026-11-15 — Stop shuts sockets recorded at connect, not `conn.sock`

**Context.**
- PR #33 review: Stop did nothing once response headers had arrived. urllib's `do_open` closes and nulls `conn.sock` after `getresponse()`, and the body is read through `resp.fp`, which the watcher never saw. A gateway that sends headers early held the call ~10 s past Stop.

**Decision.**
- `_TrackConns` wraps each connection's `connect()` and records `conn.sock` the moment it connects; the watcher shuts the recorded sockets. urllib's `close()` only drops a reference while `resp.fp` holds the same socket object, so `shutdown()` still cuts the body read.

**Why.**
- No window between headers and the watcher picking up the response; nothing reaches into urllib's response internals; the watcher never sees `None`, which also removes the check-then-shutdown race.
- Rejected:
  - **Watching `resp.fp` after `_urlopen` returns:** a gap before the watcher sees it, and depends on the private `fp.raw._sock` chain.

**Consequences.**
- Stop holds behind gateways and proxies that send headers early (test: loopback server with early headers).
- The socket list grows with redirects/retries within one call; bounded by the call's lifetime.

### D-DRAFT-7 — 2026-11-15 — An early stream exit cancels the run, then forgets it

**Context.**
- PR #33 review: `forget_run` ran only on the normal path. A closed tab or a lane that raised left the registry entry behind, and the model call ran on.

**Decision.**
- The ask stream's body runs in `try/finally`. A normal finish forgets the run; any early exit (client gone, lane raised) first calls `cancel_run`, then forgets it.

**Why.**
- A closed tab should free the shared model endpoint, the same as pressing Stop.
- Rejected:
  - **Forget only on early exit:** frees the registry but leaves the model call running on the shared endpoint.

**Consequences.**
- Closing the tab mid-answer stops the answer; there is no "finish in the background" for an abandoned stream.
- The History lane gets the same: a closed tab stops new batches.
