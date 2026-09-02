# Draft decisions — llm-model-choice-eval

Drafts for `/close-session` triage; promoted to canonical `DECISIONS.md` at
`/land-strand` time. Numbering restarts here — this was drafted as D-DRAFT-5
under the parent `llm-model-choice` strand before Phase 2 was split out.

## D-DRAFT-1 — Reasoning effort is recorded on eval runs, not enforced as a comparability key

**Context.** `golden_cli --reasoning` varies how Stage-2 synthesis generates.
`StackStamp` carries comparability keys — `stage1_key()` / `stage2_key()` — and
two runs are treated as comparable when those keys match. Reasoning effort
plainly changes generation behaviour, so the question was whether it belongs in
the key.

**Decision (Hanif, 2026-09-01).** No. `reasoning_effort` is recorded on the
stamp, carried into `to_dict()`, and printed as `rsn=` on the GEV `id:` line,
but is deliberately kept OUT of both keys. Two runs at different levels still
pool.

**Why.** "We shouldn't stop anyone from comparing." The tool's job is to record
and surface what produced a run; deciding which runs are legitimately
comparable is the analyst's call. A hard gate would refuse comparisons a human
knows are meaningful.

**Alternatives considered.**
- *Add it to `stage2_key()`* — argued for on the grounds that pooling runs at
  different levels silently mixes conditions. Rejected as too restrictive; it
  would also have changed the key's tuple shape, breaking comparison against
  every historical run unless backfilled.
- *Fold it into `llm_identity`* (e.g. `model@none`) — would have preserved the
  tuple shape while still gating. Rejected for the same reason: it gates.

**Consequences accepted.**
- The printed `rsn=` line is the ONLY place two otherwise-identical runs at
  different levels visibly differ. That makes the line load-bearing, so tests
  assert both halves: that the keys stay equal, and that `rsn=` appears when a
  level was set. Unset leaves the line byte-identical to before.
- An analyst pooling runs without reading the `id:` line can mix conditions
  without the tool objecting. That is the deliberate trade.
