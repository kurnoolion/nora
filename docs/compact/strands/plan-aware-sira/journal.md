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
