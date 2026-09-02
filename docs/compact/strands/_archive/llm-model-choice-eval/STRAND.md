# llm-model-choice-eval

**Status:** landed
**Opened:** 2026-09-01
**Landed:** 2026-09-02
**Assignees:** Hanif
**Target modules:** eval
**Active phase:**

## Summary

Phase 2 of the reasoning work: carry the reasoning control into the golden-eval
lane. `golden_cli --reasoning {none,low,medium,high}` applies to Stage-2
synthesis only — the judge keeps the endpoint default, so the scoring yardstick
stays fixed while generation varies. The level is recorded on `StackStamp` and
printed as `rsn=` on the GEV `id:` line, but deliberately kept out of the
comparability keys.

## Notes

- 2026-09-01: Split out of the `llm-model-choice` strand. That strand covers
  Phase 1 (the Ask page) and lands when PR #11 merges; `/land-strand` archives
  the folder, and in-flight work must not be left pointing at an archived
  strand. The two share `plan.md` in the parent as the roadmap of record.
- Depends on Phase 1: `OpenAICompatibleProvider(reasoning=...)` comes from that
  branch, so this branch is stacked on it and its PR targets
  `llm-model-choice`, not `main`.
- Phase 3 (provider roster + primary/secondary failover) gets its own strand
  when it starts — it should reuse neither of these.
- Landed on 2026-09-02 with 1 promoted decision: D-217.
