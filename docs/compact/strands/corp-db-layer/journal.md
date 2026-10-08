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
