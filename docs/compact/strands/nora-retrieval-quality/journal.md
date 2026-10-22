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

## 2026-08-04 — Carried from plan-aware-sira landing: fan-out query-type routing

- Strand `plan-aware-sira` landed today (D-182 promoted; pointer-row/fan-out
  design canonical as D-090). Its unshipped residue moves here, per its
  2026-05-27 finding: fan-out ON/OFF is query-type dependent — plan-aware mode
  (pointer rows + fan-out) is right for plan-summarize queries; vanilla mode
  (pointer rows excluded, fan-out off) is right for feature/concept queries.
  Today a single global `NORA_SIRA_FANOUT_ENABLED` flag picks one posture for
  all queries. The needed end-state is query-type routing: detect a named-plan
  target → plan-aware; else → vanilla. Slots naturally into this strand's
  retrieval-lane work (deferred FR-37 anchors SIRA-in-NORA integration).

## 2026-08-04 — Folded in: parent-displacement hypothesis (from nora-retrieval-parent-displacement)

- Strand `nora-retrieval-parent-displacement` abandoned today; its finding
  moves here as the leading root-cause hypothesis for this strand's
  single-MNO miss symptom: NORA retrieval favors heading-only parent chunks
  over content-bearing children on breadth queries ("What 5G bands shall a
  device support" — NORA prompt 4,171 chars vs SIRA 30,021 on the same
  query, via the Test-page probe). Both symptoms live in module `query`.
- Constraint carried with it: any fix must not regress the per-type tuning
  baked in by D-040. Related prior art: the 2026-05-02 chunk-augmentation
  flag (include_children_titles default-off — parents displacing children in
  cross-doc retrieval is a known corpus property).
