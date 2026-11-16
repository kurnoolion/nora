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
