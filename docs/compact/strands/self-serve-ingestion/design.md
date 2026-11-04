# Self-serve ingestion — design

Status: draft for review, 2026-11-03. Field facts (cycle timings, host
user, mounts) are still to be folded in; nothing here depends on them
except the numbers the page will show.

## 1. Goal

A member of the requirements team ingests a new release of an existing
MNO from the web: they copy the release's documents to the shared folder,
open the Ingest page, give the folder, the MNO and the release month, and
press Start. The system runs the whole cycle on that one cell, scores the
staged set with the golden eval, records the gate, and shows a Serve
button and, after the flip, a Roll back button. No Linux shell, no cycle
verbs, no container names.

Out of scope (rulings 2026-11-03): a new MNO (its first release is
ingested by hand, including the prompt-derivation step); a Windows-side
skill (D-DRAFT-2: a later thin client over the same API); cross-host
builds (see §9).

## 2. Shape

Three parts, two of them new:

| Part | Where | New or changed |
|---|---|---|
| Ingest page and API | `core/src/web/routes/ingest.py`, templates, team-mode allowlist | new |
| Ingest runner | `core/src/ingest_runner/` (package with `ingest_runner_cli.py`), run on the host as a service | new |
| Cycle driver | `docker/cycle.sh`: `--yes` on the two gates, `--seed <prev-build>` on `start` | changed |

The web never starts a container (D-DRAFT-1). It validates the request,
writes one job to its own jobs database, and streams that job's log as it
does for pipeline jobs. The runner, a process on the build host under the
build user, claims the job from the same database file (the web's state
directory is a bind mount), runs the cycle verbs and the publish steps as
the runbook does by hand, and writes each stage's progress and report
block back into the job log. The page reads only the database.

```mermaid
sequenceDiagram
  autonumber
  actor T as requirements team member
  participant W as nora-web (container)
  participant J as jobs DB (web state dir, bind mount)
  participant R as ingest runner (host service)
  participant CY as cycle.sh
  participant P as publish (nora-batch one-shot)
  participant ST as staging nora-store
  participant G as golden_cli (nora-batch one-shot)
  participant S as serving nora-store

  T->>W: POST /api/ingest {source, mno, release, note}
  W->>W: validate (path, cell new, MNO served, no cycle in flight)
  W->>J: job(type=ingest, params) + ingests row
  R->>J: claim (submitted → running, heartbeat)
  R->>R: stage documents into requirements/<mno>/<release>/
  R->>CY: start <build> --seed <prev-build> --profiles <prev-build>
  R->>CY: parse → taxonomy → enrich --cell → verify-enrich --yes → promote --label
  R->>J: per stage: log lines, CYC block, stage status
  R->>P: cell_db_convert (one cell) ; publish publish --cells-src --env-dir
  R->>ST: up with SERVE_RELEASE_SET=<label>
  R->>G: golden_cli --stack-url <staging> --stage 1
  R->>R: verdict = rule (§6)
  R->>P: publish gate --kind golden-stage1 --verdict --run-ref
  R->>ST: down
  R->>J: status staged; eval summary; verdict
  T->>W: Serve (POST /api/ingest/{id}/flip)
  W->>J: job(type=ingest-flip)
  R->>P: publish flip --label --store-url
  R->>S: (publish reloads and verifies healthz)
  R->>G: golden_release_cli advance --mno --release --yes
  R->>J: status served; previous label
  T->>W: Roll back (POST /api/ingest/{id}/rollback)
  R->>P: publish rollback --to <previous> --store-url
```

## 3. The job, stage by stage

One ingest is one row in a new `ingests` table and one job row per
run (the build, the flip, the rollback, a retry). Stages of the build
job, in order, with the command the runner executes and what reuse
applies. Every command is one the runbook already shows; the runner adds
no pipeline logic.

| # | Stage | Command (host) | Reuse | On failure |
|---|---|---|---|---|
| 1 | stage documents | copy `<share>/<folder>/*` to `requirements/<mno>/<release>/`; refuse if the cell exists | — | job fails before anything runs |
| 2 | open build | `cycle.sh start <build> --seed <prev> --profiles <prev>` | seed hard-links the previous build's `out/` (all cells) and copies its profile bindings, adding the new cell bound to the profile of the MNO's latest cell | CYC-E021 (cycle in flight): refused at submit; here only on a race |
| 3 | parse | `cycle.sh parse --skip-standards` | the lane skips every cell whose IR and tree are newer than the source (D-DRAFT-8 keys), so only the new cell is parsed | failed cells named in the log; Retry re-runs the verb |
| 4 | taxonomy | `cycle.sh taxonomy` | the ledger keeps every plan whose tree sha is unchanged; only the new release's plans are extracted | the verb's own retry: Retry re-runs it for failed docs |
| 5 | enrich | `cycle.sh enrich --cell <mno>__<release>` | the SIRA db-root holds the new cell only; nothing else is enriched | Retry runs `--retry-failed`; a verify FAIL stops here |
| 6 | verify | `cycle.sh verify-enrich --yes` | — | FAIL lines → job fails with the CYC block; Retry goes back to 5 |
| 7 | promote | `cycle.sh promote --label <label> --scheme <scheme>` (local, no `--host`) | the flat label is the converter's input | — |
| 8 | convert | `cell_db_convert --label <serve>/<label> --cells <mno>__<release> --out <work>/cells --verify 25` | one cell | CNV code in the log |
| 9 | publish | `publish publish --serve-root --label --cells-src <work>/cells --env-dir <build> --prompt-scheme` | every other unit is inherited from the active set; Trees are reused by fingerprint; History of this MNO is rebuilt | PUB code; nothing is visible to the stack |
| 10 | eval | staging store up with `SERVE_RELEASE_SET=<label>`; `golden_cli --stage 1 --stack-url <staging> --stack-label <label> --allow-map-mismatch`; store down | — | GEV-E: job fails; the set stays staged |
| 11 | gate | verdict by the rule in §6; `publish gate --kind golden-stage1 --verdict pass|fail --run-ref <run-id>` | — | a `fail` verdict leaves the set staged and the page says why; Serve is disabled |

The build id is `<release>-<mno>-<yyyymmdd>-<n>` (letters, digits, `.`,
`_`, `-` only, as `start` requires); the label is the same string. Builds
are never reused. The baton stays with the cycle driver: a second ingest
is refused at submit while one is in flight.

The flip job runs `publish flip --label --store-url` (the CLI reloads the
store and verifies healthz; PUB-E008 fails the job and the page shows the
rollback button), then `golden_release_cli advance --mno --release --yes`
and records how many samples the text check flagged for review. The
rollback job runs `publish rollback --to <previous label> --store-url`.
Both are small; they run through the same runner so the web still starts
no container.

### Retry and abandon

Retry re-enqueues the ingest from its failed stage: every verb is
resumable and artifact-checked, so re-running it is safe. Abandon runs
`cycle.sh abandon --note` (frees the baton; build dirs stay for the
administrator to remove) and marks the ingest abandoned. A staged but
unflipped set is harmless and is cleaned by `publish gc` later.

### Cancel

Cancel takes effect between stages. A detached phase that is already
running finishes or fails on its own; the runner then sees the cancel
and abandons the cycle. Stopping a running container is the
administrator's command, not the page's.

## 4. The runner

A Python package, `core/src/ingest_runner/`, with a CLI the host runs as
a long-lived service:

```
python -m core.src.ingest_runner.ingest_runner_cli --config config/ingest_runner.json
```

Responsibilities, and only these:

- **Claim.** Poll the jobs DB (`WEB_STATE_DIR/nora_jobs.db` on the host)
  for `job_type in (ingest, ingest-flip, ingest-rollback)` and
  `status = submitted`; claim one at a time with the runner's identity
  and a heartbeat it refreshes while working. The web shows a stale
  heartbeat as "runner not responding" with the service name to restart.
  The DB files are owned by the host user who runs the stacks, with
  owner-only write, so the runner runs as that user (the same user runs
  the cycles today). One ingest at a time is also what the cycle driver
  enforces: `start` refuses while `ACTIVE` points at a build that is
  neither promoted nor abandoned, so the web's "no ingest queued or
  running" check has the driver as its backstop.
- **Execute** the stage table of §3 as subprocesses, in the repo
  checkout the runner was started from (the same commit the images were
  built from; the runner records both, as `start` records image ids).
- **Report.** Tail each phase's log file into `job_logs` (the SSE stream
  the job page already has), append the CYC block at the end of each
  stage, and update the `ingests` row (stage, build, label, run id,
  verdict, previous label).
- **Staging store.** Bring up and tear down the staging nora-store with
  a dedicated compose project and env file (`<stack>-stage`), prepared
  once by the administrator. The eval one-shot joins that project's
  network so `--stack-url` names the service.

What it does not do: parse anything, decide anything the cycle driver or
publish decides, or write outside the build roots, the requirements dir,
the work dir and the jobs DB.

Config (`config/ingest_runner.json`): the jobs DB path on the host, the
repo's `docker/` dir, the builds wiring env, the serve root and store
URL, the staging project name and env file, the share mapping
(`windows → host path`, same shape as the web's `path_mappings`), the
work dir, `seed_build` for the first run (later runs seed from the last
served ingest), the prompt scheme, the eval tolerance (§6), the poll
interval. No secrets: the LLM keys stay in the batch env files the
containers already read.

The share mapping is the runner's own, not the web's. The web's
`path_mappings` names a Linux path inside the web container that the
host does not have (the host mounts the share at a different path), so
the runner maps the Windows share to the host's own mount point and the
web only validates that the submitted path lies under a mapped share.
Stage 1 then copies from the host mount.

Placement and user: see §9, ruling 1.

## 5. Web: page, API, gate

**Routes** (`core/src/web/routes/ingest.py`):

| Route | Does |
|---|---|
| `GET /ingest` | the form (share folder browser via the existing path mapping, MNO from the served cells, release month `MMMYYYY`, note, name) and the list of ingests with their state |
| `POST /api/ingest` | validate, insert, redirect to the detail page; JSON body or form; returns `202 {ingest_id, job_id}` for API callers |
| `GET /ingest/{id}` | detail: stage chips with expected durations, the job's SSE log, the CYC and GEV blocks, the eval verdict with the baseline it was compared to, the buttons the state allows |
| `GET /api/ingest/{id}` | the same state as JSON (what a skill polls) |
| `POST /api/ingest/{id}/flip` · `/rollback` · `/retry` · `/abandon` | enqueue the matching job; each refused unless the state allows it |

**Validation at submit**, in the web, before a job exists: the share
path maps and exists and holds at least one `pdf`/`docx`/`xlsx`; the MNO
is among the served cells (store healthz); the release is a valid
`MMMYYYY` and not already served or staged for that MNO; no ingest is
queued or running; the runner's heartbeat is fresh (else the page says
the runner is down and does not accept). Counts and types are shown
before Start.

**Team-mode gate**: `/ingest` and `/api/ingest` join `_TEAM_ALLOWED`;
the strand's tests run with the gate on. Fully self-service means a
gated member may Start, Serve and Roll back; `submitted_by` is the free
name the pipeline form already takes, recorded on the ingest and in the
publish `flip` row's note.

**Jobs schema**: `jobs` gains `params` (JSON), `runner` and
`heartbeat_at`; `job_type` admits the three ingest kinds. A new
`ingests` table carries the ingest's own state (mno, release, source,
note, build, label, run name, stage, eval run id and summary line,
verdict and baseline, previous label, timestamps). Migration is
additive (`ALTER TABLE … ADD COLUMN` on init).

## 6. The eval gate

Today acceptance is a human comparison against a baseline kept by hand
(architecture §27). Self-service needs a rule. Proposed:

- The runner runs Stage 1 only (no judge model in the loop; Stage 2
  stays the experts' tool).
- The baseline is the Stage-1 `recall_avg` of the last ingest that
  passed the gate on this serve root, stored on its `ingests` row. The
  first self-serve run has none: it passes when the run has no `GEV-E`
  error, and becomes the baseline. The page says so.
- Verdict `pass` when `recall_avg >= baseline - tolerance` and no
  `GEV-E` error; tolerance default `0.03` (repeat runs move about
  ±0.02, §24), set in the runner config. Otherwise `fail`, with both
  numbers on the page. The administrator can still flip by hand with
  `publish flip --force --reason` and the page shows that waiver.
- `--allow-map-mismatch` is passed because the staged set's latest
  release for this MNO is one ahead of the release map until the flip
  advances it. Standard samples ignore release, so they score the new
  release's cells; version samples resolve against the old map and
  still hold. The mismatch is stamped on the run id line.

What the rule does not cover: a release whose requirements changed so
much that standard samples lose their ground truth. The text check at
`advance` flags those samples for review after the flip; the page shows
the count. See §9, ruling 2.

## 7. Cycle driver changes

- `--yes` on `prompts` and `verify-enrich`: skips the prompt and records
  `confirmed_by:<owner> (auto)` in CYCLE.json and the CYC block.
- `start --seed <prev-build>`: hard-links `<prev>/out/{extract,profile,parse,resolve,taxonomy}` into the new build (same filesystem; `cp -al`), then copies `profiles.json` and adds the new cell's binding from the MNO's latest cell. Fails loud (new CYC-E code) when the previous build is missing or the MNO has no cell in it.
- `enrich --cell` already exists. `promote` without `--host` already produces the local label.
- The detached phase's container id is recorded in CYCLE.json (today it is discarded), so an administrator can stop it by name.

These are small and keep every verb runnable by hand; the README's phase
sections get one line each.

## 8. Tests

- `core/tests/test_ingest_runner.py`: the stage planner against a fake
  `cycle.sh` and `publish` (subprocess stubs that write the expected
  artifacts), claim and heartbeat, retry from a failed stage, the
  verdict rule (no baseline, pass, fail, GEV-E).
- `core/tests/test_web_ingest.py`: validation cases, the state machine of
  the buttons, the API JSON, and the team-mode gate ON.
- `core/tests/test_web_jobs.py`: schema migration on an existing DB.
- Field: one real release on the build host, timed per stage; those
  timings become the page's expected durations. Measured on the current
  host for a full cycle (all cells), the figures the page starts from:

  | Stage | Measured | Note |
  |---|---|---|
  | parse | ~15 min | all cells; a seeded cycle parses only the new cell |
  | taxonomy | under 1 min | ledger skips every unchanged document |
  | enrich | ~70 min | one batched run of one cell; requeues included |
  | verify-enrich | seconds | |
  | Stage 1 eval | seconds | the 12-hour figure in the metrics was a full campaign with Stage 2, not the gate |

  So one ingest is about an hour and a half end to end, dominated by
  enrichment. The page shows enrichment as the long stage and the rest
  as short.

## 9. Open rulings

1. **Where the runner lives and as which user.** Recommendation: a
   `systemd --user` service of the build user on the host that holds the
   builds root, the serve root and the serving store, with lingering on.
   Version 1 requires build and serve roots on one host; the cross-host
   promote (`serve-push.sh`) stays a by-hand path. The current host
   holds both stacks' state dirs, the builds roots and the serving
   stores, so the one-host assumption holds. The build user is in the
   `docker` group and has sudo, and lingering is off: a user service
   would stop at logout. The administrator turns lingering on once
   (`loginctl enable-linger <user>`) before the service is installed,
   or installs a system unit with `User=<build user>`; the design works
   with either, and the user unit keeps the runner out of root's hands.
2. **What the eval compares for a cell with no golden samples of its
   own.** Recommendation: the rule in §6 (baseline-relative Stage 1,
   first run seeds the baseline). The host already has a Stage-1 run
   over every served cell on record, so the first ingest can seed its
   baseline from that run instead of passing unconditionally. The new release is covered only by
   standard samples of that MNO until experts add version samples; the
   page states this on the verdict line.

## 10. Order of work

1. Cycle driver: `--yes`, `--seed`, container id (small; testable by hand).
2. Jobs schema, `ingests` table, runner package with the stage planner and the verdict rule (medium).
3. Web page, API, allowlist, tests with the gate on (medium).
4. Docs: web and new runner MODULE.md, README "Self-serve ingestion" section, architecture §20 and §27 updates, decision drafts.
5. Field run on the build host; timings into the page; handover note for the requirements team.
