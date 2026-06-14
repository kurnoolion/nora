# multi-mno-nora

**Status:** in-flight
**Opened:** 2026-06-13
**Landed:**
**Assignees:** kurnoolion
**Target modules:** parser, profiler, extraction, adapter
**Active phase:** architecture

## Summary

Extend NORA's extract → profile → parse pipeline for multi-MNO/multi-release.
Onboard new MNOs (starting with MNO-B) whose requirements arrive as a single
PDF with sections-as-plans — which the current one-plan-per-document model
collapses. Promote plan to a per-requirement attribute (req_ids encode it;
profile-configured extraction) so one document yields N plans, keeping the
one-tree-per-document invariant. `graph` is a touched consumer (FR-7 plan
organization).

## Notes
