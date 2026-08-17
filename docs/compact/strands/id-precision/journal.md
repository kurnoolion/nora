## 2026-08-17 — Guard built, field-corrected twice, field-complete in one day

### Done this session
- Strand opened (four items from table-fidelity's pre-landing audit) and
  worked to field-complete across three validation rounds.
- Over-capture guard at every parser req-id capture seam, evolved
  through field correction: v1 (per-capture recovery + fixed 3-word
  bound) rejected ~220 legitimate multi-word ids per cell and risked
  truncation → v2 bounds-first (in-bound captures pass untouched; word
  bound = profile knob `requirement_id.guard_max_words`, default 6,
  MNO-A pinned 5) fixed all collateral but proved no word bound can
  catch a weld narrower than the widest legitimate id → v3 containment
  trigger (a capture whose prefix and suffix slices both fullmatch
  holds ≥2 complete ids; recovers bounds-independently; 0 false
  positives measured on a full cell inventory).
- Recall checker imports the same guard function and profile knob —
  extract side can no longer over-capture in agreement with the parser.
- `sira_incremental dedup`: newest-wins duplicate-row repair (kept
  trace + enrichments, atomic rewrite, --dry-run/--stage) — closes the
  verify-FAIL-with-no-repair gap; field smoke PASS.
- Field acceptance (round 3): weld caught on both MNO-A cells (ids
  delta −1, added=0), rejected 0, u≤7 inventory intact, TOC pairing
  improved 6→4, MNO-B/MNO-C untouched; A-E + promote clean; served
  corpus has ZERO over-capture-class ids on every cell; recounts
  unchanged (0/77/86/100/94).
- Two new ParseStats counters (req_id_over_captures_recovered /
  req_id_captures_rejected); parser + profiler MODULE.md updated.
- Commits: 5dd9159 (v1 + checker + dedup), aa211d9 (v2 bounds-first +
  knob), 7272c89 (containment).

### In progress
- (nothing — field work complete)

### Next
- Landing proposal → /land-strand id-precision on the architect's approval.
- Runbook line: sandbox-side code changes require rebaking BOTH images
  (sira-batch builds from a separate dockerfile than nora-pipeline).
- On landing notice: pre-guard serve label may be GC'd (data side).

### Flags
- profiler MODULE.md's GENERIC_PLACEHOLDERS line predates the
  hyphen/space class widening (stale vs code) — fix with the next
  profiler MODULE.md touch.
- Known accepted residue: the single-id weld shape (id + prose, one
  complete id, in-bound) passes the guard by design — governed by the
  word bound and profile class discipline, not containment.
