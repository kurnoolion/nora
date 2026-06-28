## 2026-06-28 — Strand opened: carve NORA retrieval out of multi-mno-nora

### Done this session
- **Strand created** to own NORA's retrieval / candidate-selection quality,
  split out of `multi-mno-nora` so that strand could land its completed
  ingestion mission (extract → profile → parse → corpus). Relocated the two
  open query-side draft decisions here:
  - **D-DRAFT-1** (was multi-mno-nora D-DRAFT-11) — per-cell vectorstore + NORA
    query-side cell routing & fusion (the NORA-native RAG lane).
  - **D-DRAFT-2** (was multi-mno-nora D-DRAFT-16) — balanced pin / cross-cell
    representation for the rerank-pin lane.
  select-synth (D-DRAFT-14) and the reasoning sentinel (D-DRAFT-17) stayed and
  LANDED with multi-mno-nora — they are sound (select-synth is sira-verified);
  only their *nora-lane verification* is gated on retrieval, tracked here.

### The problem (why this strand exists)
- Single-MNO queries miss critical chunks — the FR2 band requirement is not
  retrieved.
- Cross-MNO queries drop an entire MNO's chunks completely (one-sided answers).
- Net: NORA's query lanes are starved of the relevant chunks, so the SIRA-backed
  select-synth lane could not even be fairly evaluated in the nora context.

### Opening hypothesis
- select-synth works end-to-end in the SIRA harness (multi-mno-sira, 2-MNO
  verified) but not in the nora lane. Same SIRA service underneath ⇒ the gap is
  most likely **integration**, not synthesis design: which enrich run the nora
  lane reads, the candidate hand-off, or a `top_k` / fusion cut that starves the
  input before it reaches the LLM. Start the investigation there.

### Next
- Reproduce the FR2 miss on a single-MNO query; trace whether the chunk is
  absent from the candidate pool vs dropped at fusion/`top_k` vs never embedded.
- Reproduce the cross-MNO one-MNO-drop; check balanced fusion (SIRA side) +
  balanced pin (NORA side) actually reach synthesis together.
- Reconcile the relocated drafts' cross-refs to multi-mno-nora `D-DRAFT-N` →
  the landed canonical `D-0XX` IDs (mapping recorded at land — see the
  multi-mno-nora land entry).

### Flags
- This strand inherits the deferred `regen-map` / `drift-check dev-module
  {query,vectorstore}` from multi-mno-nora's query side.
