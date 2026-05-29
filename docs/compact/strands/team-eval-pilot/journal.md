## 2026-05-28 — Build phase complete: merged-tab live

### Done this session
- Backend foundation (commit f625286):
  - Extended `core/src/web/feedback_db.py` test_feedback schema with 8 new
    nullable columns (lane, user_name, retrieved_ids, reranked_ids,
    cited_ids, user_score, user_categories, lane_config). Idempotent
    upgrade path via PRAGMA-driven ADD COLUMN — legacy DBs migrate in
    place on next service start; row preservation verified.
  - CATEGORIES module constant (4 frozen keys + labels).
  - record_qa extended with optional merged-tab kwargs (legacy callers
    unchanged). New record_user_feedback for 0–9 score + categories +
    comment + user_name; does not touch the legacy vote column. 22 tests.
- Citation + snapshot helpers in playground.py (commit 54b7d58):
  _flatten_cited_ids, _pick_sira_snapshot, async _snapshot_sira_lane_config
  (fault-tolerant: returns {"_error": ...} on failure rather than blocking
  the Ask), _snapshot_nora_lane_config. 10 tests.
- Merged-tab UI + endpoint (commit ae1719f):
  - Templates: extracted _feedback_legacy.html, new _feedback_merged.html
    (score select + 4 checkboxes + comment), _answer.html feedback widget
    became conditional include, new _merged_answer.html (responsive
    col-md-6 / col-md-12), rewrote index.html with the merged form
    (question + NORA/SIRA checkboxes default-checked + optional name +
    Ask). _feedback_ack.html acknowledges merged successes.
  - Endpoint: section=='merged' branch in playground_ask runs enabled
    lanes in parallel (fault-isolated — one lane's failure renders an
    error alert, the other still works), inserts one test_feedback row
    per (question x lane), pre-renders each lane's _answer.html to a
    string, returns _merged_answer.html. playground_feedback dispatches
    on user_score presence (merged path) vs vote (legacy path).
- Work-PC smoke passed for the basic flow.

### In progress
- (none — build phase done, strand transitions to feedback-collection mode)

### Next
- Team begins using the merged tab for ad-hoc questions; feedback rows
  accumulate in <env_dir>/state/nora_test_feedback.db.
- When meaningful feedback volume arrives, run SQL analyses
  (NORA-vs-SIRA score deltas by question; category clustering by lane;
  comment patterns) and journal findings here.
- Decide whether to retire the legacy section-tab URLs
  (?section=requirement_bot / ?section=sira_retrieval) once any
  bookmarks are migrated.
- Deferred (carry forward): export CLI for SQLite → CSV/JSONL sharing
  outside the env_dir.

### Flags
- Visual nesting (border-in-border) inside each lane column was accepted
  for the pilot — the per-lane _answer.html keeps its own border + Q
  heading, which appear inside the merged container's wrapper. Flag for
  polish if the team reports it reads cluttered; otherwise ships as is.
- Lane-config snapshot for NORA is intentionally minimal for the pilot
  (llm_model + query_intent + candidate_count + 5 env vars). Will likely
  need to grow as analyses identify which knobs matter on which questions.
- `core/src/web/MODULE.md` Public surface section does not yet enumerate
  the new `feedback_db.py` additions (`CATEGORIES`, `record_user_feedback`,
  `_MERGED_TAB_COLUMNS`). Deferred to a future `/drift-check dev-module web`
  pass rather than fixing inline this session.
