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
- Not yet committed — awaiting user review in the browser.
