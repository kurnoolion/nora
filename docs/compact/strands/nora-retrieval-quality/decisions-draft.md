## D-DRAFT-1 — Per-cell vectorstore + NORA query-side cell routing & fusion

*(Relocated from multi-mno-nora D-DRAFT-11 on 2026-06-28 — this is the open
NORA-native retrieval problem the strand owns. Body verbatim; cross-refs to
multi-mno-nora `D-DRAFT-N` now point at that strand's landed canonical `D-0XX`
IDs — reconcile at this strand's land time.)*

**Context:** D-DRAFT-6 makes `vectorstore` per cell
(`out/vectorstore/<mno>/<rel>/`). NORA's query pipeline today loads **one**
ChromaDB collection and builds an in-memory BM25 over the whole store. Per-cell
stores isolate BM25 IDF / DF statistics (the dense component is
statistics-agnostic) and enable balanced fusion for cross-MNO comparison and
release-diff — mirroring SIRA D-DRAFT-3 (per-cell index) + D-DRAFT-4 (composite
identity) + D-DRAFT-7 (runtime cell-dict).

**Decision:** Build **one vector store + BM25 per cell**. The NORA query pipeline
becomes **cell-aware**: resolve query scope → select target cell(s) → retrieve per
cell → merge into one candidate pool keyed on the **composite
`(mno, release, chunk_id)`** (chunk ids stay cell-local) → rerank → synthesize.
The global graph supplies scope / candidate routing (which cells + plans);
per-cell stores supply retrieval. "Latest release" / release-diff resolve
structurally over cell order (MMMYYYY).

**Why:** Per-cell isolates BM25 statistics (avoids the cross-cell IDF blending
that buries the low-frequency release-diff signal — SIRA D-DRAFT-3 rationale,
which applies to NORA's BM25-hybrid component); the composite identity keeps R2's
and R3's same `req_id` distinct (citation + release-diff need this — SIRA
D-DRAFT-4); and it mirrors SIRA's runtime cell-dict so the two systems share one
model. Rejected: single store + `(mno,release)` metadata filter (simpler, but
blends BM25 statistics and gives no structural release-diff — diverges from SIRA);
per-MNO store (loses the release axis).

**Consequences:**
- **This expands the strand into the query side.** `vectorstore` builds per cell;
  the `query` pipeline gains scope → cell-select → per-cell retrieve → merge →
  rerank, threading `(mno, release)` on every retrieved chunk (a path that drops
  the cell tag silently corrupts comparison answers); BM25 is built per cell;
  query/service startup scales with cell count.
- Unblocks the deferred `QueryType.COMPARISON`.
- `eval` becomes cell-aware (consumes per-cell stores) though its output stays
  global.
- Larger surface than ingestion alone — **sequence after the ingestion decisions
  (D-DRAFT-6..10) land.**

---

## D-DRAFT-2 — Balanced pin: round-robin SIRA-pinned chunks across cells for synthesis

*(Relocated from multi-mno-nora D-DRAFT-16 on 2026-06-28 — cross-cell
representation for the rerank-pin lane; the "cross-MNO drops one MNO" symptom.
Body verbatim; the numbering note below is historical. Pairs with
multi-mno-sira's D-DRAFT-16, still open there.)*

**Context:** The merged SIRA lane pins the top SIRA results to NORA's
synthesizer via a score-based filter. For a cross-MNO query the highest-scoring
cell took ~all pin slots (the same cross-cell score-skew as the SIRA fusion
problem), so the synthesizer only saw one MNO and produced a one-sided answer.

**Decision:** Add `NORA_SIRA_PIN_MODE=balanced` (default `rerank-topk` =
unchanged score filter). In balanced mode `_select_pinned_chunks` round-robins
across `(mno, release)` cells (in-cell rerank order) up to `NORA_SIRA_PIN_MAX`
(default 16, sized for the 32K synth context), so every resolved cell is
represented in the pinned set; the synthesizer does final relevance over the
balanced set. The `/test` caption is mode-aware.

**Why:** A pure score filter can't represent "both MNOs" when one corpus
out-scores the other. Round-robin guarantees representation while keeping
in-cell rerank order. Pairs with SIRA's balanced fusion (multi-mno-sira
D-DRAFT-16) — but found **insufficient alone**: SIRA's own top_k cut starves the
input before NORA pins it, so both layers are needed (this fixes what survives
to synth; the SIRA fusion fixes what SIRA returns). Largely superseded for the
band-query use case by Path-B / select-synth, which doesn't pin-by-score at all;
kept for the rerank-pin lane.

**Consequences:**
- New `_balanced_pin` + `_PIN_MODE`/`_PIN_MAX` knobs; two `/test` context sites
  + the caption carry `sira_pin_mode`/`sira_pin_max`.
- Balanced mode ignores the score floor/threshold — representation over strict
  relevance, by design for comparison queries.
- Numbered D-DRAFT-16 (not 15) in the originating strand to match pre-existing
  code + cross-strand references; a coordinated pair with multi-mno-sira's
  D-DRAFT-16.
