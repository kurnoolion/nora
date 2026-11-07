# Self-serve ingestion — design

Status: 2026-11-06. One spelling of a cell everywhere, `<MNO>/<release>` (D-DRAFT-10), the SIRA cells inside the build (D-DRAFT-9) and one wiring variable, `NORA_BUILD_DIR` (D-DRAFT-11), are built; `publish migrate-units` rewrites each stack's registry rows once. Earlier: 2026-11-03, rulings settled (§9) and the host facts folded in.
Built and tested: the cycle driver changes of §7
(`core/tests/test_cycle_driver.py`), the jobs schema and the runner of §4
(`core/tests/test_ingest_runner.py`), the page and the API of §5 with
the ingest token (`core/tests/test_web_ingest.py`, gate on and off).
Docs: README "Self-serve ingestion", architecture §28, the two
MODULE.md files. Left: the field run (§8).

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
| 2 | open build | `cycle.sh start <build> --seed <prev>` | seed takes the previous build's profile bindings, adding the new cell bound to the profile of the MNO's latest bound cell; nothing is copied (every stage is per cell since the taxonomy became per release) | CYC-E021 (cycle in flight): a failed or cancelled ingest still holds the baton; the page names it, Retry or Abandon it first. CYC-E026/E027: bad seed, MNO without a binding |
| 3 | parse | `cycle.sh parse --cell <mno>/<release> --skip-standards` | the lane runs that cell alone | failed cells named in the log; Retry re-runs the verb |
| 4 | taxonomy | `cycle.sh taxonomy --cell <mno>/<release>` | one taxonomy per release (D-DRAFT-8), derived from the cell's own trees into `out/taxonomy/<mno>/<release>/`; the enrichment lane reads that directory | the verb's own retry: Retry re-runs it for failed plans |
| 5 | enrich | `cycle.sh enrich --cell <mno>/<release>` | the build's `out/sira/` holds the new cell only (the SIRA cells are the build's last stage dir, ruling 2026-11-06); nothing else is enriched | Retry runs `--retry-failed`; a verify FAIL stops here |
| 6 | verify | `cycle.sh verify-enrich --yes` | — | FAIL lines → job fails with the CYC block; Retry goes back to 5 |
| 7 | promote | `cycle.sh promote --label <label> --scheme <scheme>` (local, no `--host`) | the flat label is the converter's input | — |
| 8 | convert | `cell_db_convert --label <serve>/<label> --cells <mno>/<release> --out <work>/cells --verify 25` | one cell; first, if the cell's `best.jsonl` dangles after a resumed enrichment, `sira_incremental promote` (field-found on the first retry) | CNV code in the log |
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
  the cycles today). Submits queue and the runner takes them oldest
  first; one cycle at a time is what the driver enforces: `start`
  refuses while `ACTIVE` points at a build that is neither promoted nor
  abandoned, so a failed ingest that still holds the baton blocks the
  queue until it is retried or abandoned, and the page names it.
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

As built (`core/src/ingest_runner/`, MODULE.md there): standard library
only, so the service runs on the host's python3 without the project's
virtualenv; the schema it shares with the web is `core/src/web/jobs_schema.py`,
which imports nothing else. The one-shot containers get explicit `-v`
mounts (`/data/serve`, `/data/labels`, `/data/work`, `/data/corrections`)
rather than new compose services. The stage list has twelve entries:
§3's eleven plus `prompts` (`cycle.sh prompts --yes`) between parse and
taxonomy, which the driver requires before taxonomy. Jobs: `ingest`
(params `ingest_id`, optional `retry_from`), `ingest-flip`,
`ingest-rollback`, `ingest-abandon`. Abandon runs `cycle.sh abandon` when
the cycle in flight is this ingest's build and removes the staged copy of
the documents (the share keeps the originals; `abandon_removes_documents`
turns that off). The baseline order of §6 gained a step: the last passed
ingest, else `baseline_recall` from the config, else the newest recorded
golden run other than this one, else the run seeds the baseline.

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
the runner maps the Windows share to the host's own mount point. The
mapping lives in the ingestion wiring env (`NORA_SHARE_MAP=\\SERVER\Share=/host/mount;…`
in `.env.builds`, ruling 2026-11-03), never in a committed file; the
JSON config's `share_mappings` is an optional second form. The web
validates the shape of the submitted path (a UNC path) and the runner's
documents stage fails loud when it is under no mapped share.

Placement and user: see §9, ruling 1.

## 5. Web: page, API, gate

Access (ruling 2026-11-03): the ingest surface has its own token, given
to the requirements team, separate from the admin token.
`NORA_WEB_INGEST_TOKEN` in the web's env; `/ingest-unlock?token=<it>`
sets an HttpOnly cookie of its own (`nora_ingest`), the way
`/admin-unlock` sets the admin cookie; `team_mode.is_ingest_user(request)`
is true with that cookie or the admin cookie. Every `/ingest*` route
requires it: the API answers 403 without it, the page says to unlock
first. With team mode on, `/ingest`, `/api/ingest` and `/ingest-unlock`
are on the allowlist so a gated member holding the cookie reaches them;
the route check, not the allowlist, is the gate. With no token
configured the surface is closed.

**Routes** (`core/src/web/routes/ingest.py`):

| Route | Does |
|---|---|
| `GET /ingest` | the form (share folder browser via the existing path mapping, MNO from the served cells, release month `MMMYYYY`, note, name) and the list of ingests with their state |
| `POST /api/ingest` | validate, insert, redirect to the detail page; JSON body or form; returns `202 {ingest_id, job_id}` for API callers |
| `GET /ingest/{id}` | detail: stage chips with expected durations, the job's SSE log, the CYC and GEV blocks, the eval verdict with the baseline it was compared to, the buttons the state allows |
| `GET /api/ingest/{id}` | the same state as JSON (what a skill polls) |
| `POST /api/ingest/{id}/flip` · `/rollback` · `/retry` · `/abandon` | enqueue the matching job; each refused unless the state allows it |

**Validation at submit**, in the web, before a job exists: the share
path has the shape of a UNC path (the runner maps it and counts the
documents at its first stage, since the host mount is its, not the
web's); the MNO is among the served cells (store healthz); the release
is a valid `MMMYYYY` and not already served, staged, queued or running
for that MNO. Submits queue (ruling 2026-11-06): the page shows each
queued ingest's position and the runner takes them oldest first, one
cycle at a time; a failed or cancelled ingest that opened a build is
named as holding the cycle until it is retried or abandoned. The
runner's last-seen time is shown, not enforced: a submit with the runner
down waits until it is back.

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

Notification (ruling 2026-11-03, built): clients do not poll. The page
and API clients hold `GET /api/ingest/{id}/events` (server-sent events:
`state` on each change, `done` when no job runs); a submit may carry a
`callback_url` and the runner posts `ingest.<state>` events to it,
signed with HMAC-SHA256 under the runner's `webhook_secret`, to hosts in
its `webhook_hosts` only, three attempts, never failing the job. A skill
has no listener: it submits, keeps the id and asks later; the webhook
feeds a chat or mail bridge for the people.

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

Built 2026-11-03:

- `--yes` on `prompts` and `verify-enrich`: skips the prompt and records
  the confirmation as `<user> (auto)` in CYCLE.json and the CYC block.
  The evidence lines are still printed, so the job log carries them.
- `start --seed <prev-build>`: takes the seed's `profiles.json` as the
  default binding source (`--profiles` still wins) and binds every cell it
  does not cover to the profile of that MNO's latest bound release,
  recorded in the CYC block as `inherited=<cell><-<release>`. Nothing is
  copied (2026-11-06: with the taxonomy per release, every stage is per
  cell and a build holds only the cells it parsed; the earlier copy of
  the previous build's `out/` is gone). CYC-E026 for a seed without
  bindings, CYC-E027 for a cell of an MNO with no binding to inherit;
  both fail before anything is created. Without `--seed` nothing changes:
  bindings are still chosen, not inherited.
- `parse --cell` and `taxonomy --cell`: the lane runs one cell
  (`--mno`/`--release`); the enrich verb sets `NORA_SIRA_TAXONOMY_DIR` to
  the cell's taxonomy directory on the lane's command line; `prompts`
  notes a partial build instead of refusing it; `status` and the enrich
  precondition read the per-cell ledgers.
- The detached phase's container id is recorded with the phase entry in
  CYCLE.json and shown by `status`, so an administrator can stop a
  runaway phase by name.
- `enrich --cell` already existed. `promote` without `--host` already
  produced the local label.

Every verb stays runnable by hand. The README's driver paragraph names
the two flags.

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

## 9. Rulings (settled 2026-11-03)

1. **Where the runner lives and as which user.** A `systemd --user`
   service of the build user on the host that holds the builds root, the
   serve root and the serving store (D-DRAFT-3). Version 1 requires
   build and serve roots on one host; the cross-host promote
   (`serve-push.sh`) stays a by-hand path. The current host holds both
   stacks' state dirs, the builds roots and the serving stores, so that
   holds. The build user is in the `docker` group; lingering is off
   today, so the administrator turns it on once
   (`loginctl enable-linger <user>`) before the service is installed, or
   the service stops at logout.
2. **What the eval compares for a cell with no golden samples of its
   own.** The rule in §6: baseline-relative Stage 1 (D-DRAFT-4). The
   host already has a Stage-1 run over every served cell on record, so
   the first ingest seeds its baseline from that run instead of passing
   unconditionally. The new release is covered only by
   standard samples of that MNO until experts add version samples; the
   page states this on the verdict line.

## 10. Order of work

1. Cycle driver: `--yes`, `--seed`, container id (small; testable by hand).
2. Jobs schema, `ingests` table, runner package with the stage planner and the verdict rule (medium).
3. Web page, API, allowlist, tests with the gate on (medium).
4. Docs: web and new runner MODULE.md, README "Self-serve ingestion" section, architecture §20 and §27 updates, decision drafts.
5. Field run on the build host; timings into the page; handover note for the requirements team.
