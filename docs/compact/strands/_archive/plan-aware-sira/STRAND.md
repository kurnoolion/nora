# plan-aware-sira

**Status:** landed
**Opened:** 2026-05-24
**Landed:** 2026-08-04
**Assignees:** kurnoolion
**Target modules:** sandbox/adapter, sandbox/sira_query
**Active phase:**

## Summary

Multi-granularity rows in the BEIR adapter (per-doc, per-section, per-requirement) with a fan-out step in the SIRA service to expand doc/section-level matches into their constituent req-level chunks at retrieval time. Targets plan-summarize queries that SIRA's DF-filter design structurally underserves. Decoupled matching (doc/section-level rows containing req_id pointers) from content payload (req-level chunks) — sidesteps BM25 length-norm penalty while leveraging strong plan-name matching, and preserves req-level citation through the synthesizer.

## Notes

Landed on 2026-08-04 with 1 promoted decision: D-182 (incremental enrichment). Drafts 1, 3, 5 dropped as superseded/absorbed (pointer rows + fan-out already canonical as D-090; patches-not-fork as D-155; temperature knob shipped in code + /healthz). Draft 4 (no non-deterministic LLM in ranking path) dropped as absorbed into the D-103 LLM-select posture and deliberately relaxed by D-105/D-116/D-118 fusion. Unshipped residue — fan-out query-type routing — carried to strand nora-retrieval-quality (journal 2026-08-04).

