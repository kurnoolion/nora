## 2026-11-03 — strand opened; scope settled; design study pending

### Done this session
- Strand opened (19433d9). Scope: the requirements team ingests a new release of an existing MNO from the web, without the Linux CLI.
- Rulings: fully self-service, the flip included, with rollback one click away; the existing golden eval runner is the gate; new MNOs stay out of scope (their first release is ingested by hand on the command line); the same operation is exposed as an API endpoint so an LLM-side skill can orchestrate it later.
- Ground surveyed: the cycle is already scripted phase by phase in `docker/cycle.sh`, with single-cell parse and enrichment; the manual prompt-derivation step applies to new MNOs only; the web has the job queue, streamed logs, the shared-folder browser with path mapping and the team-mode gate; publish refuses a flip without a gate and keeps the previous set for rollback.
- The one architectural problem named: the web runs in a container and cannot start the sibling batch containers. Mounting the Docker socket into the web container rejected; a host-side runner that consumes the web's job queue and runs the cycle verbs chosen instead (D-DRAFT-1). The web page comes first; a skill is a thin client over the same API (D-DRAFT-2).
- Applicability lists stepped back to TBD behind this strand (shape recorded in the archived rel-history journal).

### In progress
- Field facts to gather before design: cycle timings per phase for one cell, driver state, host user and service model, jobs DB and log locations, shared-folder mount and path mappings, golden sets per cell and last eval runs, build env keys and profile inheritance.

### Next
- Design: the host-side runner (placement, user, service model); the page and `POST /api/ingest`; one job with named stages, streamed logs and per-stage report blocks; the staged set with Serve and Roll back; the golden eval recorded as the gate.
- Code only after two rulings: where the runner lives and under which user; what the eval compares for a cell that has no golden set yet.

### Flags
- A brand-new cell has no golden set. Until one exists the gate can only be the enrichment verify plus the publish dry run. Needs a ruling before the gate is coded.

## 2026-11-06 — built end to end, field-run served, restructure landed

### Done this session
- Design settled (8774937): host-side runner over the web's jobs DB; the two rulings taken (runner as a `systemd --user` service of the build user; Stage 1 baseline-relative gate seeded from the recorded run).
- Cycle driver: `--yes` on the gates, `start --seed` (bindings only), `parse --cell`, `taxonomy --cell`, detached container ids, wiring lines, executable bits; tests with a fake docker.
- Runner `core/src/ingest_runner/` (stdlib only): twelve stages, flip, rollback, abandon, signed webhooks with a host allowlist, the `best.jsonl` heal before convert, `--check` refusing template values, share mapping from the ingestion wiring env.
- Web: Ingest page and API, own token and cookie, event stream, submits queue with positions and a blocking notice, runners table.
- Pipeline: taxonomy per release (no cross-release supersession, build-level consolidation); SIRA cells at `<build>/out/sira/`; one cell spelling `<MNO>/<release>` with `publish migrate-units`; one wiring variable `NORA_BUILD_DIR`.
- Field: installed on the host; one release staged with a passing gate (recall 0.71 against 0.68), served and verified; a question on the Ask page resolved to the new cell.
- Causes found: a leftover `NORA_SIRA_BATCH_MAX_REQS=1` made enrichment take fourteen hours; SIRA's resume path leaves `best.jsonl` dangling; the wiring env lacked `STORE_ROOT=`; three scripts had no executable bit; five error codes were unregistered.

### In progress
- Round four on the host: rebuild the three images, `NORA_BUILD_DIR` in the envs, `migrate-units` on both registries, one release staged through the page on the restructure.

### Next
- After round four: Serve from the page on the new names, then the rollout to the requirements team (token link, the README section).
- `/land-strand` once the field run on the restructure is clean: D-DRAFT-1 to D-DRAFT-13 promote.
- FR-6 still says "unified taxonomy"; reword at the next `/drift-check design` (per release, consolidated per build).

### Flags
- Three sandbox strict-verify tests fail at HEAD and before this strand (a resolved parse-error batch is classified as a note, the tests expect a warning); not touched here.
- A failed or cancelled ingest that opened a build holds the cycle driver's baton until retried or abandoned; the page names it.


## 2026-11-08 — the layout settled, Purge, field rounds six to eleven

### Done this session
- Serving containers mount no build (`/data/env` and the db mount default to `docker/empty`); `NORA_BUILD_DIR` is ingestion wiring only; one serving env and one ingestion wiring env per stack, never a symlink; `v1`/`v2` are per-stack build roots, each with a base build; the six served cells copied into v2's base and v1's five renamed on the slash spelling.
- Runner: seed falls back to `seed_build` when the last served build is gone; same-day build id and label skip names a snapshot or release set still carries; promote moves to a free label; every one-shot runs as the runner's own user unless the env file names a job user (the eval run included); `cycle.sh` gives the serving-only compose variables a placeholder and the same job-user default.
- Purge, the fourth verb: `publish purge` (REG-E009 for an active set), `ingest-purge` job, state `purged`, administrator-only, refused when another live ingest carries the same names.
- Query analyzer accepts the compact `MMMYYYY` release form.
- Field: the release ingested from zero on the web page by the requirements lead, gate pass, served; Purge used on the dead rows; both guards seen refusing.
- `docs/self-serve-ingestion-guide.md` for the team; drift-check dev-full on the six strand modules plus requirements (FR-47 new).

### In progress
- Nothing on the host. Landing the strand.

### Next
- `/land-strand self-serve-ingestion` (D-DRAFT-1..19).
- stage-graph strand: two rulings open.

### Flags
- Four pre-restructure build directories remain outside the roots by the administrator's choice.
- One abandoned ingest row shares its label with the served ingest; purge refuses it until the served one is dead.
- Pre-existing sandbox strict-verify test failures unchanged.
