# ingest_runner

**Purpose**
The host-side runner behind self-serve ingestion (strand self-serve-ingestion, D-DRAFT-1): a long-lived process on the build host, run as the build user, that claims `ingest*` jobs from the web's jobs database and executes them the way the runbook does by hand — the cycle driver's verbs for one cell, the converter, publish, a staging store, Stage 1 of the golden eval, the gate record — then the flip, the rollback and the abandon on request. It writes every line of progress back into the same database, so the web's job page streams it unchanged. Standard library only: the host needs python3 and the docker CLI, nothing from `requirements.txt`.

**Public surface**
- CLI `python -m core.src.ingest_runner.ingest_runner_cli --config config/ingest_runner.json [--once | --check] [-v]` — the service entrypoint (`docker/ingest-runner.service.example` is the `systemd --user` unit). `--once` runs one job and exits (exit 1 when it failed); `--check` validates the config and opens the jobs DB. Exit 2 on a bad config (`IRN-E001`) or an unopenable DB (`IRN-E002`).
- `config.RunnerConfig` — `load(path)` / `from_dict(d)`; required keys `jobs_db`, `docker_dir`, `builds_wiring`, `requirements_dir`, `labels_root`, `registry_root`, `work_dir`, `golden_dir`, `stack_project`, `stack_env`, `staging_project`, `staging_env`; optional `cycle_env` (the `NORA_*` layout vars `cycle.sh` reads), `share_mappings` (`windows` UNC prefix → `host` mount; the ingestion wiring env's `NORA_SHARE_MAP=\\SERVER\Share=/host/mount;…` line is merged in by `load()` and wins on a duplicate prefix — `parse_share_map`, `read_env_value`), `store_url` / `staging_store_url` (in-container service URLs), `staging_healthz_url`, `prompt_scheme`, `seed_build`, `eval_tolerance` (0.03), `baseline_recall`, `verify_n` (25), `overlay_db` | `overlay_root`, `document_extensions`, `abandon_removes_documents` (true), `poll_seconds`, `heartbeat_seconds`, `detached_poll_seconds`, `detached_timeout_seconds`, `webhook_secret`, `webhook_hosts` (exact host or `.suffix`; `webhook_host_allowed`), `webhook_timeout_seconds`. Derived: `nora_builds_root`, `active_file`, `build_dir(build)`, `sira_build_dir(build)` (`<build>/out/sira`, or `<NORA_BUILDS_SIRA_ROOT>/<build>` for the older layout — the same rules as `cycle.sh`), `resolve_source(path)` (UNC or a host path under a mapped share → host path; `ConfigError` otherwise). `config/ingest_runner.example.json` is the template.
- `db.RunnerDB(path)` — the runner's `sqlite3` view of the jobs DB (schema from `core.src.web.jobs_schema`, applied on open): `claim_next(runner)` (oldest submitted job of the runner types, claimed by one guarded UPDATE), `heartbeat`, `job_status`, `set_job`, `finish_job`, `append_log`, `logs`, `get_ingest`, `update_ingest`, `last_served_build()` (the seed of the next cycle), `baseline_recall()` (Stage-1 recall of the last passed gate).
- `plan` — `STAGES` (`documents`, `start`, `parse`, `prompts`, `taxonomy`, `enrich`, `verify`, `promote`, `convert`, `publish`, `eval`, `gate`), `RETRY_FROM`, `Ctx`, `run_ingest(ctx)` (from the start or from `params.retry_from`), `run_flip`, `run_rollback`, `run_abandon`, `heal_best_link(ctx)` (before convert: a resumed enrichment that found every document enriched leaves `enrichments/doc/best.jsonl` dangling; the runner runs `sira_incremental promote` for the cell and fails loud if it still dangles); `StageError`, `Cancelled`. Job params: `{"ingest_id": …, "retry_from"?: stage}`.
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
- A flip runs only for a `staged` ingest with verdict `pass`; a rollback only for a `served` one with a previous label; an abandon only for a `failed`, `cancelled` or `staged` one. `publish flip --force` stays a by-hand waiver.
- No requirement text in the runner's own log lines; what the commands print is theirs.

**Key choices**
- A host process over a Docker socket in the web container (D-DRAFT-1); a `systemd --user` unit of the build user on the one host that builds and serves (D-DRAFT-3).
- Standard library only, so the service runs on the system python without the project's virtualenv; the schema it shares with the web lives in `core.src.web.jobs_schema`, which imports nothing else.
- The jobs DB is the queue: the web's state directory is a bind mount, so the file is the contract; no broker, no HTTP between web and runner.
- Explicit `-v` mounts on the one-shot containers (`/data/serve`, `/data/labels`, `/data/work`, `/data/corrections`) rather than new compose services: the runbook's commands, with their paths fixed.
- The build id and the label are one string, `<release>-<mno>-<yyyymmdd>-<n>`.
- The seed of a cycle is the build of the last flipped ingest, else `seed_build` (D-DRAFT-5).

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
<!-- END:STRUCTURE -->
