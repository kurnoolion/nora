### D-DRAFT-1 — 2026-11-14 — Silver recall is stored in the feedback row's lane_config, not a new column

**Context.** Each SIRA answer's recall numbers must travel with its feedback row so the answer card, shared page, History pane and a report can read them. The grilling session (Q14) had leaned to a new column on `test_feedback`.

**Decision.** A `silver_recall` key inside the existing `lane_config` JSON: `{recall: {"<k>": share}, cited, outside, fetched, packed}`. Numbers only, no ids or text.

**Why.** Follows the intent precedent (`intent`, `intent_confidence` live there), needs no schema migration, and the shared page and History pane already read `lane_config` back. A new column was rejected for the migration and the second read path.

**Consequences.** The report parses JSON per row instead of aggregating in SQL. Rows from before the change carry no key; the UI shows nothing and the report counts them as skipped. Reviewer to confirm at PR.

### D-DRAFT-2 — 2026-11-13 — The silver recall report lives in eval and reads web's feedback DB as data

**Context.** The report averages numbers that web writes into its feedback DB. Candidate owners: web (owns the DB) or eval (owns `intent_report` and the eval-report pattern).

**Decision.** `eval/recall_report.py` opens the DB read-only with sqlite and does not import `web`. Its test builds the DB from web's `_SCHEMA`. Output goes to `reports/recall/`, gitignored.

**Why.** web already imports eval, so eval importing web would create a cycle; eval already holds the timestamped-report pattern. Reports come from live usage and the repo is public, so they are never committed.

**Consequences.** A data edge (eval → web's table shape) that the import graph does not show; the test is the guard against a renamed table or column. Reports are shared outside the repo, not through it.

### D-DRAFT-3 — 2026-11-15 — Cited named-ID pins are counted apart (`pinned_cited`), not in `cited`

**Context.**
- PR #32 review: a cited pin that BM25 did not return counted as "outside", so questions that name an id showed lower recall by construction. That measures pinning, not search depth, and it mixed into the by-intent trend. The reviewer asked us to choose: keep pins out of `cited` with a separate count, or state the effect in the report.

**Decision.**
- `silver_recall.compute` takes `pinned_ids`; cited ids among them go to `pinned_cited` and out of `cited` (and so out of recall and outside). No recall (`None`) when nothing but pins was cited. The answer line shows "· N pinned" when non-zero; the report gains a Pinned column.

**Why.**
- Silver recall is meant to show how deep in the search the evidence sat; a pin is in context whatever its rank.
- Rejected:
  - **Keep pins in `cited` and explain the effect in the report:** the number still drops for every question that names an id, and the by-intent trend still mixes pinning with depth.

**Consequences.**
- A pin BM25 also returned is still counted apart, not credited to its rank.
- Rows stored before this change keep pins inside `cited` and lack `pinned_cited` (read as 0); the trend mixes both definitions until they age out.
