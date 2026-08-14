# req-recall

**Status:** in-flight
**Opened:** 2026-08-14
**Landed:**
**Assignees:** kurnoolion
**Target modules:** parser, profiler, extraction
**Active phase:** development

## Summary

Requirement-recognition recall: users reported requirements visible in the
source PDFs that never surface in Eval Studio; spot checks confirm they are
real requirements that were not recognized as such during ingestion. Plan:
inventory all valid unique req_ids visible in extract-stage output per cell,
diff against parse-tree requirement nodes (new `sandbox/verify_req_recall.py`
checker), classify each missing id by cause (heading rule mismatch, id-format
variant, table-anchored form, excluded/struck section, extract-stage
misclassification), then fix parser/profiler/profiles accordingly. Sequencing
constraint: ingestion fixes batch with the next enrichment cycle so the
expensive enrichment runs once. Acceptance: the specific user-reported
missing reqs appear post-fix.

## Notes

<!-- appended to over the strand's lifetime -->
