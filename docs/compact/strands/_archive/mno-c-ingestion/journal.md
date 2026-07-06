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

## 2026-07-02 — SIRA ingest tooling, the reference-as-requirement hunt, docs sync

### Done this session
- SIRA adapter (`nora_to_beir.py`): excluded structural section nodes
  (`is_requirement=False`) from the per-req BEIR corpus AND the multigranularity
  plan/section pointer lists (back-compat `bool(req_id)`); added `--only
  <MNO>__<REL>` cell filter (fail-loud, case-insensitive; format matches
  `sira_multi --only`) so one cell can be added to an existing db_root without
  re-emitting/wiping the others; added `--print-skips` / `--print-noid` audit
  flags; documented the incremental flow (README + SETUP + multi-cell runbook).
- Fixed `substitute_placeholders` to cover `requirement_type_pattern` (it stayed
  literal → every node `is_requirement=False` → 0 corpus rows on first run) and
  later `id_label_pattern`. Lesson: every new profile regex field must be added
  to the substitution walk, and re-runs must start from the `profile` stage (the
  substituted profile is cached at `out/profile/<cell>/profile.json`).
- The reference-as-requirement duplicate hunt (90 real-requirement duplicates →
  0): release-notes/change-log entries citing req-ids were captured as
  definitions. Root fix = `id_label_pattern` (a node's OWN id must be behind the
  `ID:` label), applied successively to all three heading-mode id paths —
  heading inline, standalone small-font id block, body-text scavenge — each
  found by a work-PC test round. Priority/title ordering corrected for the real
  trailing format `(PRIORITY) ID: <REQ-ID>`; a `\s*$` anchor was tried and
  dropped (citations are bare, so `ID:` presence is the discriminator). Residual
  duplicate (a citation WITH `ID:`) removed by adding `release notes` to
  `exclude_section_pattern` (title-matched, section number varies). MNO-C
  verified: requirement-duplicates 0, Release Notes subtree dropped.
- Docs-vs-code audit (3 parallel agents): 10 findings fixed — README `--only`
  format, SETUP.md adapter output + audit flags, runbook §A adapter `--only`,
  Docling design doc status/paths, MODULE.md curated updates (profiler 9 new
  fields, parser is_requirement/id_label invariants, extraction LayoutProvider
  surface, models html/caption, vectorstore is_requirement metadata). regen-map
  run scoped extraction+pipeline: Structure sections + MAP.md (522 rows).
- Traced the web/SIRA pickup path: SIRA loads cells at startup (`_load_cells`);
  web resolves cells per-query via `resolve_cells` (unscoped → all MNOs at each
  MNO's latest release); environments config is cosmetic (blurb only). Adding
  MNO-C = restart SIRA service only.

### In progress
- MNO-C SIRA pipeline run (adapter → sira_multi → service restart) on work PC.

### Next
- Verify MNO-C answers in the web playground after SIRA restart (`/healthz`
  shows the cell; unscoped queries fan out to it).
- Sanity-check the requirement-count shed from the Release Notes exclusion
  (only change-log entries should have left the tree).
- Deferred: query-side `is_requirement` gating (Citation build sites +
  synthesizer/query_cli/context_builder filters) when the NORA-native lane runs.
- New strand `image-ingestion` scaffolded (vision-model analysis of extracted
  figures → Mermaid/tables); bind with `/switch-strand image-ingestion` to start.

### Flags
- extraction public-surface drift (regen-map): `BBox` (layout_provider.py) and
  `extract_file` (extract.py) are pub but undeclared in Public-surface prose.
- Cross-module touches outside this strand's modules: sandbox/adapter (SIRA),
  vectorstore/chunk_builder + web docs (is_requirement metadata) — noted for
  land time.
