# image-ingestion

**Status:** in-flight
**Opened:** 2026-07-02
**Landed:**
**Assignees:** kurnoolion
**Target modules:** extraction, profiler, parser
**Active phase:**

## Summary

For the MNO-A and MNO-B corpora we used DOCX and pymupdf to extract text and
table content; images were never considered. For MNO-C, pymupdf had trouble
extracting tables reliably, so we moved to a combination of pymupdf and Docling
— pymupdf for text content, Docling for tables (and figure crops + captions),
fused into one IR JSON. Ingesting/fusing image *content* was flagged for future
work; this strand is to do exactly that. Core idea: use an OpenAPI-endpoint-
compatible in-house image analysis model to analyze the extracted images and
convert them to an appropriate format — the majority are flow diagrams (to be
converted to Mermaid diagram format), a few are tables (to be converted to
tables), and UX flows are TBD.

## Notes

<!-- appended to over the strand's lifetime -->
