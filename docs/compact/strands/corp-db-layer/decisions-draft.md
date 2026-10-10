### D-DRAFT-1 — One write-once SQLite cell DB per (MNO, release) version

**Date:** 2026-10-09
**Context:** Corp migration needs a storage unit for corpus + enrichment +
BM25 index that supports incremental push, versioning, rollback, and A/B
serving on a corporate network that prefers standard embedded DBs.
**Decision:** One SQLite file per (MNO, release) cell version
(`cell-<mno>-<release>-v<N>.db`), write-once (built → VACUUM → rename;
opened mode=ro, immutable=1). A tiny `registry.db` (versions + active
pointers) is the only mutable corpus-side state.
**Why:** Incremental push is a file copy; immutability is structural;
rollback is a pointer flip that never touches data; a corrupted transfer
harms one cell; BM25 statistics must stay per-cell anyway (scores are not
comparable across cells), so a merged index would be a ranking bug. One
big DB loses all of this; one DB per MNO (per-release tables) gives up
write-once semantics — held in reserve if ops dislike file counts.
**Consequences:** ~4,000 files at global scale (registry tiers hot/warm/
cold; loader opens lazily with LRU); schema changes are republishes of
derived artifacts, never in-place ALTERs; serving layer must resolve
cell → file through the registry.

### D-DRAFT-2 — Engine-agnostic index format; one engine per stack; eval adjudicates

**Date:** 2026-10-09
**Context:** BM25 must move from in-memory flat-file rebuilds to a DB
backend, but committing to FTS5 up front would hard-code its fixed k1/b,
missing max-df, and awkward weighted expansion into the schema.
**Decision:** The cell DB carries the inverted index as data (`terms` +
`postings` + `doclen` + params in `meta`, optional FTS5 table from the
same token stream). The service selects exactly one engine per stack at
startup (`NORA_SIRA_ENGINE = memory | postings | fts5`); the phase-2
golden + team eval adjudicates. Expected production engine: `postings`
(exact parity with tuned parameters); fts5 ships only if it beats it.
Phase-4 publish emits only the winning engine's tables.
**Why:** Commits to the file format, not an engine — an engine change is
forever after an eval-gated config flip, never a schema change. `memory`
is disqualified as production at global scale (~100–150 MB RAM per open
cell, minutes of warmup) but stays as the eval parity control, rebuildable
from rows that ship regardless. Rejected: Tantivy, DuckDB FTS, Postgres
pg_search (new runtimes/extensions — weak fit for corp standardization).
**Consequences:** DB engines keep RAM flat (<1 MB fixed per open cell +
a page-cache budget, ~2–5 GB total at global scale); the `postings`
engine means owning a small scoring loop in the service; FTS5 limitations
(fixed k1/b, max-df and expansion weighting emulated query-side) are
accepted as eval candidates, not designed around.

### D-DRAFT-3 — Converter parity by construction, with mandatory self-verify

**Date:** 2026-10-09
**Context:** The converter must produce index statistics identical to
what the service computes today; re-implementing tokenization and
counting would make parity an ongoing testing burden.
**Decision:** The converter builds the index with bm25x's own calls (same
build + enrich_batch the service uses) and dumps the resulting postings/
df/doclen/params into the tables. Every cell then self-verifies: probe
queries through the in-memory bm25x index vs a scorer over the dumped
tables must rank identically, or the cell's conversion fails. FTS5
overlap is reported as informational only.
**Why:** The stored statistics are whatever bm25x computed — parity is
structural, not asserted. The self-verify turns any dump bug into a
build-time failure instead of a serving-time ranking drift.
**Consequences:** The converter imports bm25x, so it lives in `sandbox/`
(D-111 boundary); conversion cost includes a full index build per cell
(measured ~0.3 s/963 chunks — acceptable); bm25x version is recorded in
`meta` since the dump depends on its behavior.

### D-DRAFT-4 — Corp serves the Ask surface only, behind a CorpusStore seam

**Date:** 2026-10-09
**Context:** Architect's scope ruling (2026-10-09): corp deployment needs
requirement Q&A, feedback, model roster, and retrieved-chunk display;
everything else is a dev/test concern.
**Decision:** Two deployment profiles, one codebase. Corp-prod runs the
two serve containers only, reading cell DBs through a new `CorpusStore`
protocol (`FsCorpusStore` = today's files, default; `SqliteCorpusStore` =
corp); a `NORA_SURFACE=ask|full` knob gates the web surface explicitly.
Dev/test keeps the entire current world including ingestion. Dropped from
corp: vector store (dropped outright), knowledge graph (deferred), jobs
DB, parse review / corrections / golden authoring surfaces.
**Why:** The seam mirrors the proven LLMProvider pattern and touches only
two consumers (web/req_tree.py, sira-query's cell loader); an explicit
surface knob beats routes degrading when data dirs happen to be absent;
keeping ingestion in dev/test means corp needs no pipeline machinery.
**Consequences:** Deployment pushes updated corpus into corp incrementally
(publisher pipeline, phase 4); dev-only surfaces must stay cleanly
separable; the corp profile's feature set is a config contract, not an
accident of missing files.

### D-DRAFT-5 — Corrections reach corp only via the next publish

**Date:** 2026-10-09
**Context:** Today an enrichment-overlay Apply reaches live serving
without re-promotion. Cell DBs are write-once, so that path cannot exist
in corp as-is.
**Decision:** The converter bakes current overlay state into the cell DB
at convert time (digest recorded in meta); the Apply/pending machinery is
inert on cell-DB stacks. In corp, correction-shaped fixes ride the next
publish. Approved in review 2026-10-09.
**Why:** Preserves write-once immutability and single-source publishes;
the overlay surface is dev-only under the scope ruling, so no live user
loses a capability today.
**Consequences:** Correction latency becomes publish latency in corp; a
future first-time MNO onboarding runs review/corrections in dev and its
output flows into the next publish; if live correction is ever needed in
corp, it requires a deliberate new mechanism, not a quiet regression.
