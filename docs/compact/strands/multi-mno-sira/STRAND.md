# multi-mno-sira

**Status:** in-flight
**Opened:** 2026-06-13
**Landed:**
**Assignees:** kurnoolion
**Target modules:** sandbox/adapter, sandbox/sira_configs, sandbox/sira_query, web
**Active phase:** development

## Summary

Multi-MNO support — extend SIRA to handle multiple US carrier corpora
simultaneously.

## Notes

**Landing gate (2026-06-28):** do **not** `/land-strand` until the **3rd MNO
corpus is ingested AND a 3-way cross-MNO query is verified end-to-end** (e.g.
compare VoWiFi requirements across MNO-A / MNO-B / MNO-C). 2-MNO is verified
end-to-end and working; any >2-MNO fixes are expected to be incremental and
belong in THIS strand's journal — which is why we keep it in-flight rather than
land now and re-open. Land fully after the 3-MNO verification passes.
