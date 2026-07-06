# Design note (DEFERRED): referenced-asset ingestion

**Status:** deferred design — surfaced during `mno-c-ingestion` but is a separate,
cross-module capability. **Do NOT build it in this strand.** Candidate home:
the in-flight `references-handling` strand, or a fresh `asset-ingestion` strand,
under an **architecture-phase** pass (it needs decisions + new requirements).

## Problem
A plan directory usually holds one plan PDF, but some also carry **assets** the
plan's requirements reference:
- **XL files** (translations, etc.) → **ignore** (skip by extension).
- **Image files** (jpg/png) — flow diagrams referenced from the plan PDF.
- **Extra PDFs** — flows and/or API specs referenced from the plan PDF.

Goal: ingest + store the images/API-spec PDFs and surface them in LLM-synthesized
answers when relevant (user asks about flows/APIs, or a retrieved requirement
references an asset).

## Proposed architecture
1. **Asset as a first-class, linked entity** (NOT a Requirement): `asset_id`
   (`asset:img:<hash>` / `asset:apispec:<hash>`), `asset_type` ∈
   {`image_flow`, `api_spec`, `flow_doc`}, `source_file`, `linked_req_ids`,
   searchable text (caption / parsed text), and `image_path` for images. Edges
   back to the requirements that reference it.
2. **Linkage (the crux + biggest risk):** map a PDF's textual reference
   ("Figure 3", "registration flow", "API §2") to the specific file. Best case
   the **filename encodes the citation** (`Figure_3.png`) → profile-driven
   `asset_reference_pattern` + a naming rule resolves it. Worse case → coarse
   directory-level linkage (all assets in a plan dir link to that plan's reqs)
   or a small manual mapping. **Open: what is the filename convention?**
3. **Ingestion per type:**
   - API-spec / flow **PDFs** → reuse extract→parse with `doc_type=asset` so they
     become **reference chunks, not Requirements**; embed + index; tag + link.
   - **Images** → ingest-time **vision-LLM caption** (describe the flow) + OCR of
     embedded labels → store the caption as the retrievable chunk + keep the
     binary for display. Needs a **vision-capable provider behind the
     `LLMProvider` Protocol** (e.g. `describe_image()`), decoupled from synthesis
     (the synthesis LLM may not be multimodal).
4. **Storage:** asset chunks in the same SIRA BEIR / NORA vectorstore corpus with
   `asset_type` + `linked_req_ids` metadata (reuse the plan-aware-sira **D-090**
   multi-granularity + fan-out machinery); image binaries under
   `out/assets/<mno>/<rel>/…`, served by the web UI.
5. **Retrieval + synthesis (two triggers):**
   - *User asks about flows/APIs* → asset chunks retrieve **directly** (they're in
     the corpus). No special path.
   - *A retrieved requirement references an asset* → **fan-out** to its linked
     asset chunk(s) (exactly D-090's doc/section→constituent fan-out).
   - **UI:** render the image inline when an `image_flow` asset is cited
     (`rag_chunk.image_path`); the caption drives retrieval/citation.

## Phasing
1. (Now) Land MNO-C **base requirements**; skip assets.
2. Cheap/early: detect + record asset **references** in the parser (req→asset
   edges) so linkage data exists before it's used.
3. **API/flow PDFs** as `asset` reference chunks + fan-out (text-only, lower risk).
4. **Images**: vision-caption + UI display (highest effort).

## Decisions to settle (architecture phase)
- Filename/reference **convention** (auto-link vs directory-coarse vs manual) — linchpin.
- New `Asset` entity vs. extending `Requirement.images` for external files.
- **Vision provider** choice + whether captions are good enough for retrieval.
- Fan-out granularity + how asset citations render in the answer.

_Captured 2026-06-30 from the mno-c-ingestion session (full discussion in transcript)._
