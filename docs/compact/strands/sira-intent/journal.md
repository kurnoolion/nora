## 2026-11-08 — Bug reports as compliance, floor sweep, repeatable eval report

### Done this session
- Compliance keeps its own checklist prompt (MEETS / VIOLATES / UNCLEAR, overall verdict); separate from the client compliance-tracking plan (D-DRAFT-10).
- Pasted bug reports asking for a verdict classify as compliance (`_VERDICT_ASK`, checked right after change); negated "nothing changed" is not a release change; six `kb*` dev samples (77 total, labels reviewed) (D-DRAFT-9).
- Compliance prompt v3 judges the observed device behaviour; local spot-check 4/5 (miss: IMS re-registration case).
- Reviewed baseline: rules 0.753, Kev alone 0.753, confidence-routed 0.870, change bar met. Floor sweep: plateau 0.40–0.50, floor stays 0.50 (D-DRAFT-11).
- Label review page (`core/src/eval/label_review.html`) with stale-save guard.
- `python -m core.src.eval.intent_report` + `eval-report` skill; reports in `reports/intent/<time>_kev-floor_<set_id>/` (D-DRAFT-12).
- Filed #23 (translate a finished answer) and #24 (recall@10/20/30/40 per intent).

### Next
- Push + PR (Hanif).
- #24 recall per intent.
- Spot-check bug verdicts on the served model.
- Held-out intent check on real feedback questions (internal infra).

### Flags
- Kev alone misses `change` (4/10) and pasted bug reports: those labels follow our own conventions, and the option descriptions don't spell them out. Sharpening the descriptions is a cheap experiment, not done.
- The analyzer still sets `query_type=RELEASE_DIFF` on any "changed" substring; left as is (judged minor).
- Drift-check skipped this session (last: 2026-10-25); consider `/drift-check dev-full` before merge.
