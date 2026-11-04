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
