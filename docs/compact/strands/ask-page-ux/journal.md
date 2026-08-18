# ask-page-ux — journal

## 2026-08-18 — Strand opened

- Minor UX feedback on the main Ask page (`query.html` @ /query, `query_page` in
  routes/query.py). Target module: web.
- Branch `ask-page-ux` cut from `main` (post eval-studio-ux-2 push).
- Awaiting the feedback list from the user; each item logged here as triaged.

### Feedback list (4 items, triaged)
1. Cache the username on the Ask form.
2. Collapse the answer's engineering metadata by default — normal users want
   question → answer → feedback; citations/chunks/prompts are for engineers.
3. Ask button sits below lane checkboxes + correction label — collapse those and
   move Ask next to the question box.
4. Make a query+response shareable by link (for review/feedback).

Decisions (user): default view = answer + feedback only; form keeps
question + name + Ask; share = read-only snapshot at a plain row id (/ask/s/<id>).

Storage finding (`playground.py:468`): the merged lane persists question, answer,
FULL citations, lane, user_name, model, elapsed, lane_config and retrieved/
reranked/cited **ids** — but `metadata={}`, so chunk text, SIRA cards + scores,
taxonomy, graph candidates and LLM prompts are NOT stored. User chose **no storage
change**: the shared page reproduces the normal user view (which is all a normal
user sees anyway) and works retroactively for rows already in the DB.

### Batch 1 — declutter (items 1, 2, 3)
- **Item 2 (`_answer.html`):** answer body moved above the SIRA preamble; every
  engineering section (SIRA badges/timing, Stage-1 taxonomy, Stage-3 graph scoping,
  cited-by-LLM, RAG chunks, SIRA retrieval cards, citation audit, LLM prompt,
  elapsed footer) wrapped in one Bootstrap collapse `#eng-details-<row_id>` behind
  an "Engineering details" toggle. Failure alerts (`sira_notes`, `synth_error`)
  deliberately kept ABOVE the fold — they explain a missing/degraded answer.
- **Item 3 (`index.html`):** retrieval lanes + correction label moved into a
  collapsed `#ask-options` panel; Ask button now sits directly under the question
  with an Options toggle beside it. Name stays visible (credits feedback). The
  team-restricted hidden `lanes` input is preserved — collapse hides, it does not
  disable, so collapsed inputs still submit.
- **Item 1 (`index.html`):** sticky fields via localStorage `ask-last-fields` —
  name + correction label + lane checkboxes prefilled on load, saved on submit.
  Disabled (team-restricted) lane boxes are never overridden.
- Verified live on env_demo (NORA lane, real ask): rendered answer HTML has
  answer-body at 890, collapse spanning 2140–25479 with Query analysis / RAG chunks
  / LLM prompt / elapsed all inside, and the `/api/test/feedback` form at 25520 —
  outside the collapse, i.e. still visible. Templates compile; div/if/for balanced.
- Test note: `test_feedback_db.py` fails with "no current event loop" when run
  AFTER `test_web_playground.py` — pre-existing pollution, reproduces identically
  on clean main with these changes stashed. Not caused by this strand; left alone.

### Batch 2 — shareable link (item 4)
- **Route** `GET /ask/s/{row_id}` (`playground.shared_answer`): reads the stored
  ask via the existing `FeedbackStore.get_row()`, 404s on an unknown id, decodes
  `citations_json` / `cited_ids` defensively (corrupt JSON degrades to an empty
  list rather than a 500). No new storage, no schema change.
- **Template** `test/shared.html`: read-only snapshot — lane badge, asker, time,
  model, elapsed, the question, the answer through the same `| md` filter, and the
  cited requirements. No feedback form, no re-run (a reviewer must see the answer
  the asker saw). No engineering block: those internals were never persisted.
- **Share button** in `_answer.html` next to the Engineering-details toggle,
  rendered only when `row_id` exists. Copy helper `askCopyShare` lives in
  `index.html`, NOT in the partial — `_answer.html` is injected via innerHTML,
  which never executes inline `<script>` (Bootstrap's data-bs-toggle still works
  because it is delegated). Falls back to a prompt() when the clipboard API is
  unavailable.
- Verified live on env_demo: `/ask/s/7` → 200 with the real question + rendered
  markdown answer + cited requirements; bogus id → 404. Rows 3–6 (from the user's
  own earlier asks, made before this feature) also render — confirming the
  no-storage-change decision makes existing history shareable retroactively.
- Tests: `test_shared_answer_renders_stored_row`,
  `test_shared_answer_unknown_id_is_404`,
  `test_shared_answer_survives_malformed_json`. 12 pass in test_web_playground.py.
