# eval-studio-ux-fix

**Status:** in-flight
**Opened:** 2026-08-14
**Landed:**
**Assignees:** kurnoolion
**Target modules:** web
**Active phase:** development

## Summary

Address UX feedback collected from users on the web eval studio (Test /
team-eval pages). Specific issues get enumerated in the journal + Notes as
they're triaged; fixes scoped to the `web` module.

## Feedback backlog

<!-- one line per user-reported UX issue; check off as fixed -->
- [ ] **Expand-requirement in the Eval Studio picker** — on a small monitor the
  MNO→plan→requirements list (`eval_studio/_picker_reqs.html`) truncates title
  (`max-width:340px`) + body (`r.text[:160]…`) with no way to read the full
  requirement. Add a per-row expand to read complete text. Reference: the sira
  enrichment-review row expand (`enrich_review/_row.html` native
  `<details>/<summary>` + full text in a scrollable `<pre>`), which hit a
  400+-row perf issue solved with CSS-grid rows + `content-visibility: auto`
  + `contain-intrinsic-size` + offset pagination (`_TABLE_PAGE=100`) — apply
  the same perf posture here since `reqs_for_plan` is uncapped.

## Notes

<!-- appended to over the strand's lifetime -->
