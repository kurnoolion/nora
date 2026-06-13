# multi-mno-sira — draft decisions

Draft decisions for this strand. Promoted to canonical `DECISIONS.md` with real
`D-XXX` IDs at `/land-strand` time.

---

## D-DRAFT-1 — Multi-MNO SIRA retrieval: per-MNO BM25 indexes + LLM-rerank fusion (design C)

**Context:** SIRA's batch pipeline and runtime service are single-corpus —
one BM25 index, one dataset loaded at service startup. Extending to multiple
MNOs (each with multiple releases) and supporting cross-MNO comparison
queries ("compare VoWiFi of A and B") forces a corpus-slicing decision.
BM25's IDF is corpus-wide, and SIRA's doc-enrichment DF filter (the
discriminative-term invariant, plan-aware-sira D-DRAFT-1) is also
corpus-wide — so how the corpus is partitioned changes the retrieval
statistics. Three options were weighed:

- **A — Union index** (one BM25 over all MNO×release, MNO as metadata):
  cross-MNO is natural, but IDF/DF blend across MNOs (a term discriminative
  within MNO A but common across the union gets the wrong DF → enrichment
  mis-fires, single-MNO precision degrades), and adding an MNO perturbs DF
  for every existing doc → forces whole-union re-enrichment.
- **B — Per-MNO indexes**: clean per-MNO IDF/DF, single-MNO precision
  preserved, MNO-add doesn't perturb others — but cross-MNO comparison must
  merge BM25 scores that aren't comparable across indexes (different IDF
  scales).
- **C — Per-MNO indexes + LLM-rerank fusion**: B's retrieval isolation,
  with SIRA's existing LLM reranker (absolute 0-100 relevance scoring,
  corpus-independent) as the cross-MNO merge layer.

**Decision:** Adopt **design C**. Retrieval is per-MNO (clean stats,
isolation); cross-MNO queries retrieve top-K per MNO, merge the candidate
pools, and LLM-rerank the union to produce comparably-scored, balanced
material for the synthesizer.

**Why:** C isolates the BM25-statistics problem (B's win — no shared IDF/DF
to blend or perturb) while solving B's score-incomparability problem with a
mechanism SIRA already has. The LLM reranker scores `(query, doc)` relevance
absolutely, not relative to a corpus, so its scores merge cleanly across
MNOs where raw BM25 scores cannot. C also explains the "balanced retrieval"
requirement (FR-multi-3) mechanically: retrieving top-K *per MNO before* the
union guarantees neither MNO is starved by vocabulary skew. A and B both
rejected — A for statistics-blending + re-enrichment cascade on MNO-add; B
for unsolved cross-MNO score fusion.

**Consequences:**
- The LLM reranker becomes **load-bearing** for cross-MNO queries — it can
  no longer be disabled (`NORA_SIRA_RERANK_ENABLED=false`) for that query
  class, because rerank *is* the fusion mechanism. This raises the priority
  of the parked dedicated-`/rerank` backend TODO (rerank latency was already
  the bottleneck; now mandatory for a whole query class).
- Score-fusion quality depends on rerank scores being genuinely
  MNO-independent — ties directly to the score-normalization concern in the
  dedicated-`/rerank` TODO.
- Per-MNO indexes mean per-MNO enrichment runs (more orchestration in
  `sandbox/sira_configs` + the adapter).
- Open sub-questions deferred to architecture phase: per-MNO-index
  granularity (per MNO, or per MNO×release?); whether query-scope extraction
  reuses NORA's analyzer or is SIRA-local; the concrete merge-then-rerank
  flow in `sandbox/sira_query`.
- This is an architecture decision made during requirements phase —
  re-confirm at land-strand.

---

## D-DRAFT-2 — Release resolution for multi-MNO queries: independent latest-of-each-MNO when unspecified

**Context:** Multi-MNO queries can name a release or not. NORA's FR-10
already resolves "latest → newest release in scope" for the single-corpus
case, but cross-MNO comparison introduces a wrinkle: "compare A and B" with
no release named could mean (i) global-latest release label across both,
(ii) matching/aligned releases (both Q3-2025), or (iii) each MNO's own latest
independently. Different MNOs publish on different cadences, so their
"latest" release labels often differ.

**Decision:** When no release is named, **each MNO in scope resolves
independently to its own latest release** (so "compare A and B" → A-latest vs
B-latest, which may be different release labels). When releases are named
explicitly, use exactly those. FR-multi-5 additionally requires the resolved
`(mno, release)` per lane to be **surfaced** in the /test response so the
user can see when a comparison spans a release gap.

**Why:** Comparing each MNO's *current* state is the most common analyst
intent ("how does A's current spec compare to B's current spec?").
Global-latest (option i) is incoherent across independent release-numbering
schemes. Matching-release (option ii) is often impossible — B may have no
release in the same quarter as A — and over-constrains the common case.
Per-MNO-latest is the natural default; the surfacing requirement (FR-multi-5)
mitigates the one real risk of this choice — that the user silently compares
across a release gap (A's Q4-2025 vs B's Q1-2024) without realizing it.

**Consequences:**
- A comparison can span mismatched release vintages; correctness depends on
  the user reading the surfaced `(mno, release)` labels. Accepted because
  the alternative (forcing release-alignment) breaks the common case.
- The runtime service must track each corpus's release ordering to compute
  "latest" per MNO — a small metadata requirement on the adapter output
  (release must be orderable, not just a free-form string).
- If a future use case needs release-aligned comparison ("compare A and B as
  of the same quarter"), it's an additive query mode, not a change to this
  default.
