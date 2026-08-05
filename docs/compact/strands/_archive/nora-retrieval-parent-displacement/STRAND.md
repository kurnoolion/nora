# nora-retrieval-parent-displacement

**Status:** abandoned
**Opened:** 2026-05-22
**Landed:**
**Assignees:** kurnoolion
**Target modules:** query
**Active phase:**

## Summary

NORA's retrieval favors heading-only parent chunks over content-bearing children for breadth-style queries (e.g., "What 5G bands shall a device support"). Discovered via the SIRA Test-page probe: NORA prompt 4,171 chars vs SIRA prompt 30,021 chars on the same query. Investigate why parents displace children even when children's headings also match, and ship a fix that doesn't regress the per-type tuning baked in by D-040.

## Notes


Abandoned on 2026-08-04 — folded into strand nora-retrieval-quality (parent-displacement is the leading hypothesis for its single-MNO chunk-miss symptom; see that journal 2026-08-04).
