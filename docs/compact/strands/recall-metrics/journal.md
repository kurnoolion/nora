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

## 2026-11-15 — PR #32 review round

### Done this session
- Rebased on `main` (#31): keep-both in `playground.py`, `test_web_sira_intent_routing.py`, `web/MODULE.md`; `MAP.md` merged on its own.
- Review 1: SIRA's copies of pinned ids count as packed, so packed = fetched when nothing was cut (`d3a7492`).
- Review 2: report page sets the intent cell as text (`27005cf`).
- Review 3 (our call): cited pins counted apart as `pinned_cited`, out of `cited`; `None` when only pins were cited (`d3a7492`). D-DRAFT-3.
- Nits: `compute` best-effort; "ranked" not "BM25 ranked"; tooltip names pins and below-last-k; report skips non-object rows, URL-quotes the DB path, keeps a second same-minute run (`-2`); Pinned column on the page and CLI table.
- design.md §2, §3, §4, §6 brought in line; MODULE.md for web and eval.

### Next
- Force-push `recall-metrics`; reply on #32 naming the pins choice.
- If #33 lands first: `MAP.md` keep-both on the next rebase (`/regen-map`).

### Flags
- Full suite: 46 failures in this checkout, identical on `main` (environmental). None from this branch.
- Rows stored before this change keep pins inside `cited`; the report's by-intent trend mixes the two definitions until those rows age out of the window.
- Local `node` is broken (Homebrew `llhttp` dylib missing); the report JS was syntax-checked with JavaScriptCore.
