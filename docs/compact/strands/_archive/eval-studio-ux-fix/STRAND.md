# eval-studio-ux-fix

**Status:** landed
**Opened:** 2026-08-14
**Landed:** 2026-08-14
**Assignees:** kurnoolion
**Target modules:** web
**Active phase:** development

## Summary

Address UX feedback collected from users on the web eval studio (Test /
team-eval pages). Specific issues get enumerated in the journal + Notes as
they're triaged; fixes scoped to the `web` module.

## Feedback backlog

<!-- one line per user-reported UX issue; check off as fixed -->
- [x] **Expand-requirement in the Eval Studio picker** — on a small monitor the
  MNO→plan→requirements list (`eval_studio/_picker_reqs.html`) truncates title
  (`max-width:340px`) + body (`r.text[:160]…`) with no way to read the full
  requirement. Add a per-row expand to read complete text. Reference: the sira
  enrichment-review row expand (`enrich_review/_row.html` native
  `<details>/<summary>` + full text in a scrollable `<pre>`), which hit a
  400+-row perf issue solved with CSS-grid rows + `content-visibility: auto`
  + `contain-intrinsic-size` + offset pagination (`_TABLE_PAGE=100`) — apply
  the same perf posture here since `reqs_for_plan` is uncapped.
- [x] **Picker selection reset on add** — clicking `+` (or `Add selected`) in the
  picker re-rendered the whole `#es-editor`, wiping the picker's MNO/plan/release
  selection + expanded rows + checkboxes. Fix: split the ground-truth panel into
  its own swap target (`#es-gt-panel` / `_gt_panel.html`); gt/add, gt/add-bulk,
  gt/remove swap only that panel + OOB the GT-count badge, never touching the
  picker column. Double-add already guarded server-side by `(req_id, mno,
  release)` in both add paths (single `+` errors, bulk skips) — preserved.
- [x] **Reverse jump: ground-truth entry → picker** — a locate (⌖) button on each
  GT entry drives the picker to that entry's MNO/plan/release so the expert can
  pull more siblings from the same plan. Extracted the picker body into
  `_picker.html` (parameterized by optional `sel_mno/sel_plan/sel_release` +
  `plans/releases/rows`), wrapped in `#es-picker-body`; new GET
  `picker/jump` route renders it fully pre-selected in one round-trip. Composes
  with feedback #2 (add-more targets `#es-gt-panel`, picker stays put).
- ~~Already-added checkmark in the picker~~ — **tried, reverted 2026-08-14.**
  Prototyped (render-time `added_ids` + OOB `<tr>` flips on add/remove). Reverted
  at the user's call: the added-state marker created a two-way sync liability
  (delete-all and remove-here vs unselect-there paths could leave the picker's
  checkmarks stale) for little gain. Server-side `(req_id, mno, release)` dedup
  already prevents real double-adds, so the picker stays a plain `+` list.

## Notes

<!-- appended to over the strand's lifetime -->

Landed on 2026-08-14 with 0 promoted decisions.
