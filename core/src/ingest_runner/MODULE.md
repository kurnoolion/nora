# ingest_runner

**Purpose**
The host-side runner behind self-serve ingestion (strand self-serve-ingestion, D-DRAFT-1): a long-lived process on the build host, run as the build user, that claims `ingest*` jobs from the web's jobs database and executes them the way the runbook does by hand — the cycle driver's verbs for one cell, the converter, publish, a staging store, Stage 1 of the golden eval, the gate record — then the flip, the rollback and the abandon on request. It writes every line of progress back into the same database, so the web's job page streams it unchanged. Standard library only: the host needs python3 and the docker CLI, nothing from `requirements.txt`.

**Public surface**
- CLI `python -m core.src.ingest_runner.ingest_runner_cli --config config/ingest_runner.json [--once | --check] [-v]` — the service entrypoint (`docker/ingest-runner.service.example` is the `systemd --user` unit). `--once` runs one job and exits (exit 1 when it failed); `--check` validates the config and opens the jobs DB. Exit 2 on a bad config (`IRN-E001`) or an unopenable DB (`IRN-E002`).
- `config.RunnerConfig` — `load(path)` / `from_dict(d)`; required keys `jobs_db`, `docker_dir`, `builds_wiring`, `requirements_dir`, `labels_root`, `registry_root`, `work_dir`, `golden_dir`, `stack_project`, `stack_env`, `staging_project`, `staging_env`; optional `cycle_env` (the `NORA_*` layout vars `cycle.sh` reads), `share_mappings` (`windows` UNC prefix → `host` mount; the ingestion wiring env's `NORA_SHARE_MAP=\\SERVER\Share=/host/mount;…` line is merged in by `load()` and wins on a duplicate prefix — `parse_share_map`, `read_env_value`), `store_url` / `staging_store_url` (in-container service URLs), `staging_healthz_url`, `prompt_scheme`, `seed_build`, `eval_tolerance` (0.03), `baseline_recall`, `verify_n` (25), `overlay_db` | `overlay_root`, `document_extensions`, `abandon_removes_documents` (true), `poll_seconds`, `heartbeat_seconds`, `detached_poll_seconds`, `detached_timeout_seconds`, `webhook_secret`, `webhook_hosts` (exact host or `.suffix`; `webhook_host_allowed`), `webhook_timeout_seconds`. Derived: `nora_builds_root`, `active_file`, `build_dir(build)`, `sira_build_dir(build)` (`<build>/out/sira`, or `<NORA_BUILDS_SIRA_ROOT>/<build>` for the older layout — the same rules as `cycle.sh`), `resolve_source(path)` (UNC or a host path under a mapped share → host path; `ConfigError` otherwise). `config/ingest_runner.example.json` is the template.
- `db.RunnerDB(path)` — the runner's `sqlite3` view of the jobs DB (schema from `core.src.web.jobs_schema`, applied on open): `claim_next(runner)` (oldest submitted job of the runner types, claimed by one guarded UPDATE), `heartbeat`, `job_status`, `set_job`, `finish_job`, `append_log`, `logs`, `get_ingest`, `update_ingest`, `last_served_build()` (the seed of the next cycle), `baseline_recall()` (Stage-1 recall of the last passed gate).
- `plan` — `STAGES` (`documents`, `start`, `parse`, `prompts`, `taxonomy`, `enrich`, `verify`, `promote`, `convert`, `publish`, `eval`, `gate`), `RETRY_FROM`, `Ctx`, `run_ingest(ctx)` (from the start or from `params.retry_from`; a retry from `enrich` passes `--retry-failed`, batched — single-request mode is the administrator's `--single`, D-DRAFT-14), `run_flip`, `run_rollback`, `run_abandon`, `run_purge` (every artifact of a dead ingest, by its record: baton, release set via `publish purge`, snapshot, work dir, build and a separate SIRA tree, golden run, documents unless the active set serves the cell; a failed removal fails the job with the list and leaves the state; the row stays, `purged`), `heal_best_link(ctx)` (before convert: a resumed enrichment that found every document enriched leaves `enrichments/doc/best.jsonl` dangling; the runner runs `sira_incremental promote` for the cell and fails loud if it still dangles); `StageError`, `Cancelled`. Job params: `{"ingest_id": …, "retry_from"?: stage}`.
- `verdict` — the gate rule (D-DRAFT-4): `recall_avg(report)`, `gev_errors(report)`, `read_report(golden_dir, run_id)`, `latest_recorded_recall(golden_dir, exclude)`, `decide(recall, baseline, tolerance, errors) -> (verdict, detail)`.
- `execute.Exec.run(argv, env, cwd, on_line, stdin) -> Result(rc, lines)` (output streamed line by line); `CommandError`.
- `webhook.deliver(url, payload, secret, timeout, post, sleep) -> (delivered, note)` — one JSON event `{event: "ingest.<state>", ingest_id, mno, release, state, stage, verdict, label, previous_label, error, at}` per state change of an ingest that named a `callback_url`; `X-NORA-Event` and, with a secret, `X-NORA-Signature: sha256=<HMAC-SHA256 of the body>`; three attempts (a 4xx is final). `Ctx.set_state(state, **cols)` is the one place a state changes and the webhook fires (`NOTIFY_STATES`); an unlisted host is logged, never posted to; delivery never fails the job.
- `runner.Runner(cfg, db, ex, sleep, healthz, post)` — `run_once()` (claim and run one job; `None` when the queue is empty), `run_forever()`, `stop()`, `run_job(job)`; a heartbeat thread refreshes `jobs.heartbeat_at` while a job runs.

**Invariants**
- One job at a time per runner; a job is claimed by one `UPDATE … WHERE status = 'submitted'`, so two runners on one database never run the same job.
- Nothing is reimplemented: every stage runs the command the runbook shows (`cycle.sh` verbs, `sandbox.cell_db_convert`, `core.src.stores.publish`, `core.src.eval.golden_cli`, `golden_release_cli advance`) and the job log carries their output verbatim.
- Writes on the host: the requirements dir (one new cell), the build roots through `cycle.sh`, the work dir, the registry root through `publish`, and the jobs DB. Never the served files, never the web's other databases.
- Detached cycle phases are followed through the build's `CYCLE.json` and the phase log to its `CYC-EXIT rc=` line, as `cycle.sh status` reads them; a non-zero rc fails the stage.
- The staging store is always taken down after the eval, pass or fail; its failure to come down is a warning, not a job failure.
- A cancel (the web sets the job `cancelled`) is honoured between stages and while waiting on a detached phase; the cycle in flight is left for `abandon`.
- The gate is Stage 1 only and baseline-relative: the last passed ingest, else `baseline_recall` from the config, else the newest recorded golden run other than this one, else the run passes and seeds the baseline. A `GEV-E` error always fails.
- A flip runs only for a `staged` ingest with verdict `pass`; a rollback only for a `served` one with a previous label; an abandon only for a `failed`, `cancelled` or `staged` one; a purge only for an `abandoned`, `rolled_back`, `failed` or `cancelled` one (`PURGEABLE_STATES`), never for a row whose label or build another live ingest also carries (`RunnerDB.ingests_sharing`; names restart after a by-hand cleanup), and the registry refuses to purge an active set. `publish flip --force` stays a by-hand waiver.
- No requirement text in the runner's own log lines; what the commands print is theirs.

**Key choices**
- A host process over a Docker socket in the web container (D-DRAFT-1); a `systemd --user` unit of the build user on the one host that builds and serves (D-DRAFT-3).
- Standard library only, so the service runs on the system python without the project's virtualenv; the schema it shares with the web lives in `core.src.web.jobs_schema`, which imports nothing else.
- The jobs DB is the queue: the web's state directory is a bind mount, so the file is the contract; no broker, no HTTP between web and runner.
- Every compose one-shot the runner starts (stack, staging, or the eval run) carries `JOB_UID`/`JOB_GID` = the runner's own ids unless the shell or that env file names them (`_job_user`): compose defaults `user:` to root, and a serving or staging env has no job user, so eval runs and registry writes would land root-owned on the host. `cycle.sh` applies the same default for its one-shots (`id -u`/`id -g`).
- Explicit `-v` mounts on the one-shot containers (`/data/serve`, `/data/labels`, `/data/work`, `/data/corrections`) rather than new compose services: the runbook's commands, with their paths fixed.
- The build id and the label are one string, `<release>-<mno>-<yyyymmdd>-<n>`, where `n` is the first same-day number no build directory, promoted snapshot (`labels_root`) or registry release set carries (labels are immutable; a removed build does not free its label). If the label is taken by promote time, promote moves to the next free one and records it on the ingest; the build keeps its name.
- The seed of a cycle is the build of the last flipped ingest if its directory still exists under the builds root, else `seed_build` (D-DRAFT-5). A build removed by hand is logged and skipped, never a failed `start`.

**Non-goals**
- No web routes, no templates: the page and the API live in [web](../web/MODULE.md).
- No parsing, enrichment, publishing or scoring logic of its own.
- No cross-host promote (`serve-push.sh`); version 1 builds and serves on one host.
- No new MNO: the first release of an MNO is ingested by hand (profile choice, prompt derivation).
- No Stage 2 of the golden eval; it stays the experts' tool.

**Depends on**
[web](../web/MODULE.md) (`jobs_schema` only — the DDL and the job types). Runtime: `docker/cycle.sh`, the compose file and the stack and staging env files, the batch and store images.

**Depended on by**
None — a leaf service. The web reads what it writes (the `ingests` table and the job rows and logs).

**Deferred**
- A skill or script client over `POST /api/ingest` (D-DRAFT-2).
- Cross-host promote.

<!-- BEGIN:STRUCTURE -->
_Alphabetical, regenerated by regen-map._


`config.py`
- `CTR_CORRECTIONS` — constant — pub
- `CTR_ENV` — constant — pub
- `CTR_LABELS` — constant — pub
- `CTR_SERVE` — constant — pub
- `CTR_WORK` — constant — pub
- `ConfigError` — class — pub
- `DEFAULT_STORE_URL` — constant — pub
- `ENV_SHARE_MAP` — constant — pub
- `RunnerConfig` — dataclass — pub
  - `active_file` — property — pub
  - `build_dir` — method — pub
  - `from_dict` — classmethod — pub
  - `load` — classmethod — pub — The JSON config, plus the share mapping from the ingestion
  - `merge_env_share_map` — method — pub — Add the mappings of a `NORA_SHARE_MAP` value; an env entry
  - `nora_builds_root` — property — pub
  - `resolve_source` — method — pub — Map a submitted share path (UNC, or a host path under a mapped
  - `sira_build_dir` — method — pub — The build's SIRA cells: `<build>/out/sira/` (the build's last
  - `template_values` — method — pub — Keys whose value still looks like the example config's
- `ShareMapping` — dataclass — pub
- `parse_share_map` — function — pub — `\\SERVER\Share=/host/path;...` → mappings. Raises ConfigError on a
- `read_env_value` — function — pub — The value of `KEY=value` in a compose-style env file ('' when the
- `webhook_host_allowed` — function — pub — `allowed` entries: an exact hostname, or `.example.test` for that

`db.py`
- `RunnerDB` — class — pub
  - `__init__` — constructor — internal
  - `_connect` — method — internal
  - `append_log` — method — pub
  - `baseline_recall` — method — pub — Stage-1 recall of the newest ingest whose gate passed.
  - `claim_next` — method — pub — Claim the oldest submitted job of the given types; None if none.
  - `finish_job` — method — pub
  - `get_ingest` — method — pub
  - `heartbeat` — method — pub
  - `job_status` — method — pub
  - `last_served_build` — method — pub — The build of the newest ingest that was flipped (served, or
  - `logs` — method — pub
  - `seen` — method — pub — Record that this runner polled now (and what it runs).
  - `set_job` — method — pub
  - `update_ingest` — method — pub
- `now_iso` — function — pub

`execute.py`
- `CommandError` — class — pub
  - `__init__` — constructor — internal
- `Exec` — class — pub — Run a command; every output line goes to `on_line` as it arrives.
  - `run` — method — pub
- `Result` — dataclass — pub
  - `text` — property — pub

`ingest_runner_cli.py`
- `main` — function — pub

`plan.py`
- `Cancelled` — class — pub
- `Ctx` — dataclass — pub
  - `batch_oneshot` — method — pub
  - `cell` — property — pub — The cell's name, `<mno>/<release>` (one spelling everywhere).
  - `check_cancelled` — method — pub
  - `compose` — method — pub
  - `cycle` — method — pub
  - `job_id` — property — pub
  - `log` — method — pub
  - `run` — method — pub
  - `set_state` — method — pub — Change the ingest's state, then tell the callback URL, if any.
  - `wait_detached` — method — pub — Follow a detached phase to its `CYC-EXIT` line, streaming the log.
- `STAGE_FNS` — constant — pub
- `StageError` — class — pub
- `_CELL_RE` — constant — internal
- `_REL_RE` — constant — internal
- `_http_ok` — function — internal
- `_next_build_id` — function — internal
- `_publish_json` — function — internal
- `_publish_mounts` — function — internal
- `_run_name` — function — internal
- `_staging_healthz` — function — internal
- `_wait_healthz` — function — internal
- `_work_cells` — function — internal
- `heal_best_link` — function — pub — A resumed enrichment that found every document already enriched
- `run_abandon` — function — pub
- `run_flip` — function — pub
- `run_ingest` — function — pub — Run the stages from the start, or from `params.retry_from`.
- `run_rollback` — function — pub
- `st_convert` — function — pub
- `st_documents` — function — pub
- `st_enrich` — function — pub
- `st_eval` — function — pub
- `st_gate` — function — pub
- `st_parse` — function — pub
- `st_promote` — function — pub
- `st_prompts` — function — pub
- `st_publish` — function — pub
- `st_start` — function — pub
- `st_taxonomy` — function — pub
- `st_verify` — function — pub

`runner.py`
- `DISPATCH` — constant — pub
- `Runner` — class — pub
  - `__init__` — constructor — internal
  - `run_forever` — method — pub
  - `run_job` — method — pub — Run one claimed job to its end; returns the final job status.
  - `run_once` — method — pub — Claim and run one job; None when the queue holds none.
  - `stop` — method — pub
- `_summary` — function — internal
- `logger` — constant — pub

`verdict.py`
- `decide` — function — pub — `(verdict, detail)`; verdict is `pass` or `fail`.
- `gev_errors` — function — pub
- `latest_recorded_recall` — function — pub — The newest run under `<golden_dir>/runs/` with Stage-1 results,
- `read_report` — function — pub
- `recall_avg` — function — pub

`webhook.py`
- `Poster` — constant — pub
- `_WAITS` — constant — internal
- `deliver` — function — pub — POST the payload; `(delivered, note)`. Retries on a connection
- `http_post` — function — pub
- `sign` — function — pub
<!-- END:STRUCTURE -->
