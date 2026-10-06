## 2026-09-18 — Section number + plan name in the bubble

### Done this session
- `find_req` returns three more keys — `section_number`, `parent_section`,
  `plan_name` — with the contract documented on the function: `plan_name` is
  primary-plan-only (D-DRAFT-1), `section_number` is routinely empty and
  `parent_section` is its fallback (D-DRAFT-2). Additive; all three call sites
  (`playground.py`, `golden_eval.py` x2) read by key, verified before changing.
- `_req_bubble.html` shows the plan name when there is one and the plan id
  otherwise, plus a "Section N" line that falls back to the parent section and
  disappears when there is nothing to show.
- 9 new tests. RED observed before each change: 4 KeyError on the find_req
  keys, then 3 template assertions. Suite 1971 passed / 8 failed / 112 skipped,
  the 8 being the pre-existing set verified against `main` earlier today.
- Browser-verified "Section 1" rendering in the floating panel against
  env_demo.

### Next
- PR up (branched off `main`, so it does NOT contain the req-bubble-tables
  work); boss merges PR #20 first, then this; then `/land-strand`.

### Flags
- **The plan-name half is unverified and may be a no-op on the real corpus.**
  Every tree in `~/work/env_demo` has an empty tree-level `plan_id` AND
  `plan_name`, with per-req plan ids — the secondary-plan case throughout — so
  locally the bubble correctly keeps showing the plan code and the name path
  never executes. Open question for the work PC: is top-level `plan_name`
  non-empty there, and does top-level `plan_id` match the requirements'
  `plan_id`? If `plan_name` is empty there too, the corpus carries no plan name
  and the remaining work is the profile's `plan_name` regex, not web work.
- Both this branch and `req-bubble-tables` (PR #20) modify
  `_req_bubble.html`, on different lines. Merge #20 first.
- `core/src/web/MODULE.md` Structure section remains stale (carried from the
  previous strand) — `/regen-map` territory.
