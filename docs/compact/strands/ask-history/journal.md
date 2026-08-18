# ask-history — journal

## 2026-08-18 — Strand opened

- Idea: revisit past questions. Each ask already persists a `test_feedback` row and
  is readable at `/ask/s/{row_id}` (PR #6), so history is a client-side index over
  links we already mint.
- Branch `ask-history` cut from `ask-page-ux` (NOT main — `/ask/s` is not on main).
- Decisions (user): stack on ask-page-ux now; one entry per lane answer (flat list,
  badged — identical to per-question for SIRA-only users, and gives engineers the
  lane-compare workflow); two-pane layout; unlimited retention with clear +
  per-entry delete.

### Implementation
- **Recording contract (no server/SSE change):** `_answer.html`'s answer wrapper
  gains `data-share-path` + `data-lane`, guarded by `{% if row_id %}`. The Ask
  page's recorder scans `#test-answer [data-share-path]` after the SSE done swap
  and unshifts one entry `{q, path, lane, ts}` per match into localStorage
  `nora-ask-history`. The question is captured from the submitted FormData, not
  read back from the DOM, so editing the box after asking can't mislabel history.
  A render error carries no share path → nothing recorded, which is correct.
- **Fragment reuse:** `shared.html` split into `_shared_body.html` (meta + answer +
  citations) and the page shell. New `GET /api/ask/s/{row_id}` returns the body
  only; both routes go through one `_render_stored_ask()` helper, and the
  defensive JSON decode is now the shared `_decode_json_column()` instead of a
  closure duplicated per route.
- **History page** `GET /ask/history` (`test/history.html`): two panes — left list
  paginated 10/page with lane badge, local timestamp, per-entry ×; right pane
  fetches the fragment. Clear-history with confirm. Unlimited retention.
  Stored text is inserted via textContent escaping, never as HTML — the entries
  are user-authored strings.
- **Stale entries:** a 404 from the fragment renders "no longer available" plus a
  Remove-from-history button, rather than a blank pane. Happens whenever the store
  is reset or points at a different env_dir.
- Verified live: `/ask/history` 200, `/api/ask/s/7` 200 body-only (no html/nav
  chrome), `/api/ask/s/999999` 404; a real ask emitted
  `data-share-path="/ask/s/13" data-lane="nora"`.
- Tests: fragment renders the partial (vs the page), fragment 404s, history page
  renders. 15 pass in test_web_playground.py; full suite 1756 passed (same 8
  pre-existing macOS test_web_config failures).
