# docker/ — NORA + SIRA distribution (strand: docker-distro, phase 1)

Design: `docs/compact/strands/docker-distro/docker-distro-design.md`.
Four CPU-only images, two views (dev/ops) via compose profiles; images ship as
Release-asset tarballs on the internal GitHub (`push.sh` / `pull.sh`).

## Quick start (serve, on one host)

    cd docker
    cp env.example .env          # fill in paths + endpoints (never commit)
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

    docker compose -p stack-a --env-file .env.stack-a --profile serve up -d
    docker compose -p stack-b --env-file .env.stack-b --profile serve up -d

Per-stack: ports, LLM env, WEB_STATE_DIR. Shared: SIRA_DB_ROOT (one ingest,
N servers) and FEEDBACK_DIR (pooled feedback DB → attributable A/B, D-120).

## Build + distribute

    # dev PC (network):
    docker compose --profile serve --profile ingest build
    ./push.sh images-v1-$(git -C .. rev-parse --short HEAD) \
        local/nora-web:dev local/sira-query:dev local/nora-pipeline:dev local/sira-batch:dev

    # target host:
    ./pull.sh images-v1-<sha>

Images bake NO endpoints, models, mappings, or corpus data — those arrive via
`.env` + `/data/*` volumes. The SIRA upstream clone IS baked (pinned by
`SIRA_REF`) so a host never needs github.com access.
