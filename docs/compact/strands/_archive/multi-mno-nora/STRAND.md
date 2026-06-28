# multi-mno-nora

**Status:** landed
**Opened:** 2026-06-13
**Landed:** 2026-06-28
**Assignees:** kurnoolion
**Target modules:** parser, profiler, extraction, adapter
**Active phase:** development

## Summary

Extend NORA's extract → profile → parse pipeline for multi-MNO/multi-release.
Onboard new MNOs (starting with MNO-B) whose requirements arrive as a single
PDF with sections-as-plans — which the current one-plan-per-document model
collapses. Promote plan to a per-requirement attribute (req_ids encode it;
profile-configured extraction) so one document yields N plans, keeping the
one-tree-per-document invariant. `graph` is a touched consumer (FR-7 plan
organization).

## Notes

**Landing gate (2026-06-14):** do **not** `/land-strand` until multiple MNO
releases have been ingested and the multi-plan / leading-id path verified on
real corpora. D-DRAFT-1 / D-DRAFT-2 stay as drafts until then.

**MNO-B parsing spec:** the complete, authoritative observations + parsing rules
for the MNO-B corpus live in [`mno-b-spec.md`](mno-b-spec.md) — read it at
session start before doing profile/parser work.

Landed on 2026-06-28 with 14 promoted decisions: D-091, D-092, D-093, D-094,
D-095, D-096, D-097, D-098, D-099, D-100, D-101, D-102, D-103, D-104.

**Scope at landing:** the strand's ingestion mission (extract → profile → parse
→ corpus) is complete; the unsolved NORA query-side **retrieval** problem was
carved out to strand `nora-retrieval-quality` before landing. select-synth
(D-103) and the reasoning sentinel (D-104) landed here — sound/sira-verified;
their nora-lane *verification* is gated on retrieval, tracked in the successor.

**Draft → canonical mapping** (for the relocated drafts' cross-refs in
`nora-retrieval-quality`): D-DRAFT-1→D-091, 2→D-092, 3→D-093, 4→D-094, 5→D-095,
6→D-096, 7→D-097, 8→D-098, 9→D-099, 10→D-100, 12→D-101, 13→D-102, 14→D-103,
17→D-104. Relocated (NOT landed here): D-DRAFT-11 → `nora-retrieval-quality`
D-DRAFT-1; D-DRAFT-16 → `nora-retrieval-quality` D-DRAFT-2.
