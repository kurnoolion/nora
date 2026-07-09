# docker/ — NORA + SIRA distribution (strand: docker-distro, phase 1)

Design: `docs/compact/strands/docker-distro/docker-distro-design.md`.
Four CPU-only images, two views (dev/ops) via compose profiles (the `dev`
profile — bind-mounts + dev-shell — lands in phase 4; `serve`/`ingest` are
live); images ship as Release-asset tarballs on the internal GitHub
(`push.sh` / `pull.sh`).

## Recommended host layout

One dedicated data root for everything the stack mounts, plus your existing
env_dir. All paths must be VISIBLE `$HOME` paths on snap-docker hosts (snap
blocks `/tmp` and hidden dirs like `~/.cache`), and env files need absolute
paths (`$HOME` does not expand there).

    /home/<you>/env_dir/               # NORA env dir (wherever yours already lives)
    /home/<you>/nora-data/
    ├── sira/                          # SIRA db_roots, ONE PER BUILD — NEVER inside
    │   ├── <build-1>/                 #   the repo tree (a `git clean -dx` would wipe
    │   └── <build-2>/                 #   indexes + the ~13h enrichment cache).
    │                                  #   SIRA_DB_ROOT points at ONE build.
    ├── web-state-a/                   # per stack: nora_jobs/metrics/config.db
    ├── web-state-b/                   #   (stack identity = directory, not filename)
    ├── feedback/                      # ONE pooled dir for ALL stacks (D-120 —
    │                                  #   attributable A/B lives in one DB)
    └── models/
        └── docling/                   # DOCLING_ARTIFACTS resolves to
                                       #   /data/models/docling in-container

## Quick start (serve, on one host)

    cd docker
    cp env.example .env                        # build + stack wiring (paths, ports)
    cp env.nora-web.example .env.nora-web      # per-service runtime knobs
    cp env.sira-query.example .env.sira-query  #   (endpoints, tuning — never commit)
    cp env.nora-pipeline.example .env.nora-pipeline   # for ingest jobs
    cp env.sira-batch.example .env.sira-batch         # for enrichment jobs

In `.env`, point the volume paths at the layout above:

    NORA_ENV_DIR=/home/<you>/env_dir
    SIRA_DB_ROOT=/home/<you>/nora-data/sira/<build>
    WEB_STATE_DIR=/home/<you>/nora-data/web-state-a
    FEEDBACK_DIR=/home/<you>/nora-data/feedback
    MODELS_DIR=/home/<you>/nora-data/models

Create the directories before `up` (docker creates missing bind sources
root-owned, and the app then can't write its DBs). Then:

    docker compose --profile serve up -d --build
    curl localhost:8040/healthz  # cells loaded
    open http://localhost:8000

## Ingest a new release (jobs, then restart)

    # ingestion lane (extract..standards) for the new cell:
    docker compose --profile ingest run --rm nora-pipeline \
      python -m core.src.pipeline.run_cli --env-dir /data/env --lane ingestion --mno <MNO> --release <REL>
    # sira lane (adapter + per-cell index/enrich) in one command:
    docker compose --profile ingest run --rm sira-batch \
      python -m sandbox.sira_lane --env-dir /data/env --db-root /data/db \
      --run-name enrich-stable --only <MNO>__<REL> --wipe-stale-index
    # (standards downloads specs over the network — on proxied hosts put
    #  HTTP_PROXY/HTTPS_PROXY in .env.nora-pipeline, or add --skip-standards)
    # (nora lane, when the native retrieval stack is wanted:)
    # docker compose --profile ingest run --rm nora-pipeline \
    #   python -m core.src.pipeline.run_cli --env-dir /data/env --lane nora
    docker compose restart sira-query

(Full scenario matrix: `sandbox/README.md` §2 — same commands, containerized.)

## Two stacks, two LLMs (A/B)

Same images; each stack gets its own wiring env + its own service runtime
files. Per stack: STACK_NAME, ports, its `web-state-<x>/` directory, and the
LLM-bearing runtime files. Shared (same values in both wiring envs):
NORA_ENV_DIR, SIRA_DB_ROOT (one ingest serves both) and FEEDBACK_DIR (the
pooled feedback DB → attributable A/B, D-120). To A/B *ingestions* instead of
LLMs, point each stack's SIRA_DB_ROOT at a different `sira/<build>`.

    # stack A
    cp env.example .env.stack-a            # STACK_NAME=stack-a, ports 8000/8040,
                                           # WEB_STATE_DIR=/home/<you>/nora-data/web-state-a,
                                           # WEB_ENV_FILE=.env.nora-web.a,
                                           # SIRA_QUERY_ENV_FILE=.env.sira-query.a
    cp env.nora-web.example  .env.nora-web.a     # LLM A (+ its sentinel setting)
    cp env.sira-query.example .env.sira-query.a  # shim/rerank -> LLM A

    # stack B — STACK_NAME=stack-b, ports 8001/8041,
    # WEB_STATE_DIR=/home/<you>/nora-data/web-state-b,
    # and .env.nora-web.b / .env.sira-query.b pointing at LLM B
    cp env.example .env.stack-b
    cp env.nora-web.example  .env.nora-web.b
    cp env.sira-query.example .env.sira-query.b

    docker compose --env-file .env.stack-a --profile serve up -d
    docker compose --env-file .env.stack-b --profile serve up -d

STACK_NAME in each env file names the compose project — no `-p` flag needed.
All stack env files live in this same `docker/` directory (gitignored; only
the `*.example` templates are committed). The pipeline/batch runtime files
usually need no per-stack variants — ingest is stack-independent; run jobs
with either wiring env. After ingesting a new cell (shared db_root), restart
BOTH stacks' sira-query.

## Build + distribute

Builds happen **on the work PC** (the only host that can also publish to the
internal GitHub). `docker pull` is broken through the corp proxy — fetch base
images via skopeo first (same workaround as the dgx/spark stacks):

    # work PC, one-time per base-image bump:
    ./skopeo-pull-bases.sh                  # python:3.12-slim etc. via skopeo + docker load

    # build — the build knobs come from .env (compose maps them into
    # build.args): work PC sets TORCH_INDEX_URL=https://pypi.org/simple there
    # (the CPU index is allowlist-blocked); HTTP(S)_PROXY are picked up from
    # your shell or .env:
    docker compose --profile serve --profile ingest build

    # publish to the internal GitHub release:
    ./push.sh images-v1-$(git -C .. rev-parse --short HEAD) \
        local/nora-web:dev local/sira-query:dev local/nora-pipeline:dev local/sira-batch:dev

    # any other internal host:
    ./pull.sh images-v1-<sha>

The dev PC (unrestricted network) can also build — useful for verifying
Dockerfile changes fast (default TORCH_INDEX_URL, smaller image) — but it
cannot reach the internal GitHub; its builds are verification, not release.

Images bake NO endpoints, models, mappings, or corpus data — those arrive via
`.env` + `/data/*` volumes. The SIRA upstream clone IS baked (pinned by
`SIRA_REF`) so serving hosts never need github.com access.
