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
