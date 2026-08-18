# eval-studio-ux-2

**Status:** in-flight
**Opened:** 2026-08-17
**Landed:**
**Assignees:** hanifm
**Target modules:** web
**Active phase:** development

## Summary

Second batch of user UX feedback on the web Eval Studio, focused on making it
easier for users to enter ground-truth data. Continuation of the landed strand
`eval-studio-ux-fix` (archived under `strands/_archive/`, shipped as PR #4 →
d8f9717 / c063567 / ebcd4bf). Fixes scoped to the `web` module.

## Feedback backlog

<!-- one line per user-reported UX issue; check off as fixed -->
- [x] **1 Bulk paste** — direct-add box takes comma/space/newline-separated ids (A)
- [x] **2 Latest-on-conflict** — id in several releases auto-picks the latest (A)
- [x] **3 Cross-MNO easy add** — unqualified paste resolves + latest-picks, no error (A)
- [x] **4 Sort** — sortable columns on board / GT list / picker (B)
- [x] **5 Scrollable** — bounded, scrollable board + GT list (B)
- [x] **6 Copy** — corner Copy on every text box + "Use as golden" draft mover (C)
- [x] **7 Stay-on-save** — golden save keeps the tab/scroll (C)
- [x] **8 Edited flag** — golden_meta.edited + "manually edited" badge (C)
- [x] **9 Highlight selected** — open sample's board row highlighted (added post-plan)
- [x] **10 Edit question in place** — question save no longer resets the tab (C)
- [x] **11 Sample MNO metadata** — tag + board filter + runner --mno (D)

## Notes

<!-- appended to over the strand's lifetime -->
- All 11 items shipped across 4 feature commits (A/B/C/D+9) on branch
  `eval-studio-ux-2`. Item 9 was missed in the plan batching, caught before PR.
- Local-only (never committed): `~/.zshrc` NORA_LLM_* env (deepseek default),
  `.gitignore`. `config/llm.json` deliberately left clean.
