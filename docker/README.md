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

    /home/<you>/nora-data/
    ├── requirements/<MNO>/<MMMYYYY>/  # THE source corpus — one canonical copy,
    │                                  #   shared by all builds (requirements_dir
    │                                  #   override; default is <env_dir>/input)
    ├── nora-builds/<build>/           # env_dirs: out/, state/, corrections/, reports/
    ├── sira-builds/<build>/           # SIRA db_roots (cells) — NEVER inside the repo
    │                                  #   tree (git clean would wipe the enrichment cache)
    ├── serve/<label>/                 # promoted hardlink snapshots (promote.sh):
    │   ├── MANIFEST.json              #   nora/out/{vectorstore,graph,taxonomy} +
    │   ├── nora/out/...               #   sira/<cells>. Stacks mount THESE — builds
    │   └── sira/...                   #   can be rebuilt/wiped without touching serving.
    ├── web-state-a/                   # per stack: nora_jobs/metrics/config.db
    ├── web-state-b/                   #   (stack identity = directory, not filename)
    ├── feedback/                      # ONE pooled dir for ALL stacks (D-120)
    └── models/
        └── docling/                   # DOCLING_ARTIFACTS -> /data/models/docling

## Quick start (serve, on one host)

    cd docker
    cp env.example .env                        # build + stack wiring (paths, ports)
    cp env.nora-web.example .env.nora-web      # per-service runtime knobs
    cp env.sira-query.example .env.sira-query  #   (endpoints, tuning — never commit)
    cp env.nora-pipeline.example .env.nora-pipeline   # for ingest jobs
    cp env.sira-batch.example .env.sira-batch         # for enrichment jobs

In `.env`, point the volume paths at the layout above:

    REQUIREMENTS_DIR=/home/<you>/nora-data/requirements
    NORA_ENV_DIR=/home/<you>/nora-data/serve/<label>/nora     # or a nora-build while iterating
    SIRA_DB_ROOT=/home/<you>/nora-data/serve/<label>/sira     # or a sira-build while iterating
    WEB_STATE_DIR=/home/<you>/nora-data/web-state-a
    FEEDBACK_DIR=/home/<you>/nora-data/feedback
    MODELS_DIR=/home/<you>/nora-data/models

Create the directories before `up` (docker creates missing bind sources
root-owned, and the app then can't write its DBs). Then:

    docker compose --profile serve up -d --build
    curl localhost:8040/healthz  # cells loaded
    open http://localhost:8000

## Ingest a new release (build lanes → promote → restart)

Ingest jobs run against the BUILD dirs (set `NORA_ENV_DIR`/`SIRA_DB_ROOT` to
the build paths for these commands, or use a builds-oriented `.env`):

    # ingestion lane (extract..standards) for the new cell:
    docker compose --profile ingest run --rm nora-pipeline \
      python -m core.src.pipeline.run_cli --env-dir /data/env --lane ingestion --mno <MNO> --release <REL>
    # sira lane (adapter + per-cell index/enrich) in one command:
    docker compose --profile ingest run --rm sira-batch \
      python -m sandbox.sira_lane --env-dir /data/env --db-root /data/db \
      --run-name enrich-stable --only <MNO>__<REL> --wipe-stale-index
    # (standards downloads specs over the PUBLIC network at run time — on
    #  agent-guarded hosts container egress is blocked: add --skip-standards,
    #  or run the standards stage bare-metal)
    # (nora lane, when the native retrieval stack is wanted:)
    # docker compose --profile ingest run --rm nora-pipeline \
    #   python -m core.src.pipeline.run_cli --env-dir /data/env --lane nora
    # promote the serve-set (hardlink snapshot — instant, wipe-immune):
    ./promote.sh --serve-root /home/<you>/nora-data/serve --label <YYYY-MM-DD-a> \
        --nora-build /home/<you>/nora-data/nora-builds/<build> \
        --sira-build /home/<you>/nora-data/sira-builds/<build>
    # point the stack .env at serve/<label>/{nora,sira} and:
    docker compose restart sira-query nora-web
    # rollback = point .env back at the previous label + restart

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
internal GitHub).

**Corporate-network note.** Some perimeter DPI devices reset TLS handshakes
that use post-quantum key exchange — which OpenSSL 3.5+ (bundled inside the
python base image) sends by default, while typical host tools on OpenSSL 3.0
pass. The symptom is misleading: TCP connects, then every in-container HTTPS
attempt dies with "connection reset" (bridge AND `--network=host`), so it
looks like "containers have no network." Both Dockerfiles bake an
`OPENSSL_CONF` that pins classical key-exchange groups, so **ONLINE builds
(`OFFLINE=0`, the default) work behind such devices**. `docker pull` of base
images may still fail there (the docker daemon uses a different TLS stack) —
`./skopeo-pull-bases.sh` covers that:

    # one-time per base-image bump (host process):
    ./skopeo-pull-bases.sh

`OFFLINE=1` remains supported — for hosts where container egress is truly
unavailable, or as a deterministic fully-vendored build:

    # whenever dependencies / SIRA_REF change (host processes):
    ./prep-offline.sh              # wheels + patched SIRA clone -> docker/vendor/
                                   # (needs host python3.12, git, rust+maturin —
                                   #  the same toolchain SETUP.md §2/3 installs)

    # build with OFFLINE=1 in .env — zero in-container network:
    docker compose --profile serve --profile ingest build

Publish + fetch (either build mode):

    # publish to the internal GitHub release (host process):
    ./push.sh images-v1-$(git -C .. rev-parse --short HEAD) \
        local/nora-web:dev local/sira-query:dev local/nora-pipeline:dev local/sira-batch:dev

    # any other internal host:
    ./pull.sh images-v1-<sha>

Runtime note: the baked classical-groups config also applies at run time, so
in-container outbound HTTPS (e.g. the `standards` stage downloading specs)
works behind the same middleboxes. If a host still blocks container egress
entirely, run the ingestion lane with `--skip-standards` in-container, or run
that one stage bare-metal.

The dev PC (unrestricted network) builds ONLINE (`OFFLINE=0`, the default):
rustup + clone + PyPI happen in-container, with `TORCH_INDEX_URL` defaulting
to the small CPU index. Dev-PC builds are Dockerfile verification only — the
release is always built and pushed from the work PC.

Images bake NO endpoints, models, mappings, or corpus data — those arrive via
`.env` + `/data/*` volumes. The SIRA upstream clone IS baked (pinned by
`SIRA_REF`, pre-patched by prep-offline in OFFLINE builds) so serving hosts
never need github.com access. Vendored wheels are bind-mounted during build
(never COPY'd) — they add nothing to image size.
