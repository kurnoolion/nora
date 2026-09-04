# reasoning-wire

**Status:** active
**Opened:** 2026-09-04
**Assignees:** Hanif
**Target modules:** llm, web, env
**Active phase:** development

## Summary

The Ask page's Fast toggle does not skip thinking on either endpoint of the
internal roster. `_reasoning_for` hardcodes `reasoning_effort="none"`, and that
wire form is rejected by both boxes for different reasons. Make the mechanism a
per-roster-entry declaration (`reasoning_control`) so each endpoint gets the
form it actually accepts, without changing what the asker sees.

Related: issue #21, D-216.

## Notes

- 2026-09-04: Constraint carried from D-216 — the Ask page keeps the two-state
  Fast/Think toggle. An effort dropdown was explicitly rejected ("users should
  not have to reason about effort levels", "two states beat five"), and exact
  levels stay in `golden_cli --reasoning`. Only the WIRE VALUE becomes
  per-entry; the UI is untouched.
- 2026-09-04: Probe evidence (measured, both roster endpoints):
  - 32B `http://105.52.91.163`, NOT vLLM (`/v1/models` object is
    `{id, object, owned_by:"local..."}`): `reasoning_effort:"none"` → 400
    `literal_error` (enum is low|medium|high); `"low"` → 200 but still thinks;
    `chat_template_kwargs {"enable_thinking": false}` → 200, content `"Four."`;
    nothing sent → 200 with content opening `"<think>\nOkay, the user is
    asking..."`.
  - 7B `http://105.52.91.247`, real vLLM (`root`/`parent`/`max_model_len`):
    `reasoning_effort` any value → 400 `extra_forbidden` "Extra inputs are not
    permitted"; `chat_template_kwargs` → 200.
  - So `reasoning_effort` works on NEITHER as sent, `chat_template_kwargs`
    works on BOTH. Two boxes, two wire contracts.
- 2026-09-04: The `<think>` block arrives inside `content`, not a separate
  `reasoning_content` field. Display was never broken — `_strip_reasoning`
  already removes it. The cost of unsuppressed thinking is token budget against
  `_SYNTH_MAX_TOKENS` (7500) and latency, which is why "the toggle looks like it
  works" was the failure mode.
- 2026-09-04: Second bug found while implementing, not in the original report.
  The roster lane built its refusal-fallback provider (`query.py`, `fb_entry`
  branch) with **no reasoning argument at all**, so a Fast question that got
  rerouted was answered by a thinking model. The reroute itself is disclosed on
  the answer card; the dropped mode was not. Fixed by resolving the mode
  through the fallback's own entry.
- 2026-09-04: Step 0 from the incoming brief (repair
  `TestExampleConfigStaysValid`, broken by commits `99c1399` + `f0ffc0f`) is a
  **no-op in this clone**. Those commits exist on neither this checkout nor
  `origin` — they are unpushed local work on another machine. Baseline on
  `main` here was 17 failed / 1913 passed, and all 17 are pre-existing failures
  in `test_env_config.py` / `test_web_config.py` unrelated to reasoning. When
  those commits are pushed, the example-config edits in this branch will
  conflict with theirs in `config/llm.json.example` — resolve by keeping the
  real roster's entries and adding `reasoning_control` to each.
