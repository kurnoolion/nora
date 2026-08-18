# ask-page-ux

**Status:** landed
**Opened:** 2026-08-18
**Landed:** 2026-08-18 (via PR from branch `ask-page-ux`)
**Assignees:** hanifm
**Target modules:** web
**Active phase:** development

## Summary

Minor UX feedback on the main **Ask** page (`query.html` at `/query`, served by
`query_page` in `core/src/web/routes/query.py`) — the primary question-answering
surface ("Ask a Question"). Sibling to the landed Eval Studio strands
(`eval-studio-ux-fix`, `eval-studio-ux-2`); different web surface, so its own
branch/PR. Items enumerated in the journal + backlog as they're triaged.

## Feedback backlog

<!-- one line per user-reported UX issue; check off as fixed -->
- [x] **1 Cache the username** — name, correction label and lane selection remembered
  per browser (`localStorage` key `ask-last-fields`) and prefilled on load.
- [x] **2 Collapse the answer metadata** — answers show question → answer → feedback;
  all engineering detail moves behind one "Engineering details" toggle. Failure
  alerts stay above the fold (they explain a missing/degraded answer).
- [x] **3 Declutter the ask form** — Ask sits directly under the question with the
  name inline beside it; retrieval lanes + correction label collapse into "Options".
- [x] **4 Shareable link** — `GET /ask/s/{row_id}` renders a read-only snapshot of a
  stored ask; Share button copies the link. No schema change, so asks already in the
  DB are shareable retroactively.
- [x] **Collapse the ingested-corpus panel** (follow-up) — reference material, starts
  collapsed; still fetches on load so expanding is instant.
- [x] **Enter asks** (follow-up) — Enter submits, Shift+Enter newlines, with a visible
  hint. Guarded against in-flight queries and IME composition. No toggle by design.

## Notes

<!-- appended to over the strand's lifetime -->
- Shipped in 5 commits on branch `ask-page-ux`. Scope: `web` module only, plus one
  new route + template; **no schema or storage change**.
- Deliberate constraint: a shared link reproduces the *normal user view* (question,
  answer, citations, attribution). The engineering internals — chunk text, retrieval
  scores, taxonomy, prompts — are not persisted at ask time (`metadata={}` at
  `playground.py:468`), so they cannot be replayed. User chose not to start storing
  them; revisit only if reviewers ask to see retrieval internals in a shared link.
- Known repo wart, pre-existing and untouched: `test_feedback_db.py` fails with
  "no current event loop" when run after `test_web_playground.py`, and 8
  `test_web_config.py` tests fail on macOS (`/tmp` vs `/private/tmp` symlink).
