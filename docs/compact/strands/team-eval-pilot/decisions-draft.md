# team-eval-pilot — draft decisions

Draft decisions for this strand. Promoted to canonical `DECISIONS.md` with real
`D-XXX` IDs at `/land-strand` time.

---

## D-DRAFT-1 — Extend `feedback_db.py` rather than build a parallel `team_pilot/` module

**Context:** The merged /test page needed structured per-(question x lane)
event logging with score + categories + comment. A `core/src/web/feedback_db.py`
already existed (the legacy thumbs-up/down log) with an aiosqlite async pattern
matching the rest of the web layer, an `/api/test/feedback` endpoint already
wired, and a schema overlap of ~60% with the new fields needed. A first attempt
in this session built a parallel `core/src/team_pilot/` module (sync stdlib
`sqlite3`, separate file, separate tests) before the existing infrastructure
was discovered.

**Decision:** Delete the just-built `team_pilot/` scaffold; extend the
existing `feedback_db.py` (additive columns + new `record_user_feedback`
method) and update `playground.py` in place.

**Why:** Single source of truth for /test feedback; no parallel near-duplicate
schema for analysts to disambiguate later. Async-consistent with the rest of
`core/src/web/` — the sync stdlib `sqlite3` path the parallel module used
would have introduced an inconsistent pattern. The existing
`/api/test/feedback` endpoint pipeline is reused rather than parallelled.
Alternatives considered: (a) keep team_pilot/ parallel with its own DB file —
rejected as two-DB drift over time; (b) migrate `feedback_db.py` *into*
`core/src/team_pilot/` — rejected as a heavier change that touches the
working legacy feedback flow.

**Consequences:** The merged-tab fields live alongside the legacy
vote/free_form_feedback fields in one table. Module boundary for "test-page
feedback" stays in `core/src/web/`, not extracted. Future analyses query a
single table. If the pilot's needs ever diverge sharply from the legacy
feedback flow (e.g. different retention policy), the consolidation might be
revisited.

---

## D-DRAFT-2 — Additive schema migration (8 new nullable columns + PRAGMA-driven ADD COLUMN) over schema rewrite

**Context:** The legacy `test_feedback` schema (id, timestamp, section,
question, answer, citations_json, vote, free_form_feedback,
query_elapsed_ms, llm_model, metadata_json) was missing fields the merged
tab needs (lane, user_name, retrieved_ids, reranked_ids, cited_ids,
user_score, user_categories, lane_config). A redesign could have cleaned up
overlaps (e.g. citations_json vs cited_ids; metadata_json vs lane_config) at
the cost of migrating existing rows; the additive path keeps all legacy
data interpretable.

**Decision:** Add the 8 new columns to the existing `test_feedback` table,
all nullable. CREATE TABLE includes them for fresh DBs. For DBs created
before the merged tab existed, `FeedbackStore.initialize()` reads
`PRAGMA table_info` and runs ADD COLUMN for any missing column (idempotent
on re-run). The `lane` index is created after `_ensure_columns` because
SQLite can't index a column that doesn't exist yet — learned during testing.

**Why:** Back-compat: legacy rows stay queryable unchanged; the existing
flows (requirement_bot / sira_retrieval section URLs) keep working. No
data migration step required — DB upgrades transparently on the next
service start. The overlaps (citations_json + cited_ids; metadata_json +
lane_config) are accepted as the price of additive migration; analysis SQL
treats the new columns as authoritative for the merged tab and falls back
to the legacy columns for legacy rows.

**Consequences:** Two near-duplicate views of citations live in one table
(rich dicts in citations_json; flat req_id list in cited_ids). Schema is
slightly more sprawling. The "fresh DB has all columns; legacy DB
upgrades" path is exercised by tests, so the upgrade behavior is
reproducible. Future schema changes follow the same pattern: column
additions go to `_MERGED_TAB_COLUMNS` (or a successor list) +
`_ensure_columns` picks them up.

---

## D-DRAFT-3 — Two methods on FeedbackStore (`record_user_feedback` new, `record_feedback` unchanged) over overloading the legacy method

**Context:** The legacy `record_feedback(row_id, vote, free_form_feedback)`
always updates both columns; passing `vote=None` deliberately clears a
prior vote. The merged tab needs to set user_score + user_categories +
comment + user_name without touching `vote` (the merged tab doesn't expose
up/down). A unified method would have to distinguish "field not passed" from
"field passed as None" — typically via a sentinel object — to preserve the
legacy clear-vote semantics while letting the merged path leave vote alone.

**Decision:** Add a new `record_user_feedback(row_id, *, user_score,
user_categories, comment, user_name)` method for the merged tab. Leave
`record_feedback` exactly as it was. The merged method's SQL UPDATE
explicitly excludes the `vote` column (only touches user_score,
user_categories, free_form_feedback for the comment, and user_name via
COALESCE so a re-submit without a name doesn't blank it).

**Why:** Two methods read cleaner than a sentinel-based dispatch. The
legacy method's tests + its existing call site in `playground_feedback`
keep working with zero changes. Re-submits on the merged tab cleanly
overwrite the merged fields without ever clobbering a legacy vote that
might be on the row (defensive invariant tested explicitly).

**Consequences:** Two methods with overlapping concerns ("user feedback on
test-page row") will need parallel updates if either flow gains a new
common field. The `free_form_feedback` column is shared between flows
(legacy free-form comment + merged-tab comment) — accepted because the
semantic is the same.

---

## D-DRAFT-4 — `lane` as a new column distinct from `section`

**Context:** The existing `section` column identifies the user-facing
tab/route (`'requirement_bot'`, `'sira_retrieval'`). The merged tab needs
to record *which retrieval pipeline answered* for a given (question,
section='merged') pair. Overloading `section` with values like
`'merged_nora'`/`'merged_sira'` would have collapsed the two concepts into
one column.

**Decision:** Keep `section` as the tab/route identifier (now also accepts
`'merged'`) and add `lane` ('nora'|'sira', NULL for legacy rows) as a new
column. The merged tab inserts `(section='merged', lane='nora')` and/or
`(section='merged', lane='sira')` rows. The row-creation invariant is
**one row per checked lane** — both checked → two rows; one checked → one
row; none checked → form-level rejection.

**Why:** `section` is the *where the question was asked from*; `lane` is
the *which retrieval pipeline answered*. Mixing them would lose the
ability to run cross-lane comparisons cleanly (the headline analysis "where
did NORA outperform SIRA on the same question?" becomes a JOIN on `question`
filtered by `lane`, not a string-prefix decomposition).

**Consequences:** Two fields to set/check rather than one. Legacy rows
have `lane=NULL`, which is meaningful (the legacy tabs predate the merged
view, and didn't distinguish a "pipeline" from the tab itself). Adding a
third pipeline later (e.g. a `'nora-rerank-v2'` lane) needs a CHECK
constraint update in both schema and the Python validator — currently
hardcoded to `('nora', 'sira')`.

---

## D-DRAFT-5 — Template architecture: conditional include in `_answer.html` + per-lane pre-render to string + outer container

**Context:** `_answer.html` is 503 lines and rendered from nine code paths
across `playground.py`. The body is largely lane-agnostic (a small SIRA
preamble is conditioned on `sira_results is defined`); the bottom feedback
widget was lane-specific (vote up/down only). Building the merged tab
required two things: per-column rendering of the existing answer body (to
preserve display fidelity) AND a different feedback widget per column
(score + categories vs vote).

**Decision:** Two-part template structure:
- The feedback widget at the bottom of `_answer.html` was replaced with a
  conditional include (`{% if feedback_mode == 'merged' %}
  {% include "test/_feedback_merged.html" %}{% else %}
  {% include "test/_feedback_legacy.html" %}{% endif %}`). The legacy
  widget moved verbatim into `_feedback_legacy.html`. New
  `_feedback_merged.html` carries the score + categories + comment form.
- The merged container (`_merged_answer.html`) is a thin two-column
  Bootstrap wrapper that receives **pre-rendered HTML strings** per lane
  (computed in Python via a new `_render_template_to_string` helper that
  re-uses the shared Jinja2Templates env + root_path injection). Each
  lane's `_answer.html` is rendered with `feedback_mode='merged'` plus
  the lane-specific context, then composed into the container.

**Why:** The conditional include is a one-line change to `_answer.html` that
preserves display fidelity (none of the 500-line body is touched). Per-lane
pre-render-to-string was picked over Jinja `{% with %}` variable
enumeration because `_answer.html` consumes ~20 context variables — listing
them all in a `with` block per lane would be brittle and easy to drift. The
render-to-string path lets the endpoint own the per-lane context shape
without templates needing to know.

**Consequences:** Two feedback widget files instead of one. The merged tab
accepts some visual nesting (border-in-border, duplicate Q heading per
column) since `_answer.html` keeps its own wrapper — accepted for the pilot
as the strictest preservation of "current display content"; can be polished
later if team flags clutter. The render-to-string helper is small but
introduces a new pattern: per-lane fragments built in Python, composed in
Jinja. Used here, may be reused if other multi-pipeline comparisons land.

---

## D-DRAFT-6 — Merged-tab UX posture: responsive layout + fault-isolated lanes + replace tab nav

**Context:** Three small UX/reliability shape-decisions on the merged tab
that were each load-bearing for the pilot's usefulness:

- *Layout:* both lanes checked vs one checked is a meaningful UX state.
- *Reliability:* SIRA's pipeline can fail (service down, LLM timeout) while
  NORA still works (or vice versa). The Ask should still return useful
  output for the lane that succeeded.
- *Navigation:* the existing /test page had a section-tab nav
  (`requirement_bot`, `sira_retrieval`) that the merged tab would render
  obsolete from the team's perspective.

**Decision:**
- **Responsive layout** — `_merged_answer.html` sizes each column at
  `col-md-6` when both lanes are rendered, `col-md-12` when only one.
  Single-lane queries get full width rather than half-empty space.
- **Fault-isolated lane runners** — `asyncio.gather(return_exceptions=False)`
  (each runner catches its own exceptions and returns `{"error": "..."}`).
  In the merged branch, lanes with an error render a Bootstrap alert in
  their column; lanes that succeeded render normally + still insert their
  `test_feedback` row. One lane's failure cannot block the other.
- **Replace tab nav** — `index.html` no longer renders the section-tab UI.
  The merged form is the sole entry point from the UI. The legacy section
  values are still accepted by `/api/test/ask` (back-compat for direct
  POSTs / bookmarked URLs), but the page no longer generates them.

**Why:** Per the user's instruction ("I just want one tab"), the team's
mental model is one form with two lanes, not "pick a tab then ask." A
responsive layout makes the single-lane case feel intentional, not
half-broken. Fault isolation is the right posture for an evaluation tool
specifically — comparing whatever did come back is more useful than
nothing, and the lane error itself is data worth seeing.

**Consequences:** Legacy section URLs are unreachable from the UI; they
linger as endpoints only. Eventual cleanup pending if no bookmarks depend
on them (carried in journal Next). The fault-isolation pattern means a
silent partial failure is possible — a team member could read a half-empty
two-column result without noticing the error alert. Accepted: the alert is
visually loud (Bootstrap `alert-danger`), and analyses can spot rows where
one lane succeeded without the other.
