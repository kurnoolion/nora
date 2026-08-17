# table-fidelity

**Status:** landed
**Opened:** 2026-08-12
**Landed:** 2026-08-17
**Assignees:** kurnoolion
**Target modules:** extraction, parser
**Active phase:** development

## Summary

Table fidelity follow-ups from field report #2: (1) fix verify_tables' inline checker to recognize HTML-inlined tables (Docling corpora were false-flagged as missing); (2) add a parser guard so a TABLE block with neither html nor grid content stores/inlines nothing; (3) render mno-a DOCX merged-cell tables as HTML in docx_extractor (rowspan/colspan from merged_cells, gated on merges present) so merged structure survives into the corpus. Contingent scope: mno-a anchored-node empty-text claim, pending recheck on a freshly built cell.

## Notes

<!-- appended to over the strand's lifetime -->

Landed on 2026-08-17 with 4 promoted decisions: D-198, D-199, D-200, D-201
