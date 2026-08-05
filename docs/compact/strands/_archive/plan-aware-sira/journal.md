## 2026-05-27 — Pipeline recovery + retrieval-quality debugging

### Done this session
- Recovered the failed full-enrich run: salvaged ~13h of doc enrichment via
  index rebuild + resume (the enrich loop had completed all 13974 docs; only
  the apply-to-index step panicked on a stale 11848-doc index).
- Fixed three recovery failure modes:
  - stale-index `doc_id out of range` panic → guarded by adapter wipe flags
  - `index/best` symlink FileNotFoundError → stale cached eval/baseline made
    the index rebuild skip; adapter `--wipe-stale-index`/`--wipe-all-derived`
    now also clear eval/ + retrieval/ (commit c69945e)
  - "No doc enrichments found" → full-resume (enriched_count==0) skips the
    apply block, leaving best.jsonl dangling; added `sira_incremental promote`
    to reconstruct enrichments/doc/<run>.jsonl from the run's kept file
    (commit c69945e, +4 tests)
- Verified enrichment completeness: doc 119/124 (96%), section 1918/2002 (96%),
  NOT-PROCESSED=0 across all granularities. all_filtered rows are expected
  DF-filter attrition on broad rows, not gaps.
- SETUP.md: added "Re-ingesting after corpus growth" workflow (incremental /
  full-rebuild / promote) + troubleshooting rows (commit 90c5fb7).
- Made query-enrichment temperature configurable, default 0.0 (was hardcoded
  0.4) — commit fead3a6.

### In progress
- Tuning summarize-query retrieval on the live :8040 service.

### Next
- Test NORA_SIRA_EXPANSION_WEIGHT=0 for a deterministic retrieval baseline;
  confirm the correct chunk surfaces on raw BM25 alone.
- Decide expansion strategy: off / cached / seeded — given the MoE backend
  can't be made deterministic in-path.
- Design plan-aware routing: fire fan-out only when a query is plan-scoped
  (named-plan target), not unconditionally on every matching pointer row.

### Flags
- Backend LLM (MoE, …A3B…) is non-deterministic even at temperature 0 — any
  LLM call in the ranking path (query enrichment, rerank) injects irreproducible
  variance into retrieval. This is a structural constraint, not a config bug.

## 2026-05-27 — Deterministic retrieval: root cause isolated + config validated

### Done this session
- Isolated the stochasticity empirically (work PC, "Summarize ADD flow"):
  - `EXPANSION_WEIGHT=0` did NOT make retrieval deterministic (1/5 runs
    identical) — `search_with_expansion` still feeds the stochastic expansion
    terms into BM25 candidate selection, reordering tied candidates.
  - Added `NORA_SIRA_QUERY_ENRICH_ENABLED` (commit 28d76f4) to skip the
    enrichment call entirely; weight-0 alone is insufficient.
  - The DOMINANT source turned out to be **rerank** (it had been left enabled):
    an LLM call that re-scores + re-sorts every candidate. Disabling it made the
    5× loop print "same as prev" — fully deterministic. Rerank scores crossing
    the NORA-side pin threshold (PIN_MIN_SCORE=30) differently each run also
    explained the earlier 1-good / 2-no-answer / 5-broad split.
- Validated config (all live LLM out of the ranking path, fan-out off):
  RERANK_ENABLED=false, QUERY_ENRICH_ENABLED=false, FANOUT_ENABLED=false
  (EXPANSION_WEIGHT=0 is then a no-op). Result for "ADD flow": correct chunks
  top-ranked, reproducible, no pointer-row pollution. Offline doc-enrichment
  still benefits retrieval (applied to the index at load — deterministic).

### Next
- Run a real "Summarize PLAN_X" in this vanilla config to decide whether
  plan-summarize is acceptable without fan-out, or whether query-type routing
  is now the priority.
- Implement query-type routing: fan-out + pointer rows ON for plan-scoped
  queries, OFF (and ideally pointer rows excluded from retrieval) for
  feature/concept queries.

### Flags
- Vanilla mode (fan-out off) and plan-aware mode (fan-out on) want OPPOSITE
  settings and are controlled by one global flag. Until query-type routing
  exists, the operator must choose per workload. With fan-out off, pointer rows
  remain in the index and can surface as useless id-list chunks if they rank
  (confirmed they don't for feature queries, but they would for plan-name
  matches).
