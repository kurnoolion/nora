# docker/ — NORA + SIRA distribution (strand: docker-distro, phase 1)

Design: `docs/compact/strands/docker-distro/docker-distro-design.md`.
Four CPU-only images, two views (dev/ops) via compose profiles (the `dev`
profile — bind-mounts + dev-shell — lands in phase 4; `serve`/`ingest` are
live); images ship as Release-asset tarballs on the internal GitHub
(`push.sh` / `pull.sh`).

## The four images

| Image | Profile | Runs | Purpose | Reads (mounts) | Writes (mounts) |
|---|---|---|---|---|---|
| **nora-web** | serve | uvicorn `:8000`, long-lived | Web UI + API: query, compare, compliance views; jobs/metrics/config; feedback capture | `/data/env` (promoted nora label: vectorstore, graph, taxonomy) | `/data/web-state` (per stack), `/data/feedback` (pooled) |
| **sira-query** | serve | uvicorn `:8040`, long-lived | Per-query retrieval service: BM25 over the cells + LLM query-enrichment / rerank | `/data/db` (promoted sira label: `<MNO>__<MMMYYYY>` cells) | — |
| **nora-pipeline** | ingest | `run --rm` jobs | Batch lanes over the corpus: **ingestion** lane (extract → parse → resolve → standards) and **nora** lane (taxonomy → graph → vectorstore → eval); Docling layout for opt-in corpora | `/data/requirements` (ro, the corpus), `/data/models` | `/data/env` (a nora-BUILD dir: `out/`, images incl.) |
| **sira-batch** | ingest | `run --rm` jobs | SIRA lane: NORA→BEIR adapter, per-cell index build, batch doc-enrichment, incremental retries | `/data/env` outputs via adapter | `/data/db` (a sira-BUILD dir: cells) |

Lineage: nora-web / nora-pipeline share the `nora-base` dependency layer
(`nora.Dockerfile`); sira-query / sira-batch share `sira-base` with the
pinned SIRA clone baked in (`sira.Dockerfile`, `SIRA_REF`).

How they connect (build → promote → serve, and the query path):

    requirements/ corpus (ro)
        │  ingestion lane                 sira lane
        ▼                                    ▼
    [nora-pipeline] ──► nora-builds/<b> ─► [sira-batch] ──► sira-builds/<b>
        │   (also nora lane:                     │
        │    taxonomy/graph/vectorstore)         │
        └────────────┬───────────────────────────┘
                     ▼  promote.sh (hardlink snapshot, immutable label)
              serve/<label>/{nora,sira}
                     │ mounted ro-by-convention
        ┌────────────┴────────────┐
        ▼                         ▼
    [nora-web :8000] ───────► [sira-query :8040]
        │      NORA_SIRA_QUERY_URL (compose network)
        ▼                         ▼
     browser              LLM endpoints (env files;
                          host.docker.internal for host-local)

The serve pair talks over the compose-internal network (`nora-web` reaches
`sira-query` by service name); everything else connects through the shared
host directories — there is no other coupling between images. LLM endpoints
are never baked in; each service gets them from its env file at run time.

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

Edit the per-service files with your LLM endpoints. Gotcha: when
`NORA_LLM_SHIM_URL` points at a real OpenAI-compatible endpoint (not the
local shim), `NORA_LLM_MODEL` is REQUIRED — unset, query enrichment fails
with model_not_found (`curl <endpoint>/v1/models` lists valid names).

Sharing/committing the per-service env files somewhere? Keep credentials out
of them: every service also loads an optional overlay (`SECRETS_ENV_FILE`,
default `.env.secrets`) AFTER its own env file — later file wins on overlap.
Put API keys/tokens there and gitignore it wherever the configs are tracked.
Note: `${VAR}` inside env files is NOT expanded (compose passes them to the
container verbatim), so referencing shell vars from within them can't work —
the overlay file is the supported mechanism.

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

Ingest jobs run against the BUILD dirs — never against a promoted serve
label. Make a builds-oriented wiring env once: `cp .env.stack-a .env.builds`
(a FULL copy — compose validates every required path var in the whole file
even for ingest-only runs, so all six must be present), then repoint only
`NORA_ENV_DIR`/`SIRA_DB_ROOT` at `nora-builds/<build>` / `sira-builds/<build>`
and pass `--env-file .env.builds` to the commands below.

A NEW build env dir needs its per-cell profile bindings before the profile
stage: copy `profiles.json` from the previous build (or author one mapping
each `<MNO>/<MMMYYYY>` cell to a repo-relative `customizations/profiles/*.json`
path — those are baked into the image). Without it the lane fails loud with
PIP-E003; a single-profile run can bypass via `--profile <path>`.

    # ingestion lane (extract..standards) for the new cell — the helper runs
    # it detached with the console log landing in <build-env>/reports/:
    ./ingest.sh <MNO> <MMMYYYY>              # options: -f force, -l nora,
                                             #   -e <env-file>, --fg, DRY_RUN=1
    # (equivalent raw command:)
    # docker compose --env-file .env.builds --profile ingest run --rm nora-pipeline \
    #   python -m core.src.pipeline.run_cli --env-dir /data/env --lane ingestion --mno <MNO> --release <REL>
    # sira lane (adapter + per-cell index/enrich) in one command:
    docker compose --profile ingest run --rm sira-batch \
      python -m sandbox.sira_lane --env-dir /data/env --db-root /data/db \
      --run-name enrich-stable --only <MNO>__<REL> --wipe-stale-index
    # (standards downloads specs over the PUBLIC network at run time — the
    #  baked classical-TLS config gets python HTTPS through PQ-hello-resetting
    #  DPI, but if a host truly has no container egress: add --skip-standards,
    #  or run the standards stage bare-metal)
    # (nora lane, when the native retrieval stack is wanted:)
    # docker compose --profile ingest run --rm nora-pipeline \
    #   python -m core.src.pipeline.run_cli --env-dir /data/env --lane nora
    # promote the serve-set (hardlink snapshot — instant, wipe-immune):
    ./promote.sh --serve-root /home/<you>/nora-data/serve --label <YYYY-MM-DD-a> \
        --nora-build /home/<you>/nora-data/nora-builds/<build> \
        --sira-build /home/<you>/nora-data/sira-builds/<build>
    # point the stack .env at serve/<label>/{nora,sira} and RECREATE (a plain
    # `restart` does NOT re-read .env or the env_files — recreate does):
    docker compose --env-file <stack .env> --profile serve up -d
    # rollback = point .env back at the previous label + `up -d` again

First promotion note: `--nora-build` / `--sira-build` accept ANY dir in build
shape — including pre-docker bare-metal artifacts (an env_dir with
`out/{vectorstore,graph,taxonomy}`, a db_root with `<MNO>__<MMMYYYY>` cells) —
so existing deployments promote their current data as the first label.

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
BOTH stacks' sira-query — a plain `restart` suffices here (data reload, same
config); only env-file/.env CHANGES need an `up -d` recreate.

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
(`OFFLINE=0`, the default) work behind such devices**. On a new host with
container-network trouble, `sudo ./debug-egress.sh` pinpoints the blocking
layer (DNS/TCP/TLS × bare-metal/host-net/bridge, plus packet capture).
`docker pull` of base images may still fail there (the docker daemon uses a
different TLS stack) — `./skopeo-pull-bases.sh` covers that:

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

Publish + fetch (either build mode). Both scripts read `GHHOST` / `GHORG` /
`GHREPO` / `GHTOKEN` (a PAT with `repo` scope) from `./.env`:

    # optional rehearsal — stages tarballs in ./push-staging/, uploads nothing,
    # needs no token:
    DRY_RUN=1 ./push.sh images-$(git -C .. rev-parse --short HEAD) \
        <prefix>/nora-web:<tag> <prefix>/sira-query:<tag>

    # publish to the internal GitHub release (host process; image names per
    # your IMAGE_PREFIX/IMAGE_TAG — `docker image ls` shows them):
    ./push.sh images-$(git -C .. rev-parse --short HEAD) \
        <prefix>/nora-web:<tag> <prefix>/sira-query:<tag> \
        <prefix>/nora-pipeline:<tag> <prefix>/sira-batch:<tag>

    # any other internal host:
    ./pull.sh images-<sha>

Assets over `SPLIT_MB` (default 1900) upload as `.partNN` chunks; `pull.sh`
reassembles them transparently. If the GHES per-asset cap turns out lower,
pass a smaller threshold: `SPLIT_MB=900 ./push.sh …`.

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
