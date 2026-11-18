# history-fidelity

**Status:** in-flight
**Opened:** 2026-11-15
**Landed:**
**Assignees:** kurnoolion
**Target modules:** parser, stores, query, web
**Active phase:** development

## Summary

Field review of the History lane's release-to-release diffs found false
Changed, Added and Removed verdicts and wrong totals. Two causes are in
parsing: a PDF extractor that fuses a requirement's heading into the
previous paragraph (the requirement folds into its neighbour), and a
leading-ID rule that opens a requirement on a bare cross-reference line.
One is in the History store: of two nodes with one id, the last wins,
which can be the bare cross-reference. The rest are diff and display:
tables compared by piece count, structural nodes grouped under a blank
plan, and whole-plan additions and removals left out of the totals.
This strand fixes them in two passes: first the store and diff fixes,
which need only a History rebuild; then the parser fixes, behind a dry
count over both MNOs' corpora, which need a re-parse.

## Notes

- 2026-11-15: opened from the field review. Plan: (1) split a block at an
  embedded outline number plus labeled id, behind a dry count; (2) a block
  holding only ids and separators continues the previous requirement;
  (3) duplicate ids keep the occurrence with the most text, logged;
  (4) tables compared by content; (5) structural nodes without a
  requirement id leave plan grouping; (6) whole-plan items count in Added
  and Removed (decided). Order: 3, 5, 6 first; 4; then 1 and 2.
- 2026-11-15: (3) changed: the stores keep the last-occurrence rule; the
  parser counts duplicate ids (`dup_ids`) instead, and (2) removes the
  field's cause. (2), (3), (5), (6) committed in `535aa6f`.
- 2026-11-16: (1) built: heading mode with an id label splits a paragraph
  before an embedded outline number that is the plausible next heading and
  has an id label within heading length (`fused`). Parser CODE_VERSION 3.
  Next: a dry count of `fused`, `id_lists` and `dup_ids` per cell on the
  host before any re-parse; then (4).
- 2026-11-16: the MNO-B duplicates (97-98 per release) are matrix-section
  repeats; the fix is the profile's `exclude_section_pattern`, pending a
  dry parse. That parse showed MNO-B chapter headings (`Chapter N`) were
  never sections: the profile pattern admitted them but the classifier
  re-read the number with a digit-first regex. Fixed: the number comes
  from the profile match. Parser CODE_VERSION 4. Next: dry parse with the
  fix and the exclusion together, then the profile field, then one
  re-ingest of MNO-A and MNO-B.
- 2026-11-16: CODE_VERSION 4 dry parse: MNO-A unchanged; MNO-B gains 54
  chapter sections and the chapter introductions leave the matrix nodes.
  With the MNO-B profile's `exclude_section_pattern` (parent matrix title,
  placeholder `<MATRIX_SECTION_TITLE>`), `dup_ids` fall 98 → 1 and 97 → 2;
  115 matrix-only ids leave both releases; section 74.6 (one introductory
  sentence, same title) is dropped with them. Next: one re-ingest of
  MNO-A and MNO-B, then the field review's question again.
- 2026-11-16: Word-format corpus field feedback, four fixes: one plan per
  document (profile option, parser CODE_VERSION 5, profiler 2); a lane
  reply cut inside an id is retried at twice the cap, then fails its batch
  visibly, cap 8192 in `config/release_diff.json`; heading-only entries
  count as Added / Removed (amends D-294); the model's unchanged-in-meaning
  pairs are listed under Changed. Drafts D-DRAFT-1..3.
- 2026-11-17: deployed. Six cells rebuilt from the version-5 trees (only
  the 150 split-out MNO-A requirements needed new enrichment) and served
  on both stacks; the History, Trees and Cells views now agree. The gate
  failed on single runs for two cells: one was cross-operator scoring
  (the cell's own gold improved), the other the online query expansion,
  whose run-to-run spread is about 0.02 (three-run means: 0.629 rebuilt
  against 0.612 served). Follow-ups: the gate averages several runs or
  replays stored expansions; each cell scored on its own gold; runner
  jobs for the six cells.
- 2026-11-18: (4) built: History's `node_fp` ignores markdown table
  separator rows (`node_text_rule` = `tables-v1`); `text_fp` unchanged.
  Attachment captions (an id plus one file name) continue the open
  requirement in leading-id mode (`file_refs`). Parser CODE_VERSION 6.
- 2026-11-18: the self-serve gate scores the ingest's own cell, over
  three runs (`eval_runs`), against the same cell score of the cell's last
  passed ingest; a cell with no gold passes unscored (D-DRAFT-4, amends
  D-307 and D-345). Next: register runner jobs for the six cells.
- 2026-11-18: scope closed. Every defect from the field review and the
  MNO-A team feedback is fixed and served in `ALL-20261118-1`. Out of
  scope, TBD for a future strand (also in STATUS.md Next): the "N plans
  unchanged" line; the Renumbered and Moved filters; an email-ready
  side-by-side table; a cross-carrier comparison document. Before
  landing: the trial runner job (0526) passes; the reviewers re-check
  on the served release.
