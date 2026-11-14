## 2026-11-14 — Silver recall built end to end (#24)

### Done this session
- Branch recreated from main after #25 merged; design rewritten (k=10..40 capped, golden out of scope).
- Step 1: `web/routes/silver_recall.py` — `compute` (recall@k, cited, outside, fetched, packed; best rank on duplicate req_id) and `line`; unit tests.
- Step 2: `_run_select_synth_lane` stores `lane_config["silver_recall"]` (numbers only).
- Step 3: `test/_silver_recall.html` under SIRA answers on the Ask page, shared page and History pane; verified with the team gate on and off, and live on a stored row.
- Step 4: web MODULE.md contract + Structure, MAP tree.
- Step 5: `eval/recall_report.py` + HTML template; CLI `--db --since --bucket --intent --stack`; output `reports/recall/` (gitignored); eval MODULE.md, eval-report skill.
- Percentages round halves up in all three places (card, CLI table, report page).

### In progress
- Rebase onto main (b36ef37 also edits the select-synth lane in playground.py), then push and PR.

### Next
- PR for #24; reviewer confirms lane_config over a new column (design §5).
- Tune top_k / context budget once live numbers accumulate (out of scope here).

### Flags
- 26 `test_cycle_driver.py` failures on macOS (BSD `find -printf`), present on main too; candidate for #26.
