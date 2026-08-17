# id-precision

**Status:** in-flight
**Opened:** 2026-08-17
**Landed:**
**Assignees:** kurnoolion
**Target modules:** profiler, parser
**Active phase:** development

## Summary

Close the id-extraction precision hazard class surfaced by
table-fidelity's pre-landing audit: fix the MNO-A rescue-path weld
(1 corrupted id per cell, measured); add a core-side leading-anchor
over-capture guard (length/word-count bound) so no profile pattern can
silently reproduce the MNO-B defect; align the recall checker so it
cannot over-capture in agreement with the parser. Also folds in the
audit's tooling gap: a dedup repair command for duplicate kept rows —
`verify-run --strict` can FAIL on a state no existing repair command
addresses (heal-torn doesn't dedup, prune skips unchanged docs,
retry-failed only evicts failures), encoding the field-proven manual
procedure (keep-newest, both files, temp+atomic-rename).

## Notes

<!-- appended to over the strand's lifetime -->
