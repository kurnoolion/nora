---
name: eval-report
description: Run an evaluation report and file it under reports/ with the time, git commit and dataset hash, so runs can be correlated later. Today it covers the intent confidence report (Kev's confidence floor against accuracy on the intent dev set). Use when the user asks to re-run the intent eval, check the confidence floor, or produce an eval report after a change to the rules, the dataset, or the decision model.
---

Produce a dated, committed report for an evaluation, through the script that
computes it. The script does the numbers; this skill gets the environment
ready, runs it, reads the result back to the user, and proposes the commit.

## Reports available

| Report | Script | Output folder |
|---|---|---|
| Intent confidence floor | `python -m core.src.eval.intent_report` | `reports/intent/<YYYY-MM-DD-HHMM>_kev-floor_<set_id>/` |

Each folder holds `report.html` (the charts, open it in a browser) and
`run.json` (every number plus `meta`: `created_at`, `git.commit`, `git.dirty`,
`dataset_sha256`, `decision` endpoint and model, `floor`). Rows carry sample
ids and labels only, no question text.

## Procedure

### 1. Check the decision model is up

```
curl -s -m 5 http://127.0.0.1:8009/v1/models
```

No answer: ask the user to start Kev (`sandbox/run_local.sh` with `KEV_DIR`
set starts it), or for the endpoint of the one they want measured. Don't
measure a different model than the user means.

### 2. Free memory for it

On a laptop, Ollama holding a large model at a big context starves Kev, and
its calls time out. Check `ollama ps`; if a model is loaded, tell the user and
unload it with `ollama stop <model>` (it reloads on the next question).

### 3. Run

```
NORA_DECISION_PROVIDER=systemone NORA_DECISION_BASE_URL=http://127.0.0.1:8009 \
NORA_DECISION_TIMEOUT=60 python -m core.src.eval.intent_report
```

Options: `--set <path>` for another labelled set, `--floor <f>` for the floor
in use (default `NORA_DECISION_CONFIDENCE_FLOOR`, else 0.50),
`--out-root <dir>` to write elsewhere. A failed decision call stops the run
and writes nothing; fix the endpoint and re-run rather than reporting a
partial result.

### 4. Report back

Read the new `run.json` and tell the user, outcome first:
- best accuracy and the floors it holds over, and accuracy at the floor in use;
- whether the change bar is met at that floor;
- the confidence above which Kev at least matches the rules;
- what changed against the most recent earlier folder for the same report,
  if there is one (compare `summary`; name the git commits of both runs).

Give the report path. Offer to publish `report.html` as an artifact if they
want a link to share.

### 5. Propose the commit

Propose committing the new folder on the current branch (no push). Only
commit reports from synthetic sets such as `intent-dev-v1`: the repo is
public. A run on an internal or real-traffic set stays out of the repo.

## Rules

- The script owns the numbers. Never hand-edit `run.json` or the HTML; a
  wrong number means fix the script and re-run.
- `git.dirty: true` in a run means it measured uncommitted code; say so when
  reporting it.
- Adding a new report means a new script with the same output shape
  (`run.json` with `meta`, a self-contained `report.html`) and a row in the
  table above.
