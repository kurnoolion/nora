# docker/ — NORA + SIRA distribution (strand: docker-distro, phase 1)

Design: `docs/compact/strands/docker-distro/docker-distro-design.md`.
Four CPU-only images, two views (dev/ops) via compose profiles; images ship as
Release-asset tarballs on the internal GitHub (`push.sh` / `pull.sh`).

## Quick start (serve, on one host)

    cd docker
    cp env.example .env                        # build + stack wiring (paths, ports)
    cp env.nora-web.example .env.nora-web      # per-service runtime knobs
    cp env.sira-query.example .env.sira-query  #   (endpoints, tuning — never commit)
    cp env.nora-pipeline.example .env.nora-pipeline   # for ingest jobs
    cp env.sira-batch.example .env.sira-batch         # for enrichment jobs
    docker compose --profile serve up -d --build
    curl localhost:8040/healthz  # cells loaded
    open http://localhost:8000

## Ingest a new release (jobs, then restart)

    docker compose --profile ingest run --rm nora-pipeline \
      python -m core.src.pipeline.run_cli --env-dir /data/env --start extract --end parse --mno <MNO> --release <REL>
    docker compose --profile ingest run --rm nora-pipeline \
      python -m sandbox.adapter.nora_to_beir --env-dir /data/env --output /data/db \
      --multi-cell --only <MNO>__<REL> --wipe-stale-index
    docker compose --profile ingest run --rm sira-batch \
      python -m sandbox.sira_multi --db-root /data/db --sira-clone sandbox/sira --run-name enrich-stable --only <MNO>__<REL>
    docker compose restart sira-query

(Full scenario matrix: `sandbox/README.md` §2 — same commands, containerized.)

## Two stacks, two LLMs (A/B)

Same images; each stack gets its own wiring env + its own service runtime
files. Per stack: STACK_NAME, ports, WEB_STATE_DIR, and the LLM-bearing
runtime files. Shared (same values in both .env files): NORA_ENV_DIR,
SIRA_DB_ROOT (one ingest serves both) and FEEDBACK_DIR (pooled feedback DB →
attributable A/B, D-120).

    # stack A
    cp env.example .env.stack-a            # STACK_NAME=stack-a, ports 8000/8040,
                                           # WEB_STATE_DIR=.../web-state-a,
                                           # WEB_ENV_FILE=.env.nora-web.a,
                                           # SIRA_QUERY_ENV_FILE=.env.sira-query.a
    cp env.nora-web.example  .env.nora-web.a     # LLM A (+ its sentinel setting)
    cp env.sira-query.example .env.sira-query.a  # shim/rerank -> LLM A

    # stack B — different STACK_NAME, ports (e.g. 8001/8041), web-state-b,
    # and .env.nora-web.b / .env.sira-query.b pointing at LLM B
    cp env.example .env.stack-b
    cp env.nora-web.example  .env.nora-web.b
    cp env.sira-query.example .env.sira-query.b

    docker compose --env-file .env.stack-a --profile serve up -d
    docker compose --env-file .env.stack-b --profile serve up -d

STACK_NAME in each env file names the compose project — no `-p` flag needed.
After ingesting a new cell (shared db_root), restart BOTH stacks' sira-query.

## Build + distribute

Builds happen **on the work PC** (the only host that can also publish to the
internal GitHub). `docker pull` is broken through the corp proxy — fetch base
images via skopeo first (same workaround as the dgx/spark stacks):

    # work PC, one-time per base-image bump:
    ./skopeo-pull-bases.sh                  # python:3.12-slim etc. via skopeo + docker load

    # build (proxy: pip/github/rustup go through the proxy env; torch's CPU
    # index is blocked on the allowlist -> override to PyPI):
    docker compose --profile serve --profile ingest build \
        --build-arg TORCH_INDEX_URL=https://pypi.org/simple

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
