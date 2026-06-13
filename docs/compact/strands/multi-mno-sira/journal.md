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
