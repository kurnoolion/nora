# multi-mno-sira

**Status:** landed
**Opened:** 2026-06-13
**Landed:** 2026-07-03
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

Landing gate satisfied 2026-07-03: 3rd MNO corpus ingested and a 3-way
cross-MNO query verified end-to-end.

Landed on 2026-07-03 with 17 promoted decisions: D-105, D-106, D-107, D-108,
D-109, D-110, D-111, D-112, D-113, D-114, D-115, D-116, D-117, D-118, D-119,
D-120, D-121. Drafts D-DRAFT-6 and D-DRAFT-12 deferred (superseded — by
D-115 and by multi-mno-nora's D-096 respectively); they remain in this
archive's decisions-draft.md.
