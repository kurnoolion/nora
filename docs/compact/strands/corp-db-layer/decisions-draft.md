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


**Amended 2026-10-11 (at implementation + field validation):**
(a) The index payload is the ENRICHED serialized bm25x engine state as
zlib-compressed blobs (`index_blob` table), not dumped postings tables —
bm25x is a Rust multi-gram hashed engine whose ranking a textbook dump
cannot reproduce; compression is load-bearing (~500:1 on the hashed
side's slot table). (b) Engine set is `bm25x | fts5`; the designed
rebuild-from-rows `memory` engine was removed — build parameters are
neither introspectable from bm25x nor recorded, and the blob engine IS
the byte-exact parity control. `NORA_SIRA_ENGINE=memory` fails loudly.
(c) Adjudicated by the phase-2 eval on a frozen shared denominator:
production engine = bm25x; fts5 validated as quality-indistinguishable
at n=39 and kept on record as the alternate, not shipped.

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

### D-DRAFT-6 — Corpus rows are position- and byte-faithful; no unique id key

**Date:** 2026-10-11
**Context:** Field validation found real cells carry duplicate
doc/section `_id` rows (~80 per cell on one corpus); the converter's
UNIQUE constraint on corpus.req_id aborted, while the flat stack serves
those rows today.
**Decision:** The cell DB's corpus table keeps EVERY corpus.jsonl row in
file order (`doc` = position), req_id NOT unique (plain index only);
id lookup semantics are last-wins (max doc per id).
**Why:** The index blobs score every row positionally — dedup at
convert time would desynchronize doc numbering and corrupt retrieval;
a (id, kind) key still forbids what the corpus actually contains.
Last-wins mirrors the serving loader's dict semantics exactly.
**Consequences:** Consumers must not assume id uniqueness; service-
equivalent lookup is ORDER BY doc DESC LIMIT 1. Upstream question
flagged separately: duplicates inflate those rows' BM25 statistics in
every stack today (adapter may emit section rows once per occurrence) —
parity correctly preserves it; cleanup is a pipeline investigation, not
a storage one.

### D-DRAFT-7 — disable_auto_save() after every BM25.load

**Date:** 2026-10-11
**Context:** bm25x's constructor form (`BM25(index=path)`) auto-saves
the whole index back to its path after every mutation, enrich included —
and enrich accumulates tf with no dedup, so a path-bound index that gets
enriched at startup would compound into the label across restarts. The
static `BM25.load` the service uses does NOT arm auto-save (verified
empirically and against the vendored source).
**Decision:** Every BM25.load in serving, debug tooling, and the
converter is immediately followed by disable_auto_save(); tests pin both
the constructor-form hazard and the non-mutation of loaded indexes.
**Why:** Label immutability is a §7 invariant; "accidentally safe"
(safe only because we use the right API form) becomes "safe by
contract" for one call per site. SIRA's own batch scripts use the same
belt-and-suspenders.
**Consequences:** A future bm25x version or an API-form change cannot
silently mutate a label; the pinning test tells us if the guard ever
becomes load-bearing or obsolete.

### D-DRAFT-8 — Identity is asserted only over deterministic paths; engine/index knobs are comparability keys

**Date:** 2026-10-11
**Context:** The phase-2 identity gate (cell-db stack must equal the
flat stack id-for-id) cannot run over live query expansion: the
expansion LLM endpoint does not honor temperature 0, so expansion-ON
repeats churn on every run (36/36 samples) even on one stack. Separately,
the engine A/B exposed that two stacks differing only in engine shared a
stamp knob digest and would have pooled as one stack.
**Decision:** (a) Identity gates run with the nondeterministic stage
OFF on both sides, preceded by a within-stack repeat-identity check so
any cross-stack diff is real; the switched-off path's parity is carried
by layered deterministic proofs (converter self-verify, loader tests
with fixed phrases). Quality comparisons run in normal serving config
and are judged against measured run-to-run noise floors, never by
identity. (b) index_mode and engine are retrieval-shaping knobs: they
ride the healthz retrieval_knobs sub-dict (the stamp's service-owns-
knobs contract), so configurations differing in either never share a
comparability key.
**Why:** An identity assertion over a nondeterministic stage tests the
LLM's dice, not the index; floors make quality differences meaningful.
The knob contract keeps the fix two service-side lines with no
eval-side change.
**Consequences:** Gate runs require a config flip + verified restore on
live stacks; every new retrieval-shaping setting must join
retrieval_knobs when added or stamps will falsely pool; the first
Stage-2 noise floor for the golden set (judge mean ±0.5, median ±1.7)
is on record and bounds what Stage-2 can claim.

### D-DRAFT-9 — Campaign snapshots are frozen read-only copies with recorded digests

**Date:** 2026-10-11
**Context:** Field validation found the eval campaign's "snapshot"
directory was a live writer mount (the web app's golden dir), gaining
curated samples and run artifacts after the fact — the historical
denominator was no longer reproducible from that path.
**Decision:** A campaign snapshot is a COPY outside any container
mount, with its sample-set digest recorded beside it and write
permission removed; runs execute against a separate working dir seeded
from the frozen copy, and run stamps carry the set digest so mid-
campaign drift is detected mechanically (runs stop pooling), not
prevented by hope. The live golden dir remains the web app's working
dir, un-frozen by design.
**Why:** One directory was serving two roles (curation target and
frozen denominator) that are mutually exclusive. Named lesson: a
"snapshot" that is a writer's mount point is not a snapshot.
**Consequences:** Campaign setup gains a one-time freeze step;
historical results remain valid under their own digests; digest
equality between working dir and frozen reference is checked before
runs.

### D-DRAFT-10 — Batched enrichment is production; overlap judged by word-level Jaccard vs same-mode rerun noise floor

**Date:** 2026-10-16
**Context:** Suspicion that batched enrichment (many requirements per LLM
call) degrades phrase quality vs one-requirement-per-call. A three-arm
field experiment ran on a small corpus: batched rerun (A), single-request
(B), vs the serving run (r1). At temperature 0.4 (seeded), exact phrase
overlap is ~0 for ALL pairs — including batched-vs-batched reruns — so
exact-string comparison cannot distinguish anything.
**Decision:** Batched enrichment stays production. The measurement
standard for enrichment comparisons is word-level Jaccard measured
against the same-mode rerun noise floor; exact-string overlap is retired
for any temperature > 0 pipeline. Both metrics ship in
`sira_enrich_inspect --compare`.
**Why:** Batched sat exactly at the rerun noise floor vs serving (median
0.38 vs 0.38 floor — indistinguishable from a rerun of itself).
Single-request produced ~2× phrases/req (8.6 vs 4.4), violating the
prompt's 10-phrase cap, at ~9× the LLM call cost, with the least shared
vocabulary of any arm — richer output, but unvalidated richness at a
cost multiple, per the experiment's pre-agreed rule 3.
**Consequences:** No pipeline change; the suspicion is closed with data,
not reassurance. Any future enrichment change must beat batched via the
golden eval, not via overlap metrics. Parked study: whether B-style
richer phrases help retrieval, judged end-to-end. The unenforced phrase
cap is a known gap (flagged, not fixed).

### D-DRAFT-11 — Best-link blessing is ours via golden eval; experiment runs use no-best mode

**Date:** 2026-10-16
**Context:** The cell dataset's `update_best` runs its built-in eval and
repoints per-cell best links when a new run scores >= best — but on our
data that eval scores 0.0 everywhere (inert), so every run "ties"; and on
a tie it both repoints the links AND deletes the previous best's targets.
A field hold caught this before the A/B legs ran.
**Decision:** (a) The built-in eval is adjudicated inert for our corpora;
best-link movement is decided by OUR golden eval (a blessing step), never
by the dataset's own update mechanism. (b) Experiment/diagnostic
enrichment runs set `NORA_SIRA_SKIP_UPDATE_BEST=1` (patch registered in
install_configs), which skips update_best entirely and logs that best
links were untouched.
**Why:** An inert scorer plus tie-wins-and-deletes semantics means any
casual rerun would silently become "best" and destroy the previous best's
files — the opposite of an experiment's contract. Guarding at the env
seam is one line and leaves the vendored dataset code untouched.
**Consequences:** Production ingestion still calls update_best (unchanged
behavior); every experiment must set the knob or risk clobbering serving
state. The planned migration to per-cell pinned best links (maintenance
window) removes even that residual risk. Dangling best links found on one
corpus are repaired at the blessing step, not ad hoc.

### D-DRAFT-12 — Taxonomy ledger is durable and loud: unconditional final save, loud discards, launch accounting

**Date:** 2026-10-16
**Context:** A routine new-release ingestion surfaced a defect chain:
the taxonomy stage silently re-extracted the whole corpus (213 units)
with no indication why; a later zero-extraction retry truncated the
ledger 267→178 because state was saved only on extraction events; and
the whole-stage fingerprint short-circuit then stamped the truncated
ledger as complete.
**Decision:** The taxonomy ledger contract is: (a) TAX-I001 launch line
reports "N cached / M to extract" before any LLM call; (b) a
prompt-fingerprint mismatch discards the ledger LOUDLY (TAX-W006), and
an all-units-stale run warns (TAX-W005); (c) the ledger is saved
unconditionally after the unit loop, so a run's surviving knowledge is
never narrower than what it loaded; (d) stage-stamped implies
ledger-complete.
**Why:** Each defect was invisible until it cost a full re-extract or a
repair session. The root cause of the re-extract itself — an August
parser update rewrote unit bytes and taxonomy never re-ran — was correct
cache behavior, but nothing announced it. Visibility turns "mysterious
expensive run" into "expected repair, pre-announced." Lesson adopted:
output-changing parser updates schedule a taxonomy re-extract.
**Consequences:** Error codes TAX-I001/W005/W006 registered; regression
test pins the truncation fix (proven red without it). Operators read the
launch line before letting a run proceed. A stamped stage can be trusted
again; the one corrupted stamp was cleared (backed up) and rebuilt.

### D-DRAFT-13 — New releases ingest under the legacy build model: scoped additive lanes, corpus-wide taxonomy, hardlink guards

**Date:** 2026-10-16
**Context:** Team-operations/cycle build flow is not deployed on the
ingestion host; a new release had to land in the live env-dir that
serving labels were promoted from. Risks examined: partial state visible
to serving, unwanted rebuilds of prior releases' artifacts, and
corpus-wide rewriters.
**Decision:** Ingest with direct lanes scoped `--mno/--release` (writes
are additive for scoped stages); taxonomy runs corpus-wide BY DESIGN (no
cell filter — newest-release-wins dedup needs the whole corpus) but is
ledger-incremental, so prior releases cost nothing when cached; the
corpus-wide rewriters (taxonomy files, standards reference index) are
the only cross-release writes. Serving isolation rests on promoted
labels: hardlink write-through is the only live channel, so pre-flight
checks link counts (stat %h) and any in-place fix uses copy-then-rename.
**Why:** The legacy model is safe when the write surface is known
exactly; enumerating it beat deploying new build machinery mid-strand.
A cell filter on taxonomy was rejected — it would silently break
cross-release dedup for a marginal cost saving that the ledger already
provides.
**Consequences:** Operators run the enumerated pre-flight before
ingesting into a live env-dir; serving sees the new release only at the
next promote/restart. When the cycle/team-operations model deploys,
these guards become structural rather than procedural.

### D-DRAFT-14 — Enrichment traceability contract: run-scoped ids, req_ids and measured resp_chars on every row; verdict warns only on unresolved requests

**Date:** 2026-10-16
**Context:** Diagnosing two parse-error batches took a full field round
trip because trace rows carried neither the affected requirement ids nor
measured response sizes, batch ids collided across runs, and verify-run
WARNed on any parse-error event — even after a retry had provably
resolved every affected requirement (proof had to be done by hand via
coverage arithmetic).
**Decision:** Every batch trace row carries a run-scoped batch id
(`<timestamp-rand>.b#####`), the full `req_ids` list, and measured
`resp_chars`. Verify-run's verdict treats parse-error events as WARN
only while any affected requirement remains unresolved (by req_ids, or
by coverage proof for legacy rows); resolved events are reported as
transient notes. A fail-loud `--req-ids FILE` selector supports
targeted re-enrichment diagnostics.
**Why:** The run's own artifacts should answer "which requirements, how
big was the response, did the retry cover them" without inference — one
earlier inference (a response-size cap read off config estimates) was
field-disproven and withdrawn, which is what measured fields prevent.
A verdict that warns on self-healed transients trains operators to
ignore warnings.
**Consequences:** Trace rows are slightly larger; the segmenter keeps
working (id format is suffix-compatible). Legacy rows without req_ids
resolve via coverage proof only. Verdicts are now actionable: a WARN
means work remains.

### D-DRAFT-15 — Phase-3 dual-emit: cell DB is a post-verify lane product; flat remains the default

**Date:** 2026-10-16
**Context:** Phase 3 needed ingestion to produce cell DBs without
destabilizing the flat path every stack serves today (§7
backward-compat invariant: changes land behind flags with legacy
defaults).
**Decision:** `sira_lane --index=cell-db` emits the cell DB as an
additional product after the lane and its verify pass complete, reusing
the converter in-process with the lane's run name pinned and the
per-cell self-verify mandatory; output lands under
`<db_root>/cell-dbs/`. `--index=flat` (default) is byte-for-byte
today's behavior; `cycle.sh` forwards the flag. Parse/taxonomy
artifacts stay outside the cell DB (disposition row: deferred to
phase 4's publish pipeline).
**Why:** Emitting after verify means a cell DB can only exist for a
lane state that passed its own checks; pinning the run name makes the
DB reflect exactly the run that just built, immune to best-link or
latest-run drift. A separate post-hoc converter invocation was the
rejected alternative — it re-resolves run selection and can bind a
different enrichment than the lane just produced.
**Consequences:** Dual-emit adds converter cost to each ingestion
(seconds per cell); cell DBs accumulate under the sira root and their
retention is an ops concern; phase 4's publisher consumes these files
as its input contract.

## D-DRAFT-16 — No built-in HuggingFace embedder or reranker; unconfigured means a clear failure or passthrough

**Context:** With no vector store config, the web route built a default
config whose sentence-transformers embedder downloaded from the
HuggingFace hub; on a label with no NORA index an Ask hung on a download
stuck at 0 bytes. `query_cli` and `eval_cli` built that embedder directly,
whatever the store named. The reranker defaulted to a HuggingFace
cross-encoder. Project rule: serving uses no local or offline
HuggingFace artifacts; embeddings and reranking come from a remote TEI
server.

**Decision:** The embedding provider and model have no default (env
config, `VectorStoreConfig`); `make_embedder` raises on an empty
provider. Readers without a store config fail with "no NORA index" (web
route, golden/query/eval CLIs), and the CLIs use the provider the store
was built with. The reranker provider and model have no default; an
enabled reranker with no provider runs as passthrough with a warning.
Compose sets `HF_HUB_OFFLINE=1` on the serve services.

**Why:** A silent default hid misconfiguration as a hang or a quiet
quality change. Keeping the default but setting offline mode was
rejected: it fails later and less clearly, and still leaves the HF
backend as the implicit choice.

**Consequences:** Every environment that builds or reads a NORA index
must name `NORA_EMBEDDING_PROVIDER` (or config/llm.json must). The
sentence-transformers and huggingface backends still exist, but run only
when named.

## D-DRAFT-17 — promote.sh copies the NORA vectorstore instead of hardlinking it

**Context:** Labels are hardlink snapshots, on the premise that a
rebuild never mutates a promoted label. Opening a chroma index updates
`chroma.sqlite3` in place, so the write reached the build workspace and
every label sharing the link.

**Decision:** promote copies `out/vectorstore` (`cp -a`); the rest of the
serve-set stays hardlinked.

**Why:** Hardlinks isolate only rewrites that create new files. No chroma
open mode is known to be reliably write-free, and the vectorstore is
small next to the rest of a label, so a copy is the simple, sure fix.

**Consequences:** Promote takes more disk and time for the vectorstore.
Labels promoted before the change must be re-copied by hand. Any other
serve-set file opened read-write (SIRA cells are unchecked) would need
the same treatment.

## D-DRAFT-18 — The team gate refuses legacy sections instead of forcing them to SIRA

**Context:** The SIRA-only override sat inside the `section == "merged"`
branch of `/api/test/ask`. A gated POST without `section` defaulted to
`requirement_bot` and ran the NORA lane.

**Decision:** For a gated member, `/api/test/ask` runs only
`section=merged` (lanes forced to SIRA) and refuses every other section
before dispatch; `/api/test/synthesize-group`, a NORA-pipeline re-run, is
refused outright. The admin cookie keeps the legacy paths.

**Why:** Silently rewriting a legacy section to merged + SIRA would
return 200 for a request that asked for something else — the same
misreading that hid the gap (a gated "NORA" Ask that ran SIRA). The gated
page never sends a legacy section, so refusing costs nothing.

**Consequences:** Direct-POST clients under the gate must send
`section=merged`. Gate-on tests cover a missing section and each legacy
section.

## D-DRAFT-19 — Web refusal fallback restored for the Ask page (reverses 2026-10-18)

**Context:** Switching models by hand after a refusal proved inconvenient.
**Decision:** Ask reroutes a permanent refusal once to the roster's `fallback_provider`, on all stacks; `NORA_LLM_REFUSAL_MARKERS` is the switch. Curation chat stays without a fallback.
**Why:** Automatic, disclosed reroute (D-220). Rejected: manual switch — puts recovery on every asker; curation-chat fallback — not needed for authoring.
**Consequences:** Each web env file needs the markers and the roster a distinct `fallback_provider`; removing `NORA_LLM_FALLBACK_*` alone doesn't disable it.
