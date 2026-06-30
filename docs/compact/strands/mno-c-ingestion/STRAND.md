# mno-c-ingestion

**Status:** in-flight
**Opened:** 2026-06-29
**Landed:**
**Assignees:** kurnoolion
**Target modules:** parser, profiler, extraction
**Active phase:** development

## Summary

Onboard MNO-C — whose requirements structure differs from both MNO-A
(heading-anchored) and MNO-B (leading-id body-block) — into the extract →
profile → parse pipeline. Create the MNO-C document profile and, if its
structure needs a detection capability the parser lacks, add a new **generic,
profile-selectable** parser primitive (behavior stays in the profile, not
MNO-specific code — the design rule that kept the landed pipeline clean for A
and B). Lands when MNO-C parses cleanly (correct requirements, IDs, hierarchy)
on the real corpus.

This is a fresh strand because `multi-mno-nora` (the A/B ingestion strand) is
landed/archived and cannot be reopened — its extract→parse capability is now
stable, landed code we run. This strand is a **prerequisite for
`multi-mno-sira`'s landing gate**: it produces correct MNO-C parse output, which
`multi-mno-sira` then turns into a SIRA cell + uses for the 3-way cross-MNO
verification.

## Notes

**Deferred (out of scope for this strand):** referenced-asset ingestion — flow
images + API-spec/flow PDFs that plan requirements reference — designed in
[`asset-ingestion-design.md`](asset-ingestion-design.md). Cross-module capability
(extraction → asset entity → corpus → fan-out retrieval → synthesis → UI vision/
image display); belongs in `references-handling` (or a new `asset-ingestion`
strand) under an architecture-phase pass. XL asset files are ignored.
