## 2026-11-08 — Bug reports as compliance, floor sweep, repeatable eval report

### Done this session
- Compliance keeps its own checklist prompt (MEETS / VIOLATES / UNCLEAR, overall verdict); separate from the planned compliance-tracking work (D-DRAFT-10).
- Pasted bug reports asking for a verdict classify as compliance (`_VERDICT_ASK`, checked right after change); negated "nothing changed" is not a release change; six `kb*` dev samples (77 total, labels reviewed) (D-DRAFT-9).
- Compliance prompt v3 judges the observed device behaviour; local spot-check 4/5 (miss: IMS re-registration case).
- Reviewed baseline: rules 0.753, Kev alone 0.753, confidence-routed 0.870, change bar met. Floor sweep: plateau 0.40–0.50, floor stays 0.50 (D-DRAFT-11).
- Label review page (`core/src/eval/label_review.html`) with stale-save guard.
- `python -m core.src.eval.intent_report` + `eval-report` skill; reports in `reports/intent/<time>_kev-floor_<set_id>/` (D-DRAFT-12).
- Filed #23 (translate a finished answer) and #24 (recall@10/20/30/40 per intent).

### Next
- Push + PR.
- #24 recall per intent.
- Spot-check bug verdicts on the served model.
- Held-out intent check on real feedback questions (in a deployment).

### Flags
- Kev alone misses `change` (4/10) and pasted bug reports: those labels follow our own conventions, and the option descriptions don't spell them out. Sharpening the descriptions is a cheap experiment, not done.
- The analyzer still sets `query_type=RELEASE_DIFF` on any "changed" substring; left as is (judged minor).
- Drift-check skipped this session (last: 2026-10-25); consider `/drift-check dev-full` before merge.

## 2026-11-12 — PR #25 review round 1, redaction, merge main

### Done this session
- Review fixes: History routing equals main with every intent switch off (D-DRAFT-13); id_lookup answers stored (lane in feedback_db.LANES); prompts override read only with the switch on; env example documents the four new switches and the analyzer's blocking decision call; run_local.sh stops only its own PIDs and refuses busy ports (D-DRAFT-15); system font stacks; strict History store open failure raises RHD-E005 (D-DRAFT-14); public REQ_ID_PATTERN; id_lookup fence longer than any backtick run inside.
- Redaction for the public repo: the intent report records decision provider and model only; tests use {MNO_A}/{MNO_B}/{MNO_C} and fake releases; strand docs carry no hardware, ports, names or org framing.
- regen-map scoped to files this branch changed (D-DRAFT-16); origin/main merged; intent report re-run at a clean commit with the current dataset hash, numbers unchanged (best 0.870 at floors 0.40–0.50).
- PR description updated with a "Review fixes (round 1)" section.

### Next
- Re-review of PR #25.
- #24 recall per intent (stash on recall-metrics).

### Flags
- Commit c932a61's message (pushed) carries org framing; left, not rewritten.
- Structure drift in web files this branch did not touch (jobs, team_mode, model_discovery) left for main.
- 44 test failures on macOS also fail on a clean main (#26).
- The 2026-11-08 report is still committed next to the re-run; drop it if one run per dataset is wanted.
