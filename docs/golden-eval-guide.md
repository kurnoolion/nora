# Golden Eval Guide

How to run the golden evaluation (FR-38) against a serving stack, read
the compact result block, and inspect a run in detail. One guide for
whoever operates the eval — on a dev box or on a deployment machine
next to the serving stacks.

For designing the *eval set itself* (queries + ground truth in BEIR
shape for SIRA), see `sandbox/EVAL_PREP.md`. For the module contract,
see `core/src/eval/MODULE.md`.

## What the golden eval measures

Expert-curated samples — each a real question, the requirement IDs an
expert says the answer must draw on (ground truth), and optionally a
curated reference answer (golden response) — scored against a live
serving stack in two stages:

- **Stage 1 — retrieval recall.** Each sample's query is POSTed to the
  stack's `/sira-query` endpoint (black-box, over HTTP — the metric
  measures what the stack actually serves). Recall = fraction of
  ground-truth req_ids present in the retrieved results; per-hit ranks
  are recorded, so recall@5 / recall@10 derive from the same run.
- **Stage 2 — judged answer quality.** A candidate answer is
  regenerated from the rows Stage 1 actually retrieved, using the same
  production synthesis prompt, then an LLM judge scores it against the
  expert's golden response (1–10). The judge prompt is versioned
  (`core/src/eval/prompts/judge_v<N>.txt`); scores are comparable only
  within one judge version.

## Where things live

Everything proprietary lives under the runtime env dir, never in the
repo:

```
<env_dir>/eval/golden/
├── samples/gs-NNNN.json      # one file per sample (Eval Studio writes these)
└── runs/<run_id>/            # one dir per run, e.g. 20260101T120000-v2
    ├── report.json           # full detail: per-sample hits/misses/ranks
    └── report.txt            # the redacted GEV compact block
```

Samples are authored and curated in the web app's **Eval Studio**; this
guide assumes the samples already exist.

### Sample status gates what runs

Each sample carries a status:

| Status | Stage 1 | Stage 2 |
|---|---|---|
| `draft` | skipped (`GEV-W001`) | skipped |
| `stage1-ready` | scored | skipped (`GEV-W001` — no golden response) |
| `golden-ready` | scored | scored |

So a runs' scored count is usually smaller than the number of sample
files — every skip is accounted for as a `GEV-W001` warning, never
silent. Teammates' in-progress drafts ride along invisibly until
promoted.

## Running an eval

One invocation scores one stack.

**On a docker deployment** (the normal case), the CLI runs as a
one-shot job in the `nora-pipeline` container, which mounts the pooled
golden set at `/data/env/eval/golden`:

```bash
cd docker
docker compose --env-file .env.<stack> --profile ingest run --rm -T nora-pipeline \
    python -m core.src.eval.golden_cli \
    --env-dir /data/env \
    --stack-url http://host.docker.internal:PORT \
    --stack-label v2 \
    --env-name my-env
```

Notes for this form (field-learned — see "Golden-eval runbook notes"
in `docker/README.md` for the full list):

- `--env-dir` is the **container** path `/data/env`; run artifacts
  print as `/data/env/eval/golden/runs/<run_id>` but land host-side
  under `${GOLDEN_DIR}/runs/<run_id>`.
- `GOLDEN_DIR` must be set in the env file you run under — without it
  the ingest profile mounts the build dir's own empty `eval/golden`
  and aborts with "No golden samples".
- `--stack-url` must point at the **query service**, not the web app
  (the web app's HTML catch-all answers `/healthz` with HTTP 200, so
  the failure mode is a plausible-looking run with n=0 and one
  GEV-E002 per sample). `host.docker.internal` is wired in the compose
  file to reach host-published ports.
- After a code change, rebuild **both** the `nora-pipeline` image and
  the serving images — a stale image surfaces as a missing CLI flag or
  as eval behavior ignoring the change.
- `--stack-url` / `--stack-label` / `--env-name` are **explicit CLI
  arguments — nothing is inherited from the env file**. The
  `--env-file` choice selects the execution environment (the pooled
  `GOLDEN_DIR` mount, the Stage-2 LLM wiring); the scoring target is
  chosen independently per invocation — that's what makes the release
  A/B "same env, two `--stack-url`s" possible. Read the port off the
  target stack's `SIRA_QUERY_PORT` when composing the URL.

**On a dev checkout** (stack reachable directly), from the repo root:

```bash
python -m core.src.eval.golden_cli \
    --env-dir <env_dir> \
    --stack-url http://127.0.0.1:PORT \
    --stack-label v2 \
    --env-name my-env
```

Useful flags:

- `--stage 1` — retrieval recall only (no LLM needed). Default
  `--stage all` runs both stages.
- `--stack-label` — short name stamped into the run id and GEV block
  (e.g. `v1` / `v2`); make it meaningful, it's how you tell runs apart.
- `--top-k N` / `--label OVERLAY` — retrieval overrides passed to
  `/sira-query`.
- `--mno <carrier>` — run only samples tagged with that carrier. The
  filter applies before the eval-set digest freezes, so filtered runs
  key apart from full runs (they are not comparable to them).
- `--llm-model` / `--judge-model` — Stage-2 synthesis and judge model
  overrides (judge defaults to the synthesis model). Provider selection
  follows the standard env resolver chain (CLI > env var >
  `config/llm.json` > default).
- `--judge-prompt-version vN` — pin the judge prompt (default: highest
  version present).
- `--answer-prompt-version` / `--sira-prompt-scheme` — caller-known
  identity fields stamped into the run for comparability (see the `id:`
  line below).

**Stage-2 mode is auto-selected.** If `<env_dir>/out/vectorstore`
exists, Stage 2 regenerates through the full `QueryPipeline`
(`mode=pipeline`). On SIRA-only deployments that never build a graph or
vector store, it synthesizes directly over the rows Stage 1 retrieved
(`mode=sira-rows`) — same production synthesizer prompt. If Stage-2
setup fails, the run continues Stage-1-only and the block prints
`s2: SKIPPED (<reason>)` — a Stage 2 that never ran is always
distinguishable from one that scored nothing.

**Release A/B** = run twice with different `--stack-url` /
`--stack-label`, then compare the two GEV blocks (the `set=` digest
must match verbatim — see below).

Exit codes: `0` clean (W-level skips allowed), `1` hard `GEV-E` errors
occurred, `2` setup failure (no samples / unreadable sample file).

## Reading the GEV compact block

The block printed at the end (and saved as `report.txt`) is the
**chat-pasteable summary** — counts, percentages, digests, and error
codes only, never sample content. Example (generic):

```
GEV my-env v2 2026-01-01T12:00:00 judge=v1
id: fp=abcdef123456 cells=5 code=1234abc scheme=scheme-v2 aprompt=ans-v1 llm=<model> knobs=19@aabbccdd set=39@11223344 fb_pre=0 fb_delta=0
s1: n=39 recall_avg=0.66 r@5=0.43 r@10=0.49 full=19 zero=5
s2: n=38 judge_avg=5.1 judge_med=5.0 mode=sira-rows
err: GEV-E004(1), GEV-W001(27)
```

Line by line:

- **Header** — env name, stack label, start timestamp, judge prompt
  version.
- **`id:` (stack stamp)** — the run's comparability identity, captured
  from the stack's `/healthz` plus caller-supplied fields. Empty fields
  are omitted (empty = "not comparable on this axis", never a guess):
  - `fp=` — served-data fingerprint (12-hex prefix); `cells=` — number
    of per-cell fingerprints behind it.
  - `code=` — serving code version (e.g. git sha).
  - `scheme=` / `aprompt=` / `llm=` — SIRA enrichment prompt scheme,
    Stage-2 answer-prompt version, LLM identity.
  - `knobs=N@digest` — count + 8-hex digest of the stack's retrieval
    knob tuple. Same digest = same knob settings; the full dict is in
    `report.json`.
  - `set=N@digest` — scored-sample count + digest of the frozen eval
    set. **Two runs are comparable only when `set=` matches
    verbatim** (same samples, same ground truth).
  - `fb_pre=` / `fb_delta=` — the stack's LLM-fallback counter at run
    start and its growth during the run. A non-zero delta means some
    answers came from a fallback path, not the primary LLM.
- **`s1:`** — scored sample count, mean recall, recall@5/@10, and how
  many samples had full (1.0) vs zero recall.
- **`s2:`** — judged count, judge mean/median, Stage-2 mode
  (`pipeline` | `sira-rows`); or `SKIPPED (<reason>)` / `n=0`.
- **`err:`** — every error/warning code with its count, or `none`.

### GEV codes

| Code | Level | Meaning |
|---|---|---|
| `GEV-E001` | error | Sample file unreadable / schema-invalid — the load aborts (fail-loud; fix the file). |
| `GEV-E002` | error | Stack `/sira-query` failed or returned no results list. |
| `GEV-E003` | error | Stage-2 pipeline setup problem (e.g. empty vector store). |
| `GEV-E004` | error | Judge failure — missing prompt file, or unparseable verdict after a strict-format retry. |
| `GEV-W001` | warning | Sample skipped, with reason: `draft` status, empty ground truth (Stage-1 unscorable), or no golden response (Stage-2 unscorable). |

`E`-codes fail the invocation (exit 1) — a run with hard errors is not
a clean data point. `W`-codes are bookkeeping for skipped samples.

## Inspecting a run in detail

The GEV block tells you *that* recall is 0.66; to see *why*, inspect
the run dir with the report inspector:

```bash
python -m core.src.eval.golden_report_cli <env_dir>/eval/golden/runs/<run_id> [--misses]
```

On a docker deployment, no container is needed: the inspector is
host-runnable by design — point it at the host-side run dir,
`${GOLDEN_DIR}/runs/<run_id>` (the container path a run prints maps
there).

Per sample, it prints the ground-truth req_ids (with the rank each was
found at, or `MISS`) beside everything retrieval returned in rank
order. Retrieved ids that are ground truth are starred. Example
(generic ids):

```
=== gs-0001  recall=0.50  (1/2 found, 3 retrieved)
    Q: what is foo?
    EXPECTED    STATUS   ACTUAL (rank order)
    REQ-1       hit r2   REQ-9
    REQ-2       MISS     REQ-1 *
                         REQ-8

1/2 samples shown (misses only)
```

The two columns are independent lists aligned for scanning — they are
not row-paired.

- `--misses` — show only samples with at least one missed ground-truth
  entry (the triage view).
- `--samples-dir DIR` — where to read query text from; defaults to the
  run dir's sibling `samples/`, which matches the standard layout.

The tool is read-only and deliberately stdlib-only with no package
imports: you can copy the single file
`core/src/eval/golden_report_cli.py` to any machine with Python 3 and
run it directly (`python3 golden_report_cli.py <run-dir>`) — no repo
checkout needed.

For programmatic digging, `report.json` carries the same data: each
`stage1[]` entry has `hits` (with ranks), `misses`, `retrieved_req_ids`
(rank order), `recall`, `recall_at_5`, `recall_at_10`, and the
retrieval knobs actually used.

## Campaign discipline

A *campaign* is any multi-cell golden run — a sweep over stacks × arms
× repeats, or a repeat series to establish floors — whose cells must be
comparable with each other afterwards. A single ad-hoc run needs none
of this; a campaign needs all of it, because the field record shows
what happens otherwise: a 12-cell sweep whose golden set grew
mid-flight left one usable cell. Each rule below exists because it was
violated once.

### 1. Fix the anchors before launch

Write down, for each stack in the grid, the identity the cells must
carry — read them off `/healthz` right before launch:

- `code=` — serving code version, same on every stack in the grid
  (rebuild + recreate any stack that differs — a campaign never
  compares code versions by accident);
- `fp=` — served-data fingerprint per stack (this is what the campaign
  compares, so it is expected to differ between stacks and must not
  change within one);
- `knobs=N@digest` per **arm** — flip the arm on a stack, read the new
  digest, flip back; you now know both digests before any cell runs.

A cell whose `id:` line disagrees with its anchors is not a data point;
stop and find out why before running the next one.

### 2. Freeze a snapshot; never touch it again

Copy the live golden set into a campaign-local snapshot and point the
runs at it:

```bash
SNAP=${GOLDEN_DIR}/snapshots/<campaign-id>        # e.g. 2026-09-sweep-enrich
mkdir -p $SNAP && cp -r ${GOLDEN_DIR}/samples $SNAP/
```

Run every cell with `GOLDEN_DIR=$SNAP` (a copy of the serving env with
that one line changed — never edit the stack's own env for this). The
snapshot's `set=N@digest` is the campaign's second anchor: **verbatim
identical on every cell**, or the cell does not belong.

The live set stays open to Eval Studio contributors for the whole
campaign — that is the point of the snapshot. A fix or addition made
during the campaign rides the *next* campaign. Re-copying into an
active snapshot, even to fix a known-bad sample, invalidates every cell
already run; if the sample is that bad, abandon the campaign and
re-snapshot.

The snapshot dir doubles as the campaign archive: its `runs/` holds
every cell's report, so nothing has to be collected afterwards.

### 3. Run under the serving env file of the stack being scored

Each cell runs as the one-shot `nora-pipeline` job under
`.env.<stack>` (the stack it scores) — never a builds wiring env. That
file carries the three things a cell needs at once: the pooled
`GOLDEN_DIR` mount (overridden to the snapshot as above), the stack's
compose project so `host.docker.internal:PORT` is the right query
service, and the image-name variables. Before the first cell, probe the
image under that same env file:

```bash
docker compose --env-file .env.<stack> --profile ingest run --rm -T nora-pipeline \
    python -c "import core.src.eval.golden_cli"          # silent = fresh image
```

"No module named …golden_cli" mid-campaign is the stale-image
signature; the probe costs seconds.

### 4. Launch detached, label every cell, log the plan

One detached script per block (all cells of one arm), so a terminal
hang-up cannot end the campaign, with `--stack-label` encoding the grid
coordinate — `v1-on-r2` reads as stack v1, ON arm, repeat 2 — and the
campaign id as `--env-name`. Record the plan in `$SNAP/CAMPAIGN.md`
before launching: anchors (code, fp per stack, knobs digest per arm,
set digest), the grid, who launched, when. It is paste-safe by
construction (digests and counts only) and it is the document the
results are read against.

Campaigns and promotes never overlap: a running campaign holds the
serving host the way an open cycle holds the build machine. A flip
under a campaign changes `fp=` mid-grid and orphans every cell after
it.

### 5. Verify each cell as it lands, flip arms deliberately

For every GEV block that arrives, check the `id:` line against
`CAMPAIGN.md`: `set=` verbatim, `code=` equal, `fp=` equal to that
stack's anchor, `knobs=` equal to that arm's digest, `err:` free of
`GEV-E002` (a stack that went unreachable) and with `GEV-E004` well
below 15% of `s2: n` (judge loss above that makes Stage-2 medians
meaningless). Note the wall-clock per cell after the first one — that,
not any earlier estimate, is the campaign's ETA.

The arm flip between blocks is a by-hand act on every stack in the
grid: edit the knob in `.env.sira-query.<stack>`, `docker compose
--env-file .env.<stack> --profile serve up -d` (recreate — a restart
does not re-read env files), then `curl` the healthz and confirm the
knobs digest is the anchored one for the new arm **and `code=`/`fp=`
did not move**. Only then launch the next block. A flip on one stack
but not the other produces a grid where the arms are not the same
experiment.

### 6. Read results by pooling rules, then refresh the baseline

- Cells pool only when `set=`, `code=`, `fp=`, and `knobs=` all match
  — i.e. repeats of one grid coordinate. Floors are read over pooled
  repeats (min and mean of `recall_avg`, median of `judge_med`).
- Compare **arms on the same stack** (same `fp=`) and **stacks on the
  same arm** (same `knobs=`); never a diagonal.
- A cell excluded by any anchor mismatch is excluded silently — its
  digest already says why; no clean-up of run dirs is needed.

When a campaign establishes what a stack currently serves — the
accepted floor for its label — copy that cell's GEV block to
`${GOLDEN_DIR}/baselines/<stack>.txt`. That file is what a staged
promote's eval is compared against (`docs/ingestion-guide.md` §6f),
so it must always describe the label the stack serves *now*: refresh it
from the first accepted run after every production flip, never from a
`--mno`-filtered or snapshot-of-another-campaign run, and note the
campaign id and date in a comment line above the block.

## The sharing boundary (NFR-8)

- **Chat-pasteable:** the GEV compact block (`report.txt`) and the
  `format_ab_delta` line — counts, percentages, digests, error codes.
- **Local inspection only:** `report.json`, the inspector's output, and
  everything under `samples/` — these carry proprietary queries,
  req_ids, and responses. When discussing a miss remotely, describe it
  structurally ("expected entry absent from top-10; qualifier mismatch
  at rank 3") rather than pasting rows.
