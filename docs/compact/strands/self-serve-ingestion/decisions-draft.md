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
