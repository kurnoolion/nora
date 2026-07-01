## 2026-07-01 — MNO-C parsing fixes + Docling layout-provider integration

### Done this session
- Borderless/messy tables: region-scoped numbered-row → general column-gutter →
  landed on TEXT-PRESERVATION (demarcated [TABLE] block, no forced grid).
  Font-gate numbering headings (require_heading_font_for_numbering) stops
  borderless rows becoming false sections.
- Dropped top-of-page requirements → header_footer_margin_mode='pattern_only'
  (OA unchanged on 'blanket' default).
- Dropped bordered table + its text → bbox reserved only for KEPT tables.
- Priority scoping → priority_requires_req_id (marker mined only on req headings).
- Layout-engine eval: rejected Hiro-Smart-Doc; built 3-engine bake-off spike
  (experiments/layout-bakeoff/); offline Docling model provisioning for the
  proxy-blocked PC; verified Docling↔pymupdf coordinate agreement (overlay.py).
  Docling HTML tables match source exactly; figures captured.
- Docling integration (Phase 1): LayoutProvider protocol + LayoutStructures;
  IR ContentBlock.html/.caption; DocumentProfile.layout_provider; DoclingProvider
  (OCR off, tables+figures, top-left-point bboxes); extractor FUSION (provider
  tables/figures → TABLE/IMAGE blocks, bbox suppression, reading-order merge;
  geometric paths skipped when a provider is set). MNO-C → layout_provider=docling.
  1431 tests green. (Draft decisions: D-DRAFT-1..3.)

### In progress
- Phase 2 corpus validation: running MNO-C through the pipeline with Docling on
  (needs Docling + models provisioned in the pipeline env).

### Next
- Trim provider-table redundancy: TableData carries html AND best-effort
  headers/rows; the latter only feeds the table-anchored req-id path, which MNO-C
  has off → skip populating headers/rows for provider tables when anchoring is
  disabled (keep the intentional text-inline + tables[].html).
- Confirm tables/figures land correctly end-to-end; watch for tables Docling
  misses (no per-table geometric fallback when a provider is set).
- Phase 3: figure/API-spec asset ingestion (vision captions) — figures now arrive
  as IMAGE blocks with crops + captions. See asset-ingestion-design.md.
- Curate MODULE.md public surface for the new extraction files + IR/profile fields.

### Flags
- Table content appears in 3 forms with Docling: requirement text (inlined html),
  tables[].html, and tables[].headers/rows. text+tables[] duality is by-design
  (RAG chunker reads text); the headers/rows copy is redundant for MNO-C
  (anchoring off) — trim queued under Next.
- New public surface (extraction layout_provider.py/docling_provider.py, PDFExtractor
  layout params + fusion methods, IR html/caption, new DocumentProfile fields,
  TableData.html) not yet in MODULE.md — Structure sections stale; run /regen-map.
- Docling is an optional heavy dep NOT in requirements.txt; MNO-C extract fails loud
  without it + models (DOCLING_ARTIFACTS/HF_HUB_OFFLINE) in the pipeline env.
- No per-table geometric fallback when layout_provider set — trusting Docling recall
  (hybrid Docling+pdfplumber-lines dedup is the documented fallback if misses appear).
- experiments/ is a new top-level tree, spike code (not production).
- Pre-existing uncommitted edit to canonical docs/compact/STATUS.md (not from this
  session; left untouched — strand-bound close-session doesn't write canonical STATUS).
