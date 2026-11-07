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

### D-DRAFT-6 — 2026-11-03 — The ingest surface has its own token, separate from the admin token

**Context.** The Ingest page flips what every user is served. It must be gated, and the people who use it are the requirements team, not the administrator.

**Decision.** A second token, `NORA_WEB_INGEST_TOKEN`, set as its own HttpOnly cookie by `/ingest-unlock?token=…` and required by every `/ingest*` route, with team mode on or off; the admin cookie also passes. The paths are on the team-mode allowlist so a gated member holding the cookie reaches them.

**Why.** Reusing the admin cookie was rejected: it opens the whole app, and the token would have to be shared with the team. A token of its own can be given to the team and rotated alone. The same mechanism as the admin unlock keeps one pattern in the web.

**Consequences.** Two tokens to configure per stack; with no ingest token the surface is closed. The cookie carries the token, as the admin cookie does: acceptable on the internal network, not an authentication scheme.

### D-DRAFT-7 — 2026-11-03 — The share mapping lives in the ingestion wiring env, not in a committed file

**Context.** The runner must map the Windows share path the team types to the host's own mount. The web's `path_mappings` in `config/web.json` is committed and names a container path the host does not have.

**Decision.** `NORA_SHARE_MAP=\\SERVER\Share=/host/mount;…` in the ingestion wiring env (`.env.builds`), read by the runner and merged over the optional JSON form in its gitignored config. The web validates only the shape of the path; the runner fails loud at the documents stage when the path is under no mapped share.

**Why.** Host mounts are deployment facts and must not enter the repo; the wiring env is already the per-host file the runner and the cycle driver read.

**Consequences.** The web cannot tell the team at submit time that a share is unmapped; the job fails in seconds with the reason instead.

### D-DRAFT-8 — 2026-11-06 — One taxonomy per release; every build-plane stage is per cell

**Context.** The taxonomy was a global stage: one feature set over every cell's trees, with a plan present in several releases extracted once from the newest. The served path consumes only the per-plan features files in the enrichment prompt; the merged file feeds the disabled graph builder and a dormant review editor. The global stage forced the incremental cycle to carry the whole corpus's parse outputs into every build (D-DRAFT-5's copy), and the field run enriched an older release against a newer release's features.

**Decision.** The taxonomy stage runs per `(MNO, release)`: `out/taxonomy/<mno>/<rel>/` holds that release's per-plan files, taxonomy, ledger and fingerprint, derived from the cell's own trees; releases do not supersede each other. The enrichment lane reads the cell's directory. A build-level `taxonomy.json` is consolidated over the cells present, for the graph and the editor. With that, `parse --cell` and `taxonomy --cell` run one cell, `start --seed` takes only the profile bindings, and a build holds only the cells it parsed. D-DRAFT-5's copy of the previous build's outputs is withdrawn.

**Why.** A release enriched against the taxonomy of its own text is the correct input, and a self-contained per-cell build is simpler to reason about, to retry and to delete than one that carries a corpus copy. The cross-release dedupe saved LLM calls at the cost of blurring releases; one extraction per plan per release is minutes against the hour the enrichment takes. The alternative, keeping the global stage and the seed copy, was working but made every ingest a corpus-sized build.

**Consequences.** No taxonomy cache carries across builds; a full cycle re-derives every cell. FR-6's "unified taxonomy" is now per release with a per-build consolidation; the review editor sees the build-level file. A `corrections/taxonomy.json` applies to the build-level file only. The architecture's §20 table and the README's Phase 1 and 4 say so.

### D-DRAFT-9 — 2026-11-06 — The SIRA cells are the build's last stage directory, `<build>/out/sira/<MNO>__<REL>/`

**Context.** Every nora stage writes under the build's `out/`, but the SIRA enrichment wrote to a twin tree, one SIRA db-root per build beside the nora builds. Two trees per build meant two roots to configure, two directories to move or delete, and one more place a build could be found in the wrong location (field-found).

**Decision.** `cycle.sh start` creates `<build>/out/sira/` and points the wiring env's `STORE_ROOT` there; `enrich`, `verify-enrich`, `promote` and the runner use it. The cell directories inside keep the store's `<MNO>__<REL>` name. `NORA_BUILDS_SIRA_ROOT`, when set, keeps the older twin layout for hosts that have it.

**Why.** One build, one tree: every stage's artifacts in one place, deletable and movable as a unit. Renaming the cells to `<MNO>/<REL>` was considered and rejected: that name is the cell's identity in the SIRA tooling, the verify sweep, the converter, the registry's unit names and the store's flat mode, and the rename would ripple through all of them for no functional gain.

**Consequences.** New builds hold their SIRA cells; old builds stay where they were (publish inherits served cells from the registry, the seed needs only the nora side). The batch and store containers get a nested mount: `/data/env` is the build and `/data/db` its `out/sira`. The README's layout, Phase 0 and the ingestion guide say so.

### D-DRAFT-10 — 2026-11-06 — One spelling of a cell everywhere: `<MNO>/<release>`

**Context.** A cell was `<MNO>__<release>` as a directory, a registry unit, a store API name, a page parameter and a command-line argument, while every nora stage writes `<MNO>/<release>/`. Two spellings of one thing, and the SIRA directory had just moved inside the build beside the nested ones.

**Decision.** `<MNO>/<release>` is the cell's name everywhere: `out/sira/<MNO>/<release>/`, registry units `cells/<MNO>/<release>` and `trees/<MNO>/<release>`, healthz and `resolved_cells`, the `/cells/{cell}` routes (path parameter), the review and ingest pages, `cycle.sh --cell`, the lane's `--only`, the converter's and publish's `--cells`, SIRA's `data.name`. The old spelling is still parsed on input (registries, links and reports from before), and `publish migrate-units` rewrites a registry's rows once. A flat `<MNO>__<release>/` directory under a root is still found and read.

**Why.** One name to read and type. The cost was a dozen files, a one-shot migration of the registry rows on each stack, and one line in the batched-enrichment patch (the MNO for the per-MNO prompt now splits the name on the slash). SIRA joins the root and the name as a path, so the nested directory needed no change in the vendored code.

**Consequences.** Both stacks run `migrate-units` once after the images are rebuilt; until then the store serves the old rows unchanged. Old golden-run reports keep the old strings in their records. The converter's and registry's file names (hyphenated) are unchanged.

### D-DRAFT-11 — 2026-11-06 — One wiring variable names the build: `NORA_BUILD_DIR`

**Context.** The wiring env named the build twice, `NORA_ENV_DIR` for the env dir and `STORE_ROOT` for the SIRA root, and a host env missing one of them failed at the next verb.

**Decision.** `NORA_BUILD_DIR=<build>` is the one line; the compose file mounts it at `/data/env` and its `out/sira` at `/data/db`, with `NORA_ENV_DIR` and `STORE_ROOT` kept only as overrides for an older layout (a dev stack mounting a flat label, for example). `cycle.sh start` writes the one line and rewrites the older ones when an env still carries them; `need_wiring` refuses an env whose older line disagrees.

**Why.** With the SIRA cells inside the build there is one directory to name. Nested defaults in the compose file carry the derivation, so the env file holds one fact.

**Consequences.** Hosts replace the two lines with one on their next cycle; the runner config's `cycle_env` no longer names the SIRA root.

### D-DRAFT-12 — 2026-11-06 — Submits queue; one cycle runs at a time; a blocking ingest is named, never auto-abandoned

**Context.** The first page refused a second submit while an ingest was queued or running: the cycle driver's baton, the wiring env and the staging store are singletons, so two cycles cannot run at once. Refusing made the second person come back and retry.

**Decision.** Submits queue in order; the page shows each queued ingest's position and the runner takes the oldest job first. A failed or cancelled ingest that opened a build may still hold the baton; the page names it and the next queued ingest fails at `start` until someone retries or abandons it.

**Why.** The long stage is enrichment, bound by the language-model endpoint, so parallel cycles would share that capacity and gain little; a queue removes the human retry at no cost. Making the baton, the wiring env and the staging store per ingest was the alternative and was not worth its size. Auto-abandoning a blocking ingest was rejected: a failed build is evidence someone may want.

**Consequences.** Three submits in a row finish about three hours later and each person sees where they stand. The cell-uniqueness refusal stays.

### D-DRAFT-13 — 2026-11-06 — Progress by push: an event stream for clients, signed webhooks for people and systems; a skill submits and returns

**Context.** The page already received pushes over the job log's event stream; an API client would have polled `GET /api/ingest/{id}`. A future skill in a chat session has no listener a webhook could reach.

**Decision.** `GET /api/ingest/{id}/events` streams `state` on every change and `done` at the end for any client that can hold a connection. A submit may carry a `callback_url`; the runner posts one signed JSON event per state change (HMAC-SHA256 under `webhook_secret`, host allowlist `webhook_hosts`, three attempts, never failing the job), the path for a chat or mail bridge. A skill, when built, submits, keeps the id and asks later.

**Why.** A ninety-minute job at a handful of clients makes polling harmless, so the stream and the webhook are for convenience and for people, not load. The runner posts the webhook because it is the one process that knows when a state changes. A long-held stream inside a tool call was rejected for the skill: tool runtimes time out long before enrichment ends.

**Consequences.** Webhook targets must be allowlisted in the runner config; an unlisted host is logged and skipped. The web validates only the URL's shape.

### D-DRAFT-14 — 2026-11-07 — A retry of the enrichment is batched; single-request mode is opt-in

**Context.** The driver paired `--retry-failed` with `--max-reqs 1` on every retry (D-178's "standard retry pairing"). The runner retries from the enrich stage after any failure there, including an interrupted first run, and `--retry-failed` resumes the run: the whole cell then went one request per call. Field-found: fourteen hours for a cell that batches in sixty-five minutes.

**Decision.** `cycle.sh enrich --retry-failed` re-queues the failed rows and resumes, batched. `--single` adds `--max-reqs 1` for the rows that keep failing, by hand. The runner's Retry passes `--retry-failed` only.

**Why.** D-178's single-request mode is for stubborn rows after a complete run, a second step, not the first retry; most retries are resumes of an interrupted run, where batching is the whole speed. The alternative, a separate "resume" button beside "retry failed rows", was rejected as a distinction the team should not have to make.

**Consequences.** A row that fails in a batch twice is retried in a batch twice; the administrator runs `--retry-failed --single` for it. D-178's text stays true for that step.

