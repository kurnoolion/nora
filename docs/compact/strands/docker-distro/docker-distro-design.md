# docker-distro — design sketch

Status: draft for review (architecture phase). Strand: docker-distro.

## Goal — two views, one image set

1. **Developer view** — feature development, model experiments, performance
   tuning; ingesting requirements, changing code, running pipelines. Guarantee:
   *edit code on the host, run it instantly inside a container that already has
   every dependency* — no venv setup, no bm25x build, no Docling model dance.
2. **Operational view** — set up and run services; seamlessly ingest new
   requirement releases and serve them; observability; runtime stack + data
   (BM25 indexes, RAG DBs, graph outputs). Guarantee: *immutable images, all
   state in volumes, `docker compose up` serves, ingest is a job, health is a
   check*.

Same images for both; the **compose profile** (and bind-mounts) is what
differs. No separate dev images to drift.

## Ground-truth constraints (verified)

- **All-CPU images.** SIRA's trimmed install (SETUP.md §2a) runs BM25 + drives
  every LLM stage via OpenAI-compatible HTTP (per-stage routing patch); sglang /
  torch / flash-attn are only for self-hosted inference, which we don't do.
  GPU lives entirely behind the LLM endpoints (proprietary cloud, or on-prem
  DGX / RTX A4600 boxes) — so `sira-batch` runs anywhere.
- **LLM/vision endpoints are external** to the stack, env-configured. Never
  containerized here, never baked, never committed (D-062 discipline).
- **Registry: internal GitHub** (container registry on the internal instance).
  The registry hostname is configuration (`.env`), never committed — committed
  compose references `${REGISTRY}/...` only. Public GitHub mirror never
  receives images or registry names.
- **Docling models / HF artifacts** are provisioned per host into a models
  volume (existing `fetch_docling_models.py` flow), not baked — images stay
  buildable/pullable without model downloads.
- **Multi-instance is a requirement**: two (or more) stacks running
  simultaneously against two different LLMs (A/B), as `run_stack.sh` does
  bare-metal today (D-120): own ports, own LLM env, own web state DBs, pooled
  feedback DB for attributable comparison.

## Image set (4, all CPU)

| Image | Contents | Role |
|---|---|---|
| `nora-pipeline` | NORA repo deps (`requirements.txt`), pymupdf/pdfplumber/docx/openpyxl, Docling (CPU torch), the SIRA adapter | Ingest jobs (extract→…→vectorstore, `nora_to_beir`); dev toolbox shell |
| `sira-batch` | SIRA clone + **trimmed** venv, bm25x built at image-build time (compat polars wheel baked — kills the AVX2 segfault class), configs/prompts via `install_configs.sh` | `sira_multi` / `run_pipeline.py` ingest + enrichment jobs; `sira_incremental` |
| `sira-query` | Entry-point variant of `sira-batch` (needs the same venv + bm25x) | The per-query service (:8040-class) |
| `nora-web` | Slim: web deps only | The web app (:8000-class) |

`sira-query` starts as `sira-batch` with a different entrypoint (one Dockerfile,
two targets); split into a slimmer image later only if size actually hurts.
Baking the SIRA clone into `sira-batch` freezes a known-good SIRA commit per
image tag — upgrades become an image rebuild, not an in-place `git pull`
(reproducibility win over the current gitignored-clone approach).

## Compose topology

One `docker-compose.yml`, three profiles, instanced by project name + env-file:

    # OPS — serve
    docker compose --profile serve up -d          # nora-web + sira-query

    # OPS — ingest a new release (jobs; then service restart)
    docker compose --profile ingest run --rm nora-pipeline \
        python -m core.src.pipeline.run_cli --env-dir /data/env --start extract --end parse --mno <MNO> --release <REL>
    docker compose --profile ingest run --rm nora-pipeline \
        python -m sandbox.adapter.nora_to_beir --env-dir /data/env --output /data/db --multi-cell --only <MNO>__<REL> --wipe-stale-index
    docker compose --profile ingest run --rm sira-batch \
        python -m sandbox.sira_multi --db-root /data/db --sira-clone /opt/sira --run-name "$RUN" --only <MNO>__<REL>
    docker compose restart sira-query             # picks up the new cell; verify /healthz

    # DEV — same services, host source bind-mounted, uvicorn --reload
    docker compose --profile dev up
    docker compose run --rm dev-shell             # toolbox: repo mounted, all deps present

    # A/B — two stacks, two LLMs (the run_stack.sh pattern, containerized)
    docker compose -p stack-a --env-file .env.stack-a --profile serve up -d
    docker compose -p stack-b --env-file .env.stack-b --profile serve up -d

Multi-stack rules (from D-120): every port, LLM URL/model, and web-state path
is an env variable with per-stack values; the **feedback DB is a shared,
pooled volume** mounted into every stack (attributable A/B); the SIRA
`db_root` volume is shared read-only by serving stacks (one ingest, N servers).

## State layout (all volumes / bind mounts — never in images)

| Mount | Contents | Shared across stacks? |
|---|---|---|
| `/data/env` | NORA `env_dir` (input/, out/, state/, corrections/, reports/) | yes (ingest writes, serve reads) |
| `/data/db` | SIRA db_root (`<MNO>__<MMMYYYY>/` cells: raw, index, enrichments, runs cache) | yes; serving mounts ro |
| `/data/models` | Docling artifacts, HF caches (`DOCLING_ARTIFACTS`, `HF_HUB_OFFLINE=1`) | yes |
| `/data/web-state-<stack>` | jobs/metrics/config DBs | per stack |
| `/data/feedback` | pooled feedback DB (D-120) | yes — deliberately |

## Config & redaction boundary

- Committed: `docker-compose.yml`, Dockerfiles, `env.example` (placeholders:
  `REGISTRY=`, `NORA_LLM_BASE_URL=http://127.0.0.1:PORT/v1`, …).
- Per host, gitignored: `.env`, `.env.stack-*` — real registry host, real LLM
  endpoints/models/keys. Mirrors `customizations/mappings/` discipline (D-062):
  the public repo never sees an internal hostname.
- Images contain **no** endpoints, keys, mappings, models, or corpus data.

## Observability (v1 deliberately thin)

- Compose `healthcheck` on `sira-query /healthz` and the web app; `restart:
  unless-stopped`; `docker logs` per service.
- The web app's existing metrics/jobs DBs (REQ/LLM/PIP/RES/MET) remain the
  application-level observability surface — surfaced per stack.
- Deferred: Prometheus/Grafana/central log shipping until a concrete need.

## Build & distribution flow

Build on the dev PC (unrestricted network) → push to the internal GitHub
registry → work PC / other hosts `docker compose pull`. Tags: `:git-<sha>` +
`:latest`; the compose pin is an env var. Fallback for a registry-less host:
`docker save | load` tarballs. (CI builds on internal runners: later nicety.)

## Phasing

1. **Serve**: Dockerfiles for `nora-web` + `sira-query`(+batch base), compose
   `serve` profile over existing host data. Exit: `docker compose up` replaces
   README §3 on one host.
2. **Ingest jobs**: `nora-pipeline` image; run README §2 scenario S3 (new MNO)
   fully containerized. Exit: a release ingested end-to-end via `compose run`.
3. **Multi-stack A/B**: env-file parameterization + pooled feedback volume;
   two stacks, two LLMs, side by side. Exit: run_stack.sh parity.
4. **Dev profile**: bind-mounts + `--reload` + `dev-shell` toolbox (optional
   `devcontainer.json` on top). Exit: a code edit on host is live in-container
   without rebuild; a pipeline run needs zero host venvs.
5. **Docs + hardening**: README §1/§3 rewritten to compose; healthchecks;
   image-size passes; retire `run_stack.sh` (or keep as the no-docker path).

## Decision candidates (D-DRAFT at close-session)

1. Four CPU-only images + compose profiles; GPU externalized to LLM endpoints.
2. Dev view = same images + bind-mounts (no separate dev images).
3. All state in volumes; models provisioned per host, never baked.
4. Env-file config boundary carries D-062 to containers (registry + endpoints
   never committed).
5. SIRA clone baked into `sira-batch` at a pinned commit (rebuild-to-upgrade).
6. Multi-stack via compose project + env-file, pooled feedback volume (D-120
   parity).

## Open questions

- Does the internal GitHub registry allow pulls from every target host
  (proxy/cert story), or is `save/load` the realistic day-1 channel?
- `sira-query` reload-without-restart endpoint (nice-to-have; restart is fine
  for v1).
- Web app assumes paths from `config/web.json` — confirm all paths it needs are
  representable under the `/data/*` mounts (path_mappings feature may help).
- Where do eval runs (`run_pipeline` eval stages / 18-Q) fit — `nora-pipeline`
  job or dev-shell-only?
