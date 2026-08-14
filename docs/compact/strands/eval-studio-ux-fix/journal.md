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
- Not yet committed.
