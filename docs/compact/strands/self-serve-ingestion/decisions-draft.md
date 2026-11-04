### D-DRAFT-1 — 2026-11-03 — Self-serve ingestion runs through a host-side runner, not a Docker socket in the web container

**Context.** The ingestion cycle runs the batch and store images as sibling containers on the host. The web runs inside a container and cannot start them, yet the self-serve page must run the whole cycle for one cell.

**Decision.** A small host-side runner on the build machine consumes the web's existing job queue and executes the `cycle.sh` verbs, writing logs where the web already streams them from. The web only enqueues, shows progress and offers the Serve and Roll back buttons.

**Why.** Mounting the Docker socket into the web container is simpler but gives a gated web process root-equivalent control of the host. The runner keeps the web unprivileged and reuses the queue, the log stream and the publish gate as they are.

**Consequences.** One more process to install and keep running on the build host (placement, user and service model to be settled in the design). The web cannot report a stage the runner has not written; the runner's contract with the queue becomes a public surface of the web module.

### D-DRAFT-2 — 2026-11-03 — Web page first; a skill is a thin client over `POST /api/ingest`

**Context.** The team asked for a web interface and/or a Windows-side skill to trigger ingestion.

**Decision.** Build the web surface first and expose the same operation as an API endpoint. A skill, if wanted later, calls that endpoint and holds no ingestion logic.

**Why.** A skill on the team's Windows machines cannot reach the host's Docker either; it would be a second front door to the same backend. Starting with the skill means building the API anyway and maintaining a skill on every machine.

**Consequences.** The endpoint is designed as a public contract from the start (request shape, job id, status and gate result), not as an internal form handler. The skill is deferred, not rejected.

### D-DRAFT-3 — 2026-11-03 — The ingest runner is a `systemd --user` service of the build user, on the host that builds and serves

**Context.** The runner (D-DRAFT-1) needs a home: which host, which user, which service manager. The build host holds the builds roots, both stacks' state directories and the serving stores; the build user is in the docker group and owns the web's jobs database with owner-only write; lingering is off for that user.

**Decision.** One host, one user: a `systemd --user` unit of the build user. The administrator turns lingering on once before installing it. Version 1 does not build on one host and serve on another.

**Why.** The jobs database and the Docker socket are both reachable only as that user, so no other user works without widening permissions. A user unit keeps the runner out of root's hands; a system unit with `User=` was the alternative and is still possible, but adds nothing here. Cross-host would add a transport for the promoted label and the staging eval, for a layout nobody has.

**Consequences.** Lingering must be on, or the runner dies at logout; the install notes say so and the web shows a stale heartbeat as "runner not responding". A second host means a second runner and a second jobs database, not a remote runner.

### D-DRAFT-4 — 2026-11-03 — The self-serve gate is Stage 1 of the golden eval, baseline-relative, seeded from the recorded run

**Context.** Acceptance today is a human comparison against a baseline kept by hand; no target values are coded. Self-service needs a mechanical rule, and a new release of an MNO has no golden samples of its own until experts add them.

**Decision.** The runner runs Stage 1 only against the staged set and passes when `recall_avg` is at least the baseline minus a tolerance (default 0.03) and no `GEV-E` error occurred. The baseline is the Stage-1 result of the last ingest that passed the gate on this serve root; the first ingest takes it from the latest recorded golden run over the served cells. The verdict is recorded with `publish gate`; the administrator can still flip by hand with a waiver, and the page shows it.

**Why.** Stage 2 needs a judge model and expert reading; it stays the experts' tool. A relative rule tolerates the ±0.02 run-to-run noise without hand-kept numbers. Seeding from the recorded run avoids an unconditional first pass. Coding absolute targets was rejected: nobody owns them and they drift with every golden-set change.

**Consequences.** The new release is covered only by that MNO's standard samples until version samples exist; the verdict line says so. The release-map mismatch before the flip is passed through with `--allow-map-mismatch` and `advance` runs after the flip; samples the text check flags are counted on the page for expert review.

### D-DRAFT-5 — 2026-11-03 — An incremental cycle seeds the new build by copying the previous one; a new release inherits its MNO's latest profile binding

**Context.** A cycle is a full rebuild: `start` opens an empty build, parse runs every cell, the SIRA root is per build. A self-serve ingest must cost one cell, not the corpus. The parse and taxonomy stages already skip unchanged work by mtime and fingerprint when the outputs are present.

**Decision.** `cycle.sh start --seed <prev>` copies the previous build's `out/{extract,profile,parse,resolve,taxonomy}` into the new build (`cp -a --reflink=auto`), takes its `profiles.json` unless `--profiles` says otherwise, and binds each uncovered cell to the profile of that MNO's latest bound release. The SIRA side holds only the new cell; publish inherits every other unit from the active set.

**Why.** Hard links were the first idea and were rejected: the taxonomy ledger, the profile stage and resolve rewrite files in place, so shared inodes would corrupt the seed build, which is the served one. A copy costs seconds and is safe; reflink makes it free where the filesystem allows. Inheriting the binding is the ruling that a new release of a known MNO needs no profile judgment; without `--seed` bindings are still chosen, not inherited, and an MNO with no binding fails loud (CYC-E027).

**Consequences.** Builds grow by one copy of the parse outputs each; `gc` of old builds stays by hand. A release whose documents changed shape enough to need a new profile is not self-serve: the parse will warn or fail and the administrator binds by hand.

