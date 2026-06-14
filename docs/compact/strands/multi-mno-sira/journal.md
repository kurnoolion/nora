## 2026-06-13 — Strand opened: requirements scoping for multi-MNO SIRA

First session — pure requirements-phase scoping, no code. Strand created,
bound, phase set to requirements.

### Done this session

- Strand created (`multi-mno` → renamed `multi-mno-sira`), bound, phase
  set to requirements. Target modules: sandbox/adapter,
  sandbox/sira_configs, sandbox/sira_query (added mid-session — design C's
  fusion logic lives there), web.
- Framed the problem: SIRA's corpus becomes 2-dimensional (MNO × release).
  Today SIRA is flat — single MNO, single release, one BM25 index, one
  corpus loaded at service startup. NORA's side was always multi-MNO-aware
  (FR-8 metadata filters on mno/release; FR-30 `<env_dir>/input/<MNO>/<release>/`
  layout; FR-9/FR-10 query scope extraction). This strand brings SIRA up to
  that contract.
- Three target query shapes, increasing difficulty:
  1. MNO-scoped — "What 5G bands does MNO B support?" (retrieve from B only)
  2. Cross-MNO directed — "How does IMS registration of A differ from B?"
  3. Cross-MNO open — "Compare and contrast VoWiFi of A and B"
  Cross-MNO comparison (2,3) is the hard case and drives the architecture.
- Resolved the load-bearing unknown — BM25 indexing strategy — to **design
  C** (see decisions-draft D-DRAFT-1). The A/B/C tradeoff: BM25 IDF and the
  doc-enrichment DF filter are corpus-wide, so how you slice the corpus
  changes the statistics. C (per-MNO indexes + LLM-rerank fusion) isolates
  the stats problem AND uses SIRA's existing absolute-scale reranker as the
  cross-MNO merge mechanism.
- Drafted a 6-FR set (FR-multi-1..6) — proposals, not yet promoted to
  canonical requirements.md (held until /land-strand or an architect call).
- Deferred cross-MNO eval ground truth (OQ-2).

### Draft FR set (proposals — promote at land-strand)

- **FR-multi-1** — SIRA ingests multiple MNOs × ≥1 release each, keyed
  `(mno, release)`; adapter tags every BEIR row with `mno` + `release`.
- **FR-multi-2** — MNO-scoped queries retrieve only from the named MNO's
  corpus.
- **FR-multi-3** — Cross-MNO comparison retrieves top-K **per MNO** before
  merge (no vocabulary-skew starvation). Satisfied by C.
- **FR-multi-4** — Adding an MNO doesn't degrade existing MNOs' retrieval.
  Satisfied by C's index isolation (no shared IDF/DF to perturb).
- **FR-multi-5** — /test issues MNO-scoped + cross-MNO queries through the
  existing interface; SIRA lane resolves MNO scope from the query (extends
  FR-9/FR-10 to the SIRA path) AND surfaces the resolved `(mno, release)`
  per lane so the user sees when a comparison spans a release gap.
- **FR-multi-6** — Release resolution: no release named → each MNO resolves
  independently to its own latest (A-latest vs B-latest, possibly different
  labels); releases named → use exactly those.

### Deferred

- **Cross-MNO / multi-release eval ground truth (OQ-2)** —
  `(deferred: multi-MNO/multi-release qrels must be authored on-prem against
  real carrier documents — revisit: when a second MNO's corpus is ingested
  and a domain expert can label comparison-query relevance)`. Loud flag:
  single-MNO SIRA was proven WITH measurement; multi-MNO ships initially
  WITHOUT it until on-prem qrels exist.

### In progress

- Requirements scoping converged this session. The draft FRs + the C
  decision await architecture-phase elaboration (adapter row-tagging,
  index-build splitting per-MNO, the retrieve→merge→rerank fusion flow in
  sira_query).

### Next

- `/switch-phase architecture` to design the mechanics under C:
  - `sandbox/adapter` — walk `<env_dir>/input/<MNO>/<release>/`, tag rows
    with `(mno, release)`, emit per-MNO corpus structure.
  - `sandbox/sira_configs` — per-MNO dataset configs (or one config with an
    MNO list — architecture decision).
  - `sandbox/sira_query` — per-MNO retrieve → merge → LLM-rerank fusion;
    query-scope extraction (reuse NORA's analyzer or SIRA-local?).
  - `web` — MNO scope surfacing in the /test SIRA lane.
- Resolve the architecture sub-questions deferred from requirements:
  per-MNO-index granularity (per MNO, or per MNO×release?); score-fusion
  detail (does C lean on the dedicated-/rerank backend TODO?).

### Flags

- **Design C makes the LLM reranker load-bearing** for cross-MNO queries —
  it can no longer be disabled (`NORA_SIRA_RERANK_ENABLED=false`) for that
  query class, because rerank IS the cross-MNO score-fusion mechanism. This
  raises the priority of the parked dedicated-`/rerank` backend TODO
  (`sandbox/sira_patches/README.md`) — rerank latency was already the
  bottleneck; now it's also mandatory for a whole query class.
- The C decision is an architecture choice made during requirements phase.
  Captured with full A/B/C reasoning in decisions-draft so rejected options
  aren't lost; will be re-confirmed at land-strand.
- STATUS.md global Active phase was flipped to `requirements` by
  switch-phase — note this is project-wide state, while four other strands
  remain mid-flight at various phases. If that's surprising, the
  per-strand Active-phase field (in each STRAND.md) is the binding-aware
  record.

## 2026-06-13 — Architecture spike: cell identity model (granularity + provenance + ordering + layout)

Second session same day — architecture phase, pure design, no code. Picked
up from the requirements close (design C + release resolution already
decided). Grounded the design in the real code shapes before deciding.

### Done this session

- Switched to architecture phase. Of the 4 target modules, only `web` has a
  MODULE.md — the three sandbox modules (adapter, sira_configs, sira_query)
  have none by design (structure-conventions only covers core/src +
  customizations). **Reconciliation: Option 1** — keep sandbox informal,
  capture design in journal/decisions-draft; revisit Option 2 (promote SIRA
  sandbox modules to first-class with MODULE.md contracts) when multi-MNO
  proves durable. Forcing MODULE.md rigor onto a still-moving sandbox design
  is premature.
- **Granularity → per-(MNO, release) cell** (D-DRAFT-3). Not just "finer is
  safer": FR-9's release-diff query type ("how did A's VoWiFi change R2→R3?")
  *requires* per-release isolation — same isolate-then-fuse logic as design C
  applied to the release axis. Per-MNO indexing would mix releases, burying
  the diff signal under near-duplicate R2/R3 content. Synergy: incremental
  enrichment (content-hash resume) means per-release cells cost "enrich the
  delta," not full re-enrich per release.
- **Grounded in real code before deciding:**
  - Parse tree already carries `mno`, `release`, `release_date`
    (`VZW`/`OA-baseline`/`"February 2026"`).
  - `infer_metadata_from_path` (pipeline/stages.py:79) derives mno+release
    from the input PATH, not document content — so release identity already
    flows from the input directory structure.
  - Service is single-`_bm25`-global; corpus rows are `{_id,title,text}`
    with no metadata field (BM25 doesn't read metadata anyway).
- **Provenance / identity (design B, D-DRAFT-4):** `(MNO, release, doc_id)`
  is the cross-cell chunk identity. The same req_id can exist in two cells
  (VZW R2 and R3 both have `req:LTEAT:5.1` — same spec evolving), so merging
  on `doc_id` alone would collapse distinct chunks and break release-diff at
  the identity layer. Provenance is structural (a chunk's origin cell IS its
  provenance, attached at retrieval) — no doc_id prefixing. Within a cell,
  doc_ids stay as-is; the existing doc:/section: fan-out composes (pointers
  are cell-local, fan-out within-cell, fanned chunks inherit cell provenance).
- **Release ordering (design C, D-DRAFT-5):** evolved through three forms.
  Rejected "parse release_date → ISO" once grounding showed release_date is a
  free-form profile-regex capture of whatever the document author typed
  (unbounded format set — can't enumerate). Rejected "explicit profile order
  key" once the user specified the cleaner source: **the input directory name
  convention `<env_dir>/input/<MNO>/<MMMYYYY>/`** (MMM = 3-letter title-case
  month, YYYY = 4-digit year, e.g. Feb2026). The directory name IS the
  release identity AND the sort key by construction — `Feb2026` → `(2026,02)`.
  No profile change, no free-form parsing, validated at the filesystem
  boundary (fail-loud on non-matching dirs). `release_date` document field
  demoted to display-only (FR-multi-5 human label), fully decoupled from
  ordering. This resolves the open consequence left in D-DRAFT-2 ("release
  must be orderable, not free-form").
- **Layout / orchestration (design A, D-DRAFT-6 + D-DRAFT-7):**
  - Per-cell BEIR datasets at `<db_root>/<mno>__<release>/` (double-underscore
    separator, source-case preserved — VZW__Feb2026 — to round-trip cleanly
    against infer_metadata_from_path). Adapter partitions trees by
    `(mno, release)`, one cell per partition; new `--multi-cell` mode so
    single-dataset behavior stays intact.
  - One reused `data/nora.yaml` config with `data.name=<cell>` override per
    cell — no per-cell config files (cells differ only in name).
  - Batch: NORA-side cell-loop orchestrator invokes run_pipeline.py per cell
    (SIRA unchanged — consistent with patch-don't-fork). Runtime: service
    `_bm25` global → `dict[cell_key → CellState]`, enumerates cells from
    db_root at startup.
  - **Multi-cell adapter emits corpus-only for now** — the runtime service
    path (FR-multi-5) needs only per-cell corpus + BM25 index + doc
    enrichments; queries/qrels are eval-time and deferred with OQ-2.

### In progress

- Architecture spike on the identity/layout model converged. Five draft
  decisions staged (D-DRAFT-3..7). The cell `(MNO, MMMYYYY)` is now the
  consistent unit of layout, indexing, enrichment, ordering, provenance, and
  citation.

### Next

- Remaining architecture design questions (next session's material):
  - **Query-scope extraction** — does SIRA's query service reuse NORA's query
    analyzer (FR-9/FR-10 classify query type + extract MNO/release scope), or
    get a SIRA-local scope parser? This is the front of the
    retrieve→merge→rerank flow.
  - **Concrete fusion code** in `sandbox/sira_query` — the retrieve-per-cell
    → tag → merge → rerank implementation shape, and whether it leans on the
    dedicated-/rerank backend TODO.
  - Adapter `--multi-cell` implementation + the cell-loop orchestrator.
- Then `/switch-phase development` to implement, starting with the adapter
  (it's the upstream of everything — cells don't exist until it partitions).

### Flags

- **`release_date` document field is now display-only** — any future code
  must NOT use it for ordering/resolution. Ordering is strictly from the
  `MMMYYYY` directory name. A subtle trap: the tree carries both, and the
  free-form one is the tempting-but-wrong sort key.
- **Migration is a work-PC operator step**, not design work: existing
  `input/VZW/OA-baseline/` must be renamed to `input/VZW/Feb2026/` and
  re-extracted on the work PC for it to become a valid cell. Nothing to do on
  this dev PC (no corpora here).
- Option 2 (promote sandbox SIRA modules to first-class MODULE.md contracts)
  remains the eventual graduation step once multi-MNO/multi-release ships and
  proves durable. Tracked here so it's not forgotten.
- Design C makes the LLM reranker load-bearing for cross-cell queries
  (carried from the requirements session) — and per-(MNO,release) granularity
  *increases* the cross-cell query surface (release-diff is now also a
  fusion query), further raising the dedicated-/rerank backend TODO priority.

## 2026-06-13 — Architecture: resolved query-scope extraction + fusion code shape

Third 2026-06-13 session — architecture phase, design only, no code. Closed
the two open architecture questions from the prior close, so the strand is
now design-complete enough to start development.

### Done this session

- **Query-scope extraction → reuse NORA's `MockQueryAnalyzer`** (D-DRAFT-9),
  not a SIRA-local parser. Grounding showed the analyzer already extracts
  canonical MNO (`_MNO_ALIASES`: verizon/vzw/vz→VZW, etc. — matches our cell
  MNO identity exactly), release (month-year + latest/current patterns), and
  query type (FR-9's 8 types), and is LLM-optional (keyword `MockQueryAnalyzer`
  default; `LLMQueryAnalyzer` a drop-in upgrade). Reuse wins on: already does
  FR-9/FR-10, single source of truth (4th MNO = one alias-map edit), keep the
  LLM out of the scope front (it's already load-bearing for rerank). Two gaps:
  (a) the "Feb 2026"→"Feb2026" normalization seam (resolver-local); (b)
  `_extract_releases` uses `.search` (first-match-only) → a release-diff query
  naming two releases ("Oct 2025 to Feb 2026", literally in PROJECT.md's
  problem statement) captures only one. Fix = `.finditer`, in
  `core/src/query/analyzer.py` — a CURATED-MODULE change, crossing out of this
  strand's sandbox-informal scope (D-DRAFT-8); helps NORA's own multi-release
  future too, so treat with normal module rigor.
- **Fusion code shape** (D-DRAFT-10). Grounded in the real `/sira-query` flow
  (expand → `_bm25.search_with_expansion` → `hits[(idx, bm25)]` → LLM rerank →
  sort → top_k). Fusion = generalize that single-index flow to a cell loop
  with `(cell_key, idx)` composite identity threaded through; **single-cell is
  N=1** — no separate cross-cell branch, the fusion path IS the general path,
  and the three query shapes (scoped / cross-MNO / release-diff) collapse to
  one code path differing only in what `_resolve_cells` returns. Five embedded
  design calls:
  1. Fusion method = sort by rerank score, NOT RRF — valid only because
     LLM-as-judge emits absolute 0-100 (corpus-independent). Swapping to the
     dedicated cross-encoder /rerank backend later would need score
     normalization (the method is coupled to the rerank backend's score
     semantics).
  2. Balanced retrieval = `per_cell_top_n` per cell, rerank the union
     (FR-multi-3) → cost = N_cells × per_cell_top_n rerank calls. The cost
     pressure that keeps raising the /rerank TODO; make per_cell_top_n a knob.
  3. NO cross-cell dedup — same req_id in two cells (release-diff: R2's vs R3's
     req:LTEAT:5.1) must both survive to top_k. Composite (cell_key, doc_id)
     keeps them distinct; a naive doc_id dedup would silently break
     release-diff. Dedup only within a cell (fan-out handles it).
  4. Expand once (query-level LLM), DF-filter per cell (corpus-stat-dependent,
     nearly free).
  5. `_rerank_pool` reuses the existing batch/per-call rerank verbatim, re-keyed
     on the Candidate (carrying cell_key) instead of a bare idx. The reranker
     never knows about cells.
- **Grounded `_resolve_cells`** (part of D-DRAFT-10). FR-multi-6 cross-product:
  target MNOs (named → those; none → ALL, per FR-10) × requested releases
  (named → those; none → latest-per-MNO via `_order_key` max on the CELL LABEL,
  never release_date). Three helpers: `_order_key` ("Feb2026"→(2026,2)),
  `_normalize_release` (human phrasing → cell label; unparseable → miss),
  `_latest_release` (max by order key). Returns `(resolved, unresolved)` —
  missing cells are surfaced (fail-VISIBLE at query, mirror of D-DRAFT-5's
  fail-LOUD at ingest), so FR-multi-5 can show "Mar2026: not available" rather
  than silently shrinking the query. Caller errors only if resolved is empty.
  Lives in sandbox/sira_query (cell concept is SIRA's); consumes the
  reused-from-core QueryIntent — clean D-DRAFT-8/9 boundary.

### In progress

- Architecture design is complete end-to-end. The identity model runs with no
  hand-waving: infer_metadata_from_path → cell label → _order_key/_latest_release
  → _resolve_cells → fusion loop → (mno, release, doc_id) results. Ten draft
  decisions staged (D-DRAFT-1..10).

### Next

- `/switch-phase development` — implement, in dependency order:
  1. **Adapter `--multi-cell`** (upstream of everything — cells don't exist
     until it partitions trees by (mno, release) into <db_root>/<mno>__<rel>/).
  2. **`_extract_releases` .finditer fix** in core/src/query/analyzer.py (the
     one curated-module change; release-diff depends on it).
  3. **Cell-loop orchestrator** (batch) + service `_bm25`→cell-dict (runtime).
  4. **`_resolve_cells` + fusion loop** in sandbox/sira_query.
  5. **MMMYYYY ingest validation** in/beside infer_metadata_from_path
     (fail-loud on non-matching dirs).
- web-side FR-multi-5 surfacing (resolved + unresolved (mno, release) per lane)
  comes after the service returns the provenance.

### Flags

- **`_extract_releases` .finditer fix is a core-module change** — the only
  piece of this strand that touches a curated MODULE.md module
  (core/src/query). Needs normal module rigor + arguably its own
  requirements-traceability note; it improves NORA's native query path too,
  not just SIRA.
- **Rerank cost compounds** — per-(MNO,release) cells + balanced per-cell
  retrieval means cross-cell queries rerank N_cells × per_cell_top_n
  candidates. Carried + amplified from prior sessions; the dedicated-/rerank
  backend TODO is now the highest-leverage perf item for this strand.
- **release_date trap, restated** — _resolve_cells / _order_key sort strictly
  on the cell label (MMMYYYY). Any implementer reaching for the tree's
  free-form release_date for ordering is wrong (D-DRAFT-5).
- **NORA-side observation (not this strand's fix):** `core/src/query/pipeline.py`
  defaults to `MockQueryAnalyzer()` even when an LLM is configured — NORA's
  native query path doesn't auto-upgrade to `LLMQueryAnalyzer`. D-DRAFT-9 adds
  a `make_query_analyzer` selection helper that fixes this for the SIRA path;
  flagging that NORA's own pipeline should adopt the same selector separately
  (kept as an observation per the user, not changed in multi-mno-sira).
