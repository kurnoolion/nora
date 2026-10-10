## 2026-10-08 — Strand opened; artifact inventory for the corporate DB layer

### Done this session
- Strand created and bound; phase set to architecture with target modules
  env, vectorstore, web, pipeline, query (+ one-hop deps) loaded.
- Wrote `artifact-inventory.md` — code-verified inventory of every artifact
  the DB layer must re-home, measured against the OA dev environment:
  9 groups (source corpus, build artifacts, web SQLite state, corrections ×2
  + annotations, golden eval, reports, SIRA cell datasets, labels/promotion,
  git-owned config) classified into 5 DB-fit buckets.
- Key findings recorded there: the requirement row is the shared unit across
  parse trees / SIRA corpus / traces / overlay; `out/parse/` is serve-time
  data (ships in every label); sira-query loads cells read-once-into-memory,
  so the DB serves snapshots, not BM25 queries; scale is small (serve-set
  ~40 MB dev) — the driver is operational fit, not volume.

### In progress
- Nothing in code — strand is at the analysis stage.

### Next
- Settle the 5 open questions in artifact-inventory.md, starting with:
  does the corporate serve-set keep the embeddings/Chroma layer, or ship
  SIRA-lane-only? Then: BM25 rebuild-at-load vs per-(cell,label) index blob
  (needs one timing measurement on a production-size cell); one DB vs two
  schemas; build side files-then-publish vs rows-direct; corrections kept
  outside label-scoped immutability.
- Then draft the schema + MODULE.md deltas (doc-first) for the storage seam.

### Flags
- Target corporate DB technology unknown — constrains blob handling and
  the promote/flip mechanism; find out what the corporate network offers
  before schema drafting.

## 2026-10-09 — Storage + converter designs drafted, reviewed, approved, committed

### Done this session
- Architect's scope ruling captured: corp-prod serves the Ask surface only
  (Q&A, feedback, roster, retrieved chunks); Eval Studio, parse review,
  corrections, and the ingestion pipeline stay in dev/test; vector store
  dropped; knowledge graph deferred; SQLite-family DBs preferred.
- Wrote `storage-design.md` (v0.1 → v0.4 through review): two deployment
  profiles behind a `CorpusStore` seam; one write-once SQLite cell DB per
  (MNO, release) version; registry DB as the only mutable corpus state;
  engine-agnostic BM25 design — one file format, three engines (memory /
  postings / fts5), phase-2 eval adjudicates; four gated migration phases
  (architect's plan); phase-4 publish pipeline; §1a overall diagram +
  query/response path.
- Wrote `converter-design.md` (v0.1 → v0.2): label→cell-DB converter,
  schema v1, parity by construction (index built with bm25x's own calls,
  then dumped), mandatory per-cell self-verify, CLI in `sandbox/` (D-111),
  loader knobs `NORA_SIRA_INDEX` / `NORA_SIRA_ENGINE`.
- Review stress tests answered and folded into the docs: global scale
  (~100 MNOs × 40 releases, 10k reqs/cell → ~4,000 cell files, lazy-open +
  LRU loader); RAM per engine (memory engine disqualified as production,
  ~20–30 GB; DB engines ~2–5 GB); single engine per stack at runtime;
  phase-4 publish emits only the winning engine's tables.
- Architect approved the design; expected production engine: postings
  (fts5 ships only if it beats postings in the phase-2 eval).
- Committed (fb87cf6) and pushed; all redaction gates clean.

### In progress
- Nothing in code — strand moves from analysis to build next.

### Next
- Phase 1: implement `sandbox/cell_db_convert` against the current label
  (CNV- error codes, NFR-9 artifact triple, --verify self-check).
- Phase 2: sira-query loader branch (cell-db mode + engine knob), second
  stack via existing A/B compose mechanics, golden + team evaluation.
- MODULE.md deltas for the storage seam when code lands (web, sira-query).

### Flags
- §7 overlay-semantics change (corrections ride the next publish in corp;
  Apply machinery inert during the trial) — approved in review 2026-10-09,
  but promote to a logged decision at land time.
- Corp DB technology / network constraints still unknown — blocks only
  phase-4 items (§8: transfer mechanism, metrics/config DBs, NORA_SURFACE).
