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

---

## D-DRAFT-3 — Index granularity: per-(MNO, release) cell, not per-MNO

**Context:** Design C (D-DRAFT-1) settled on per-MNO BM25 indexes + LLM-rerank
fusion. But "per-MNO" left the release axis unspecified — one index per MNO
(all releases mixed) or one per (MNO, release) cell? BM25 IDF and the
doc-enrichment DF filter are corpus-wide, so the release axis has the same
statistics-blending exposure the MNO axis did.

**Decision:** The unit of indexing is the **(MNO, release) cell** — one BM25
index per cell, not per MNO.

**Why:** FR-9 lists "release diff" as one of the 8 query types ("how did MNO
A's VoWiFi change from R2 to R3?"). That is the same shape as cross-MNO
comparison — balanced retrieval from each side, fused at rerank — but on the
release axis. Per-MNO indexing (releases mixed in one index) would hit the
exact vocabulary-skew-starvation problem design C was built to avoid, now
between releases of one MNO; worse, R2 and R3 of the same spec are
near-duplicates, so mixed IDF buries the low-frequency *diff* signal that a
release-diff query is asking for. Per-(MNO, release) makes release-diff fall
out of the same isolate-then-fuse machinery as cross-MNO — one mechanism,
both query types. Per-MNO would be design C on the MNO axis but union-index
on the release axis: internally inconsistent. Cost objection (N_MNO ×
N_release indexes, each needing a ~13h doc-enrichment pass) is mitigated by
the existing incremental-enrichment machinery (`sira_incremental.py`
content-hash resume): releases are incremental (R3 ⊃ mostly-unchanged R2), so
per-cell enrichment costs "enrich the delta," not full re-enrich per release.

**Consequences:**
- More indexes + enrichment runs than per-MNO, but cost scales with actual
  change (incremental), not cell count.
- The (MNO, release) cell becomes the consistent unit of layout, indexing,
  enrichment, ordering (D-DRAFT-5), provenance (D-DRAFT-4), and citation.
- "Latest release" resolution becomes structural (pick the max-ordered cell
  per MNO), not a within-index metadata filter — see D-DRAFT-5.
- Per-MNO-only queries that intentionally span all releases ("what has MNO B
  ever supported?") must query all of B's cells and merge — same fusion
  machinery, rare query, handled.

---

## D-DRAFT-4 — Cross-cell chunk identity: composite `(MNO, release, doc_id)` with structural provenance

**Context:** With per-(MNO, release) cells (D-DRAFT-3), cross-cell queries
(cross-MNO comparison, release-diff) retrieve from multiple cells and merge
into one candidate pool for LLM-rerank fusion. The corpus rows are
`{_id, title, text}` with no metadata field, and BM25 doesn't read metadata —
so provenance can't live in the row. Meanwhile the same `req_id` legitimately
exists in multiple cells (VZW Feb2026 and a future VZW Aug2026 both have
`req:LTEAT:5.1` — the same spec evolving across releases).

**Decision:** The cross-cell chunk identity is the **composite
`(MNO, release, doc_id)`**, not `doc_id` alone. Provenance is **structural** —
a chunk's origin cell IS its provenance, attached to the chunk at retrieval
time (the chunk came out of that cell's index). No `doc_id` prefixing; within
a cell, `doc_id`s stay exactly as they are (`req:...`, `doc:<plan>`,
`section:<plan>:<num>`). The composite identity only matters at and above the
merge layer.

**Why:** Merging on `doc_id` alone would collapse two genuinely distinct
chunks (R2's vs R3's `req:LTEAT:5.1`), break citation resolution (which
release did this answer come from?), and defeat release-diff at the identity
layer — a release-diff query's entire point is comparing the same req_id
across releases, which is impossible if they share one identity. Structural
provenance (vs doc_id prefixing) keeps within-cell doc_ids untouched, so the
existing doc:/section: fan-out composes unchanged: pointers are cell-local
req_ids, fan-out happens within-cell, and fanned-out chunks inherit the cell
provenance. The composite is also exactly what FR-multi-5 surfaces in the UI
(`(mno, release)` per lane) and what cross-comparison citations need.

**Consequences:**
- The merge layer in `sandbox/sira_query` must carry `(mno, release)` on every
  retrieved chunk and key dedup/citation on the composite.
- Provenance is added at retrieval, not baked into persisted doc_ids — so a
  cell can be re-indexed without rewriting ids, and a doc_id is only globally
  meaningful when paired with its cell.
- Any cross-cell merge structure (the candidate pool, the rerank input, the
  returned results) must thread the composite through; a code path that drops
  the cell tag silently corrupts cross-comparison answers.

---

## D-DRAFT-5 — Release identity & ordering from the input directory convention `<MNO>/<MMMYYYY>`

**Context:** "Latest release" resolution (D-DRAFT-2) requires release labels
to be orderable. The parse tree carries three candidate fields: `release`
(the input dir name, currently `OA-baseline`), and `release_date` (a
free-form profile-regex capture of whatever the document author typed after
"Release Date:", currently `"February 2026"`). Grounding showed
`release_date` is unbounded — the capture group is `(.+?)`, normalized by no
pipeline stage, so different MNOs/documents could write any date format.
`infer_metadata_from_path` already derives `mno`/`release` from the input
PATH, not document content.

**Decision:** Release identity and ordering come from the **input directory
name convention**: `<env_dir>/input/<MNO>/<MMMYYYY>/`, where MMM is a
3-letter title-case month (Jan..Dec) and YYYY a 4-digit year (e.g. `Feb2026`).
The directory name IS both the release identity (label) and the sort key:
`Feb2026` → order key `(2026, 02)`, stored ISO as `2026-02`; "latest per MNO"
= max order key. Non-matching directories are rejected **fail-loud at ingest**
(in/beside `infer_metadata_from_path`). The document `release_date` field is
**demoted to display-only** — it may appear as a human label in FR-multi-5's
UI alongside the structured `Feb2026`, but it never drives ordering or
resolution.

**Why:** Three options were weighed. (a) Parse `release_date` → ISO: rejected
— bets on robustly parsing an unbounded free-form input, the exact
silent-mis-order failure mode we most want to avoid. (b) Explicit
human-supplied order key in the per-MNO profile: workable (fits the existing
profile→tree→adapter flow) but adds a profile field and parsing burden. (c)
Input directory convention: chosen — the directory name constrains the format
at the filesystem level (validated at ingest, earliest/clearest place),
requires no new code (`infer_metadata_from_path` already reads the path), no
profile change, and is orderable by construction. The operator who places the
files knows the release date; encoding it in the dir name is the natural,
already-required act. Resolves the open consequence left in D-DRAFT-2
("release must be orderable, not just a free-form string").

**Consequences:**
- `release_date` is a trap for future code — the tree carries both it and the
  dir-derived release, and the free-form one is the tempting-but-wrong sort
  key. Ordering must strictly use the `MMMYYYY` directory name.
- Operator burden: input dirs must follow `MMMYYYY` exactly; a typo
  (`Feb-2026`, `February2026`) is rejected at ingest rather than silently
  mis-sorted. Existing `input/VZW/OA-baseline/` must be renamed +
  re-extracted on the work PC to become a valid cell (a one-time migration,
  not design work).
- Month-granularity ordering assumes ≤1 release per (MNO, month) — true for
  these quarterly-cadence corpora. If an MNO ever ships twice in one month,
  the convention needs a discriminator; YAGNI now.

---

## D-DRAFT-6 — Per-cell BEIR datasets + reused config with `data.name` override

**Context:** With per-(MNO, release) cells, the adapter (currently one
`--env-dir` → one flat BEIR dataset) and SIRA's config (`data=<name>` →
`<db_root>/<name>/`) must accommodate N cells. The adapter already has every
tree's `(mno, release)` for partitioning.

**Decision:** Each cell is its own BEIR dataset at
`<db_root>/<mno>__<release>/raw/` (double-underscore separator,
**source-case preserved** — `VZW__Feb2026`). The adapter gains a
`--multi-cell` mode that partitions trees by `(tree.mno, tree.release)` and
emits one cell per partition (single-dataset mode stays intact). SIRA uses
**one reused `data/nora.yaml`-style config with `data.name=<cell>` overridden
per cell** — no per-cell config files, since cells differ only in name
(k_values/split/min_query_len are cell-independent). **Multi-cell mode emits
corpus-only for now** — per-cell queries/qrels are eval-time and deferred with
OQ-2.

**Why:** Per-cell datasets give each cell the clean, isolated BEIR layout
SIRA's prepare/bm25/doc-enrich stages already expect — zero SIRA change to
build a per-cell index. Double-underscore avoids collision with single-token
names; source-case preservation round-trips exactly against
`infer_metadata_from_path` output (case-normalization is a silent-mismatch
bug). One config + `data.name` override beats N per-cell YAMLs (unmaintainable
as cells grow) and beats interpolation magic (the path resolves from
`db_root` + `data.name` natively). Corpus-only emission matches what the
runtime service (FR-multi-5) actually consumes — per-cell corpus + BM25 index
+ doc enrichments — and defers the eval scaffolding to when on-prem qrels
exist (OQ-2), keeping the first cut small.

**Consequences:**
- `--output` semantics shift in multi-cell mode from "the dataset dir" to
  "the db_root" (parent of cells) — a mode-dependent meaning to document
  clearly.
- `data.name` must be verified cleanly overridable on the SIRA CLI at
  implementation time (the path-resolution model says it should be).
- Eval (query-enrich/rerank/eval stages) can't run per-cell until OQ-2
  delivers per-cell queries/qrels — multi-MNO ships measured only at the
  corpus/retrieval level initially.
- The legacy flat `nora` dataset is superseded by cells; once the work PC
  re-runs the adapter in multi-cell mode, it can be retired.

---

## D-DRAFT-7 — Orchestration: NORA-side cell-loop (batch) + service cell-dict (runtime), SIRA unchanged

**Context:** Two consumers must iterate cells. The batch pipeline
(`run_pipeline.py`) runs one dataset per invocation. The runtime service
(`sandbox/sira_query/service.py`) loads a single module-global `_bm25`. Both
need to become cell-aware.

**Decision:** **Batch** — a thin NORA-side orchestrator enumerates cells under
`db_root` and invokes `run_pipeline.py` once per cell with `data.name=<cell>`;
`run_pipeline.py` itself is unchanged. **Runtime** — the service's `_bm25`
global becomes a `dict[cell_key → CellState]`; the service enumerates cells
from `db_root` at startup (dirs matching `<mno>__<MMMYYYY>`) and loads each
cell's BM25 index + doc enrichments. Query-time scope resolution (FR-multi-6)
selects the target cell set; retrieve→tag→merge→rerank (D-DRAFT-4) operates
over them.

**Why:** Keeping `run_pipeline.py` unchanged is consistent with the
patch-don't-fork posture established for SIRA (the per-stage-routing patch) —
a NORA-side loop shelling out per cell adds no upstream divergence and
composes with incremental enrichment per cell. Patching `run_pipeline.py` to
accept a cell list was the alternative — rejected as more invasive for no
gain, since per-cell invocation already does exactly what's needed. The
service cell-dict is the minimal change that makes multi-cell retrieval
possible while keeping each cell's BM25 statistics isolated (the whole point
of D-DRAFT-3).

**Consequences:**
- A new NORA-side orchestrator script (location TBD —
  `sandbox/sira_multi.py`, or extend `sira_incremental.py`) becomes the batch
  entry point for multi-cell ingestion.
- The service's startup cost and memory scale with cell count (N indexes
  loaded). Acceptable at expected cell counts (a few MNOs × a few releases);
  revisit lazy-loading if it ever grows large.
- The service's per-query path gains scope-resolution + multi-cell retrieval +
  merge before the existing rerank — the concrete shape (and whether it leans
  on the dedicated-/rerank backend TODO) is the next architecture question.

---

## D-DRAFT-8 — SIRA sandbox modules stay informal (no MODULE.md) for now — Option 1

**Context:** Three of this strand's four target modules
(`sandbox/adapter`, `sandbox/sira_configs`, `sandbox/sira_query`) live under
`sandbox/`, which `structure-conventions.md` does not cover — only
`core/src/` and `customizations/` directories get MODULE.md contracts. The
architecture-phase persona assumes doc-first MODULE.md curation with
requirements traceability. So most of this strand's design work lands outside
the formalism COMPACT's architecture rigor expects.

**Decision (Option 1):** Keep the SIRA sandbox modules **informal** — capture
their architecture as strand journal entries + draft decisions, not as
MODULE.md contracts. Only `web` (a real `core/src/` module) gets MODULE.md
treatment when its turn comes. Defer Option 2 (promote the SIRA sandbox
modules to first-class with their own MODULE.md, extending
`structure-conventions.md` to cover a `sandbox/` module class) until
multi-MNO/multi-release SIRA ships and proves durable.

**Why:** The SIRA sandbox is research/integration code tracking an upstream
clone; it has deliberately avoided MODULE.md formalism so far. Forcing
doc-first contracts onto a still-moving design (the granularity, fusion, and
orchestration shapes are mid-spike) is premature — the contracts would churn
faster than they'd stabilize. Designing in the journal/decisions-draft keeps
the reasoning captured and auditable without paying contract-maintenance cost
on a moving target. Promotion (Option 2) is the right graduation step once the
subsystem is durable, not before.

**Consequences:**
- This strand's `sandbox/` design lives in the strand journal + decisions-draft,
  not in MODULE.md — `drift-check design` won't audit it (acceptable; it's
  not yet a contract).
- Extending `structure-conventions.md` for a `sandbox/` module class is
  deferred work, tracked as the eventual Option 2.
- When Option 2 lands, the journal/decisions-draft become the source material
  for the new MODULE.md contracts — so capturing them richly now pays off
  later.

---

## D-DRAFT-9 — Query-scope extraction: reuse NORA's analyzer with standard LLM-or-fallback selection

**Context:** Multi-MNO SIRA needs to extract MNO + release scope + query type
from the natural-language query (FR-9/FR-10) to drive cell resolution. NORA's
`core/src/query/analyzer.py` already does this in two forms —
`LLMQueryAnalyzer` (prompts an LLM, returns `mnos`/`releases` lists +
`query_type` incl. `release_diff`, self-falls-back to Mock on parse failure,
more accurate) and `MockQueryAnalyzer` (keyword/regex, no LLM; `_MNO_ALIASES`
maps verizon/vzw/vz→VZW etc., matching our cell MNO identity). The SIRA query
service currently has no NORA `LLMProvider` (rerank goes via raw httpx), and
there is no central selector — `pipeline.py` defaults to `MockQueryAnalyzer()`
and accepts injection.

**Decision:** Reuse NORA's analyzer rather than building a SIRA-local parser,
and select it by the standard rule: **LLM configured → `LLMQueryAnalyzer`;
not configured → `MockQueryAnalyzer` fallback** — the same configured-LLM-or-
mock posture as the rest of NORA, not a rule-based carve-out for query
analysis. This requires (a) a small selection helper
(`make_query_analyzer(llm_provider | None)`) in `core/src/query` — none exists
today; (b) the SIRA service constructing a NORA `LLMProvider` from its
**primary** LLM config (the shim / `NORA_LLM_*` endpoint used for enrichment,
NOT the rerank-override LLM) to feed `LLMQueryAnalyzer`. Cell *resolution*
(`_resolve_cells`, D-DRAFT-10) stays SIRA-local; only *extraction* is reused.

**Why:** Single source of truth (the canonical MNO alias map → VZW/TMO/ATT
matches cell identity; a 4th MNO is one edit benefiting both NORA and SIRA).
Consistency + accuracy: when an LLM is available it should do analysis (the
user's standing rule), and `LLMQueryAnalyzer` handles oblique MNO references
and multi-release release-diff queries ("Oct 2025 to Feb 2026") that the Mock
regex cannot. The earlier "keep the LLM out of the scope front to save
latency" rationale was explicitly rejected by the user — analysis is one cheap
call on the primary (enrich-quality) LLM, not the high-volume rerank path, so
quality/consistency outweighs the saved call. A SIRA-local parser was rejected
as duplicate-and-drift.

**Consequences:**
- New selection helper in `core/src/query` (a curated MODULE.md module) —
  small, and benefits NORA's native path too (which currently defaults to Mock
  even when an LLM is configured — a latent NORA gap this surfaces; flagged for
  the NORA side to address separately, not fixed in this strand).
- The SIRA service now uses **both** abstractions: NORA's `LLMProvider`
  Protocol (analysis) and raw httpx (rerank). Minor inconsistency; a future
  cleanup could route rerank through the Protocol, but out of scope here.
- `.finditer` fix on `_extract_releases` is now **fallback-only** (Mock path),
  no longer release-diff-blocking when an LLM is configured — lower urgency,
  still a core change for fallback consistency.
- Analysis quality now depends on the configured LLM's extraction reliability;
  `LLMQueryAnalyzer`'s built-in Mock fallback on parse failure means a bad LLM
  response degrades rather than breaks.

---

## D-DRAFT-10 — Fusion code shape: cell-loop generalization with `(cell_key, idx)` identity + `_resolve_cells`

**Context:** With per-(MNO, release) cells (D-DRAFT-3) each holding its own
BM25 index, a cross-cell query (cross-MNO comparison, release-diff) must
retrieve from multiple cells and combine the results into one ranking
("fusion"). The current `/sira-query` flow is single-index: expand →
`_bm25.search_with_expansion` → `hits[(idx, bm25)]` → LLM rerank → sort by
rerank score → top_k, where `idx` is a corpus index into the one `_bm25`.
BM25 scores from different cells aren't comparable (per-corpus IDF scales).

**Decision:** Fusion is the **generalization of the single-index flow to a
cell loop**, with the composite `(cell_key, idx)` threaded through as identity
(D-DRAFT-4). `_resolve_cells(intent, available)` (FR-multi-6 cross-product)
produces the target cell set; the handler retrieves per cell, tags each
candidate with its `cell_key`, merges into one pool, LLM-reranks the pool, and
sorts by rerank score. **Single-cell is N=1** — no separate cross-cell branch;
the three query shapes (scoped / cross-MNO / release-diff) collapse to one code
path differing only in what `_resolve_cells` returns. `_resolve_cells` returns
`(resolved, unresolved)` so requested-but-unavailable cells are surfaced
(fail-VISIBLE at query, mirroring D-DRAFT-5's fail-LOUD at ingest); the caller
errors only if `resolved` is empty. It lives in `sandbox/sira_query`
(cell concept is SIRA's), consuming the reused-from-core `QueryIntent`.

**Why (five embedded calls):**
1. **Fusion method = sort by rerank score, not RRF** — valid only because the
   LLM-as-judge reranker emits absolute 0-100 relevance (corpus-independent).
   RRF (rank-position fusion) was the obvious score-free alternative; rejected
   because the reranker's absolute scores are higher-quality for comparison.
   Coupling note: swapping to the dedicated cross-encoder `/rerank` backend
   would require score normalization (those scores aren't 0-100 absolute).
2. **Balanced retrieval = `per_cell_top_n` per cell, rerank the union**
   (FR-multi-3) — each cell gets full representation so neither MNO is starved
   by vocabulary skew. Cost: N_cells × per_cell_top_n rerank calls; make
   `per_cell_top_n` a knob for latency tuning.
3. **No cross-cell dedup** — the same `req_id` legitimately exists in two cells
   (release-diff: R2's vs R3's `req:LTEAT:5.1`) and BOTH must reach top_k. The
   composite `(cell_key, doc_id)` keeps them distinct; a naive doc_id dedup
   would silently break release-diff. Dedup only within a cell (fan-out
   handles it).
4. **Expand once, DF-filter per cell** — query enrichment is one query-level
   LLM call; the DF-filter of those phrases uses per-cell corpus statistics
   (a phrase discriminative in VZW may be common in TMO), so filtering runs
   per cell (cheap DF lookup, no extra LLM call).
5. **`_rerank_pool` reuses the existing batch/per-call rerank verbatim,
   re-keyed** on the `Candidate` (carrying `cell_key`) instead of a bare `idx`.
   The reranker never knows about cells — it scores `(query, text)` pairs and
   the pool threads provenance around it.

**Consequences:**
- The service's `_bm25` global becomes `dict[cell_key → CellState]`
  (D-DRAFT-7); retrieval, fan-out, rerank, and results all thread the
  composite identity. A code path that drops the `cell_key` silently corrupts
  cross-comparison answers (wrong-release citations, collapsed release-diff).
- Cross-cell rerank cost compounds (per-cell granularity × balanced retrieval)
  — the highest-leverage perf item is the dedicated-`/rerank` backend TODO.
- `_resolve_cells` ordering uses `_order_key` on the cell label (`MMMYYYY`),
  never the tree's free-form `release_date` (D-DRAFT-5 trap).
- Results carry `(mno, release, doc_id)` provenance for citation +
  FR-multi-5 UI surfacing; `_resolve_cells`'s `unresolved` list feeds the
  symmetric "requested but unavailable" surfacing.

---

## D-DRAFT-11 — Multi-cell query routing: preserve the legacy single-dataset handler, add multi-cell as a separate path

**Context:** D-DRAFT-7 specified the runtime `_bm25` global ->
`dict[cell_key -> CellState]`. Implementation faced a choice: rewrite the
235-line `/sira-query` handler so single-cell is just N=1 of the fusion
path (the design's "single-cell = N=1" principle), or keep the existing
single-dataset handler and add the multi-cell path beside it. The
existing `nora` dataset has no valid `(mno, release)` — its release is
the free-form `OA-baseline` — so it cannot be expressed as a cell, and
the handler couldn't be exercised against real bm25x on this machine.

**Decision:** Keep the legacy single-dataset handler **unchanged**; route
to a new `_multi_cell_query` path only when `<db_root>` contains
`<mno>__<MMMYYYY>` cells (`if _cells:`). The two paths coexist; a dataset
without a valid cell key stays on the legacy path.

**Why:** Zero regression risk for the current single-MNO setup — the
working handler (with its fan-out, instrumentation, pinned-chunks logic)
is untouched. A blind unified rewrite of a critical async handler that
couldn't be run here (no bm25x) was exactly the "large untestable block"
the dev persona warns against. The new path is built on the
standalone-tested `resolve_cells`/`fuse` and exercised via FastAPI
TestClient with fakes. The legacy dataset genuinely can't join the cell
model (no MMMYYYY release), so a unified path would have needed a
synthetic-cell-key hack for it anyway.

**Consequences:** Two retrieval code paths coexist — the multi-cell path
duplicates the retrieve/rerank shape rather than reusing the legacy
inline logic, and the legacy path lacks the multi-cell follow-ups (it has
fan-out; the multi-cell path doesn't yet — Follow-up 2). When the legacy
single-MNO `nora` dataset is retired (everything migrated to cells), the
legacy path becomes dead code and can be removed, leaving the unified
N=1 path as the design intended. Until then, both must be maintained.

---

## D-DRAFT-12 — MMMYYYY input validation lives sandbox-side (sira_preflight), not in core's infer_metadata_from_path

**Context:** D-DRAFT-5 stated the MMMYYYY release-dir validation should be
"fail-loud at ingest (in/beside `infer_metadata_from_path`)."
Implementation found that location wrong on two counts: (a)
`infer_metadata_from_path` is **core** (`core/src/extraction/registry.py`)
and serves the legacy NORA pipeline, where free-form releases like
`OA-baseline` are perfectly valid — enforcing MMMYYYY there would break
the single-MNO NORA flow; (b) core must not import `sandbox/sira_cells`
(the module boundary runs sandbox -> core only). MMMYYYY is a
multi-MNO-SIRA convention, not a core concern.

**Decision:** The early validation lives **sandbox-side** as an opt-in
pre-flight (`sandbox/sira_preflight.py`) the operator runs against
`<env_dir>/input/` before the multi-cell pipeline. The adapter's
`--multi-cell` partitioning (`_partition_trees_by_cell`) remains the
backstop fail-loud. Core's `infer_metadata_from_path` is unchanged.

**Why:** Keeps the MMMYYYY convention scoped to the multi-MNO flow that
needs it, without breaking the legacy pipeline or inverting the module
boundary. The pre-flight still achieves D-DRAFT-5's intent — fail-loud at
the earliest point, before extraction wastes time — just at the correct
location. This corrects D-DRAFT-5's stated implementation site (the
*intent* and *convention* in D-DRAFT-5 stand; only the "in
`infer_metadata_from_path`" detail was wrong).

**Consequences:** Validation is opt-in (the operator must run the
pre-flight) rather than automatic at extract time — a misnamed dir not
caught by the pre-flight is still caught later by the adapter, just less
early. If the multi-MNO SIRA work ever graduates to first-class (Option
2, D-DRAFT-8), a core-level convention hook could be revisited, but that
would need a way to scope it to multi-MNO runs without affecting the
legacy path. Supersedes the "in/beside `infer_metadata_from_path`"
phrasing in D-DRAFT-5's Decision; D-DRAFT-5's convention + ordering
semantics are unchanged.

---

## Cross-strand couplings — incoming from `multi-mno-nora` (2026-06-19)

The `multi-mno-nora` strand adopted the `(MNO, release)` **cell** model (its
D-DRAFT-6..12, see `docs/compact/strands/multi-mno-nora/multi-mno-ingestion-design.md`).
Two of its decisions reach back into this strand and must be reconciled when
`multi-mno-sira` next lands:

1. **NORA D-DRAFT-6 amends this strand's D-DRAFT-12 (MMMYYYY validation
   placement).** NORA promotes Verizon OA to its own cell
   (`input/VZW-OA/Feb2026/`), removing the last free-form-release holdout — the
   *sole* reason D-DRAFT-12 kept MMMYYYY validation sandbox-side (to protect
   `OA-baseline`). With that holdout gone, MMMYYYY becomes a **universal**
   convention validated in a shared **core** util (`release_key()`), which both
   NORA's `infer_metadata_from_path` and this strand's `sira_preflight` call.
   D-DRAFT-12's *intent* (fail-loud early, no sandbox→core boundary inversion)
   stands; only its "logic lives sandbox-side" placement changes to "logic lives
   in a core util, invoked from sandbox." **At land time, update D-DRAFT-12's
   Decision/Consequences to reference the core util.**

2. **NORA D-DRAFT-12 changes the adapter's tree discovery (couples to this
   strand's adapter / D-DRAFT-6).** NORA nests parse output to
   `out/parse/<mno>/<rel>/*_tree.json`. The adapter's `_load_trees` (currently a
   flat `out/parse/*_tree.json` glob) must walk the nested layout, or it silently
   reads zero trees. Downstream `(mno, release)` partitioning + `--multi-cell`
   emission are unchanged. **Lockstep:** landing NORA's per-cell layout requires
   this adapter change in the same migration.

No SIRA decision is rewritten here — this note flags the reconciliation for
whoever lands this strand.
