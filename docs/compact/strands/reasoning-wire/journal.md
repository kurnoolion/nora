# Journal — reasoning-wire

## 2026-09-04 — Fast toggle did nothing on either endpoint

**Where the bug actually was.** Not in the UI, not in `_strip_reasoning`, and
not in whether the endpoints support reasoning control — they do. It was the
assumption that one wire form fits every OpenAI-compatible server. The codebase
stated that assumption twice, in the `OpenAICompatibleProvider` docstring and in
a comment at the payload build, both claiming vLLM maps `reasoning_effort` to
`enable_thinking` itself. Both endpoints on the roster disagree, and they
disagree differently. That comment even named `chat_template_kwargs` as the
workaround for "older servers" — the fix was promoting the footnote to a
declared per-entry choice.

**Why it looked like it worked.** The `<think>` block comes back inside
`content`, not in a `reasoning_content` field, and `_strip_reasoning` removes it
before display. So the answer always looked right; only latency and token budget
(`_SYNTH_MAX_TOKENS` = 7500) paid for the thinking nobody saw. A failing wire
switch and a working one are visually identical on the Ask page, which is why
this survived until someone timed it.

**Second bug, found while implementing.** The roster lane built its
refusal-fallback provider with no reasoning argument at all. A Fast question
rerouted on a permanent refusal was answered by a thinking model. Fixed by
resolving the mode through the fallback's own entry — deliberately not by
inheriting the primary's mechanism, since the two endpoints reject each other's
form and the reroute path is the worst place to send a request that 400s. See
D-DRAFT-4.

**What the generative test earned.** `test_it_documents_every_entry_field`
derives its expectation from `dataclasses.fields(LLMProviderEntry)`, so adding
`reasoning_control` turned it red immediately and stayed red until
`config/llm.json.example` documented the field. That is the test doing exactly
its job — the example config cannot drift behind the schema. `config/llm.json`
carries the same field list in its `_comment` and has no such test; it was
updated by hand, and that asymmetry is worth remembering.

**Step 0 from the incoming brief was a no-op here.** The brief said commits
`99c1399` + `f0ffc0f` rewrote `config/llm.json.example` with the real roster and
broke `TestExampleConfigStaysValid`. Neither commit exists in this checkout or
on `origin` — they are unpushed local work on another machine. Baseline on
`main` was 17 failed / 1913 passed, all 17 pre-existing in `test_env_config.py`
and `test_web_config.py` and unrelated to reasoning. Verified by stashing the
branch and re-running: same 17. After the change, 17 failed / 1925 passed — same
failures, +12 new tests, no regressions. When those two commits are pushed,
expect a conflict in `config/llm.json.example`: keep their real roster entries
and add `reasoning_control` to each.

**Not changed, deliberately.** `supports_reasoning_control` stays — the Ask
template reads it to render "Not supported", and replacing it would have meant a
migration across the template, every deployed roster, and two pinned tests to
express what the new field expresses beside it (D-DRAFT-5). No new routes, so
nothing to add to the `web/team_mode.py` gate allowlist.

**Verification.** Payload-shape tests, not endpoint calls: the tests build a
provider from an entry and assert on the request body, since `_reasoning_for`'s
return value no longer determines the wire form on its own. Live re-check
against the real boxes is still `python -m core.src.llm.llm_debug --complete
--text 'ping'`.
