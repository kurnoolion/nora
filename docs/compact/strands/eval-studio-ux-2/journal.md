# eval-studio-ux-2 — journal

## 2026-08-17 — Strand opened

- Continuation of landed `eval-studio-ux-fix` (PR #4, merged to main at ebcd4bf).
- Second batch of user UX feedback on the web Eval Studio — theme: make it
  easier for users to enter ground-truth data. Target module: web.
- Branch `eval-studio-ux-2` cut from `main` @ 19f70f3.
- Raw feedback list received (photo of user's notes). Verbatim parse, pending
  clarification before triage:
  1. comma / space separated (input)
  2. duplicate plan — choose the latest plan
  3. cross-mno — easy to add
  4. sort option
  5. scrollable
  6. copy button in the golden response
  7. stay where you are when tapping save-response
  8. capture the metadata that the golden response is edited by the user
  9. highlight the selected qn
  10. be able to edit the qn prompt easily while generating the golden response
  11. capture the mno in the samples to group & validate carrier-specific reqs
- Header note from user: "these are mostly UX and minor and I will do asap."
- Grounding: Stage-2 = "Golden response" tab (curation chat scratchpad → golden
  textarea → Save golden response targets #es-editor whole → resets active tab).
  Query/question edited in the top meta form (query textarea + area + Save).
  Direct-add box takes a single req_id. Board list = samples (_board.html).
- Plan approved (4 batches A→D, one commit each, single PR). Plan file:
  ~/.claude/plans/vivid-soaring-nebula.md. Rhythm: build → self-verify live →
  user validates → commit → next batch.

### Batch A — Smart direct-add (feedback items 1, 2, 3)
- **Bulk paste (1):** `gt_add` splits the single `req_id` field on `[\s,]+` →
  many ids added in one submit through the existing resolve+dedup path. Paste-box
  placeholder updated ("comma or space separated").
- **Latest-on-conflict (2, 3):** unqualified id matching several cells no longer
  errors — auto-picks the latest release via new `req_tree.latest_match()` (max by
  the private `_release_sort_key`; ties → first for determinism). MNO is unique per
  id in practice.
- **Notice (user confirmed):** a muted `gt_notices` line per auto-pick —
  "REQ: matched N cells (relA, relB) — added latest (relB)". Rendered muted (not an
  alert) in `_gt_panel.html`. `_gt_panel_ctx` gained `gt_notices`.
- Summary line: "Added N, M already present"; not-found ids → warning "Not found: …".
- Files: `golden_eval.py` (gt_add, _gt_panel_ctx, +`import re`), `req_tree.py`
  (+`latest_match`), `_gt_panel.html` (notices block + placeholder).
- Tests: updated 3 that encoded the old contract (ambiguity error → latest-pick;
  GEV text → "Not found: …"; dup wording), added `test_direct_add_bulk_split`.
  47 passed (test_web_eval_studio + test_web_req_tree + test_eval_golden).
- Live smoke (env_demo): bulk mixed comma/space → Added 3 + 1 Not found; re-add →
  "Added 1, 1 already present"; auto-qualified to DEMO/Jan2026. (Latest-pick can't
  fire on the single-release demo; unit test covers it.)
- NOT yet committed — pending user live-validation.


### Batch B — sort / filter / scroll (feedback items 4, 5)
- **Scroll (5):** `.es-scroll` (max-height + overflow-y) wraps the samples board
  table and the ground-truth list, so the add form / picker stay in view as lists
  grow. (Picker reqs already had its own scroll box + content-visibility.)
- **Sort (4):** generic attribute-driven client-side sorter added inline in
  `index.html` — a `data-es-sortable` table with a stable id + `data-sort-key`
  headers + per-row `data-sk-<key>`; state held per table id and re-applied on
  `htmx:afterSettle`, so a server re-render of a panel keeps the chosen order. A
  header outside its table (picker toolbar) points back via `data-sort-table`.
  - Board: all 5 columns sortable (id / area / status / GT count / user) for
    consistency — comparator is numeric-aware (GT count int, status by lifecycle
    rank draft→stage1→golden), text otherwise.
  - GT list: sort by req_id (new thead).
  - Picker reqs: sort-by-id toggle button in the toolbar (server filter already
    present).
- Filter: picker's id/title filter already existed — left as is.
- Files: `index.html` (CSS + sorter JS), `_board.html`, `_gt_panel.html`,
  `_picker_reqs.html`.
- Tests: 22 eval-studio route tests pass (templates still render). Sort behaviour
  is client JS — verified markup (ids, data-sort-key, data-sk-*, sorter present)
  live; interaction validated in-browser by the user.
- NOT yet committed — pending user live-validation.
