# eval-studio-ux-fix — journal

## 2026-08-14 — Strand opened

- Opened for UX fixes in the web eval studio. Target module: web.
- Mission: address user UX feedback on the eval studio.

### Feedback #1 — expand requirement in the picker
- Report: on a small monitor, the MNO→plan→requirements picker list gives no
  way to read a full requirement (title + body are truncated).
- Located: `eval_studio/_picker_reqs.html` — `<table>`, title `text-truncate
  max-width:340px`, body `r.text[:160]…`. Route `golden_eval.picker_reqs` →
  `req_tree.reqs_for_plan` returns ALL reqs for the plan, uncapped; full
  `r.text` already ships to the client (truncation is visual only).
- Reference pattern: `enrich_review/_row.html` native `<details>/<summary>`
  expand → full text in a scrollable `<pre>`. Perf lesson (this strand's
  archived sibling, journal L82): 400+-row jank fixed via CSS-grid rows +
  `content-visibility: auto` + `contain-intrinsic-size` + offset pagination
  (`_TABLE_PAGE=100`). Marginal cost of inline expand here = render only
  (payload already carries the text), so `content-visibility` is the fix.
- Approach chosen (user): inline `<details>` + content-visibility (keep table).
- Implemented + verified live on env_demo:
  - `eval_studio/_picker_reqs.html` — body cell now a native
    `<details>/<summary>`; summary keeps the 160-char truncation, expand
    reveals full `r.text` in a `pre` (pre-wrap, max-height 18rem, scroll) with
    the full title above it. Row gains class `es-req-row`. No new endpoint,
    no round-trip — text was already in the payload.
  - `static/css/style.css` — `#es-req-rows .es-req-row { content-visibility:
    auto; contain-intrinsic-size: auto 3rem }` so uncapped plan listings skip
    off-screen render.
  - Verified: picker partial renders the expand; CSS served; `/eval-studio` 200.
  - Caveat: `content-visibility` on `<tr>` is honored by Chromium; weaker/ignored
    on some engines (degrades to normal render, no correctness risk). If a real
    plan's listing still janks, escalate to the grid rewrite (option B).
- Committed on branch `eval-studio-ux-fix` (d8f9717); pushed to origin.

### Feedback #2 — picker selection reset on add
- Report: clicking `+` (and by extension `Add selected`) reset the whole picker
  selection; it should persist until the user changes it.
- Root cause: the picker column is a child of `#es-editor`, and gt/add,
  gt/add-bulk, gt/remove all re-render `_editor.html` whole (`_editor.html` L1:
  "re-rendered whole after every mutation") → picker rebuilt from scratch.
- Fix (user chose: don't touch the picker DOM):
  - New `eval_studio/_gt_panel.html` = the ground-truth column (list + alerts +
    direct-add + preview), with an OOB `#es-gt-count` badge (guarded by
    `oob_count`, mirroring enrich_review's `oob_pending`).
  - `_editor.html` col-5 now `<div id="es-gt-panel">{% include _gt_panel %}</div>`;
    tab badge count wrapped in `#es-gt-count` (inline, no OOB in full render).
  - `_picker_reqs.html` `+` and `Add selected` retargeted `#es-editor` →
    `#es-gt-panel`. Routes gt_add / gt_add_bulk / gt_remove return `_gt_panel.html`
    via new `_gt_panel_ctx` helper (sets `oob_count=True`).
  - Picker column never in the swap → MNO/plan/release, expanded `<details>`,
    checkboxes and scroll all survive an add/remove.
- Double-add (user concern: `+` a row then include it in Add-all): already
  guarded server-side by `(req_id, mno, release)` in both add paths — single `+`
  returns "already in the ground-truth list", bulk counts it as "already
  present". Refactor changed only the returned template, not the dedup logic.
- Verified live on env_demo / gs-0001: picker add returns GT-panel only (0
  picker markup in response); re-add → dedup error; bulk [dup+new] → "Added 1,
  1 already present"; store has no dupes; full editor still renders picker +
  inline count + no stray OOB span.
- Committed c063567; pushed. PR #4 opened (assigned kurnoolion).

### Feedback #3 — reverse jump: ground-truth entry → picker
- Ask: from a requirement already added, jump the picker to its MNO/plan/release
  so the user can add more siblings from that same plan (reverse of the current
  picker→add flow). User chose: click source = GT entries; affordance = small
  locate (⌖) icon next to ×.
- Implementation:
  - Extracted the picker body from `_editor.html` into `eval_studio/_picker.html`,
    parameterized by optional `sel_mno / sel_plan / sel_release` + `plans /
    releases / rows / sid`. Undefined (falsy) in the editor include → placeholders
    + user-driven cascade unchanged. Editor col-7 now
    `<div id="es-picker-body">{% with sid %}{% include _picker %}{% endwith %}</div>`.
  - New GET `/api/eval-studio/picker/jump` (`picker_jump`) — resolves plans /
    releases / rows for the cell (empty/unknown release → plan's latest, matching
    `picker_releases`) and returns `_picker.html` fully pre-selected in one
    round-trip. Reqs server-rendered directly (no es-picker-changed refetch).
  - `_gt_panel.html` GT entries gain a `bi-crosshair` button (guarded on `e.mno`)
    → `hx-get picker/jump?...` targeting `#es-picker-body`.
- Composes with #2: the jumped picker's `+` / `Add selected` still target
  `#es-gt-panel`, so adding more leaves the picker on the jumped cell.
- Verified live on env_demo / gs-0001: jump renders DEMO/VOLTE/Jan2026 all
  selected + 9 rows, placeholder gone; GT panel shows locate buttons with correct
  jump URLs; full editor still renders the empty picker via the include.
- Committed + pushed with #2 wave (pending commit for #3/#4 below).

### Feedback #4 — already-added checkmark in the picker
- Ask: picker rows for reqs already in the ground-truth pool should show a
  checkmark instead of the + add control.
- Implementation:
  - Row markup extracted from `_picker_reqs.html` into `_picker_req_row.html`
    (`is_added` → green `bi-check-circle-fill` + disabled checkbox in place of
    the + form; row id `es-preq-<req_id>`).
  - Render-time: `picker_reqs` and `picker_jump` compute `added_ids`
    (`_added_ids(sid, mno, release)` = GT req_ids for that cell) → per-row
    `is_added`.
  - Live, without re-rendering the picker (honors #2): gt/add, gt/add-bulk,
    gt/remove emit out-of-band `<tr hx-swap-oob="true" id="es-preq-…">` flips via
    `_oob_picker_row` (add/bulk → checked; remove → + back). htmx 2.0.4 parses
    OOB table rows; a row for a cell not currently shown has no target and is
    silently ignored (cell-safe — no clobber of a different cell's list).
  - Added rows carry no `name="req_ids"`, so select-all / Add-selected skip
    them; the existing `(req_id, mno, release)` dedup still backstops.
- Verified live on env_demo / gs-0001: VOLTE list shows check for 001/002, + for
  003; add 003 → OOB check; remove 003 → OOB +; jump to DATARETRY (all added) →
  all checks + disabled checkboxes, 0 active bulk checkboxes.
- #3 + #4 not yet committed.

### Feedback #4 reverted (2026-08-14)
- User rejected the already-added checkmark: it introduces a two-way state-sync
  liability (delete-all doesn't reflect in the picker; remove-from-GT vs
  unselect-in-picker are two paths that can disagree) for little benefit — the
  picker `+` list plus server-side `(req_id, mno, release)` dedup is enough.
- Reverted all #4 changes, keeping #3 (jump):
  - `_picker_reqs.html` restored to the #1/#2 inline-row version (git checkout).
  - Deleted `_picker_req_row.html`.
  - `_gt_panel.html`: dropped the `oob_rows` loop.
  - `golden_eval.py`: removed `_added_ids` + `_oob_picker_row`, the `oob_rows`
    param on `_gt_panel_ctx`, all OOB wiring in gt_add / gt_add_bulk / gt_remove,
    and the `added_ids` keys in picker_reqs / picker_jump.
- Verified: picker rows plain `+` (0 checkmarks, active checkboxes); #3 jump and
  #2 add/remove intact; no dangling refs.
- Kept for commit: #3 (jump) only. Not yet committed.
