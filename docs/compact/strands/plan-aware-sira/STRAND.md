# plan-aware-sira

**Status:** in-flight
**Opened:** 2026-05-24
**Landed:**
**Assignees:** kurnoolion
**Target modules:** sandbox/adapter, sandbox/sira_query
**Active phase:**

## Summary

Multi-granularity rows in the BEIR adapter (per-doc, per-section, per-requirement) with a fan-out step in the SIRA service to expand doc/section-level matches into their constituent req-level chunks at retrieval time. Targets plan-summarize queries that SIRA's DF-filter design structurally underserves. Decoupled matching (doc/section-level rows containing req_id pointers) from content payload (req-level chunks) — sidesteps BM25 length-norm penalty while leveraging strong plan-name matching, and preserves req-level citation through the synthesizer.

## Notes

