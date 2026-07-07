## D-DRAFT-1 — One image set, two views: four CPU-only images + compose profiles

**Context.** The distro must serve two personas: developers (feature work,
model experiments, pipeline runs) and operators (run services, ingest new
releases seamlessly, observe). Separate dev/ops image sets would drift; and
SIRA's upstream dependency stack is nominally GPU-pinned, which would make
images host-specific.

**Decision.** Build exactly four CPU-only images — `nora-pipeline` (batch +
adapter + Docling, also the dev toolbox), `sira-batch` (trimmed SIRA venv +
bm25x), `sira-query` (same base, service entrypoint), `nora-web` — and express
the two views as compose *profiles* over the same images: `serve` (immutable
images + volumes + healthchecks), `ingest` (job-style `compose run`), `dev`
(host source bind-mounted, `--reload`, `dev-shell`). GPU is externalized
entirely: every LLM/vision call goes to an OpenAI-compatible endpoint
(proprietary cloud or on-prem DGX/RTX boxes), per SETUP §2a's trimmed install
(no torch/sglang in any image).

**Why.** One image set cannot drift between personas; CPU-only images run on
every host (dev PC, work PC, servers) with the GPU question reduced to "what
the endpoint needs" — which is already how the bare-metal stack works.
Bind-mount dev over the same images gives "edit on host, run in container with
all deps present" without maintaining dev images. Rejected: separate dev
images (drift), a single monolith image (couples web/service/pipeline rebuild
cadences; huge), self-hosted sglang in-image (contradicts the external-endpoint
reality and re-introduces GPU pinning).

**Consequences.** nora images carry CPU torch (~2.5 GB) for
sentence-transformers/Docling — size is the accepted cost of one-image-set.
Compose is the only supported orchestrator for now. The dev-view guarantee
depends on images tracking requirements.txt — dependency changes require an
image rebuild (bind-mounts cover code only).

## D-DRAFT-2 — Distribution via internal-GitHub Release assets (Packages disabled)

**Context.** Images must move dev PC → work PC/servers through the internal
network. The internal GHES has GitHub Packages (and thus its container
registry) disabled — the `read:packages`/`write:packages` scopes are absent
from the PAT UI. Committing tarballs into git history was rejected outright
(permanent multi-GB bloat; we have force-push scars).

**Decision.** Ship images as `docker save | gzip` tarballs attached to
Releases on the internal `<team>/nora` repo: `push.sh` (create release by tag,
upload assets) and `pull.sh` (download by tag + `docker load`), both curl-only
against the GHES API so target hosts need no gh CLI. Release tag = image
version (`images-vN-<git-sha>`). Verified by a 300 MB probe (upload +
download) on the work PC.

**Why.** Releases are core GHES (can't be disabled), asset storage is outside
git history, and the flow mirrors the team's existing internal-GitHub habits.
Rejected: container registry (disabled; becomes a drop-in upgrade if admins
ever enable it — layering is already registry-optimal), git-committed
tarballs (history bloat), ad-hoc scp (no versioning/audit).

**Consequences.** No layer dedup — every update ships full tarballs; mitigated
by the designed base/app image split (rarely-rebuilt torch+Docling base vs a
~100-300 MB app layer) — split deferred until push cadence hurts. GHES asset
size limit (2 GB default, TBD with admins) may force split archives for the
base tarball. `pull.sh` is the only distribution dependency on target hosts.

## D-DRAFT-3 — SIRA upstream baked into the image at a pinned commit

**Context.** Bare-metal installs clone upstream SIRA at `main` into the
gitignored `sandbox/sira/` and patch it via `install_configs.sh`. During the
phase-1 image build, a fresh clone at today's `main` FAILED the
per-stage-routing patch — upstream has drifted since our working clones were
made. The bare-metal instruction is silently broken for any new machine.

**Decision.** `sira.Dockerfile` clones upstream at build time pinned to
`SIRA_REF` (default `62ec59c…` — the commit our patches and configs are
verified against), applies `install_configs.sh` at build, and bakes the result
into `sira-base`. Upgrading upstream = deliberately bumping `SIRA_REF`,
re-verifying the patches apply, and rebuilding — never an in-place `git pull`.

**Why.** Reproducibility: an image tag now freezes a known-good (upstream ×
patches × configs) combination, and target hosts need no github.com access.
The build failure proved `main` is not a stable base. Rejected: cloning at
container start (network dependency at runtime, unpinned), vendoring the
patched clone into our repo (license/size, loses upstream provenance).

**Consequences.** SETUP.md's bare-metal clone instruction must pin the same
ref (flagged). Patch maintenance is now explicit: bump ref → re-verify →
rebuild. The runtime image carries git (install_configs needs `git apply`;
its swallowed stderr masked exactly this — flagged for a script fix).

## D-DRAFT-4 — State/config boundary: volumes + per-host env files (D-062 in containers)

**Context.** Images will be shared through the internal repo and must carry
zero proprietary or host-specific material; meanwhile the stack needs heavy
per-host state (env_dir, SIRA db_root, Docling/HF models, web DBs) and
per-stack config (ports, LLM endpoints) — including two simultaneous stacks
on different LLMs.

**Decision.** Images bake NO endpoints, keys, mappings, models, or corpus
data. All state lives in host-mounted volumes (`/data/env`, `/data/db`,
`/data/models`, per-stack `/data/web-state`, pooled `/data/feedback`); all
config comes from gitignored per-host env files (`.env`, `.env.<stack>`) with
a committed placeholder `env.example`. Multi-stack A/B = compose project name
+ env-file (per-stack ports/LLM/web-state; shared read-only-ish db_root;
pooled feedback DB for attributable comparison — run_stack.sh/D-120 parity).
Models are provisioned once per host into the models volume
(`DOCLING_ARTIFACTS` + `HF_HUB_OFFLINE=1`), never baked.

**Why.** Carries the established D-062 redaction boundary (placeholders in
committed files, real values per-host) unchanged into the container world —
the public mirror never sees an internal hostname even in compose files.
Volumes keep images host-portable and data lifecycle independent of image
lifecycle. Rejected: baking models (images become huge and host-coupled),
named docker volumes for corpus data (harder to inspect/back up than the
existing on-disk layouts operators already know).

**Consequences.** First run on a new host requires filling `.env` and
provisioning the models volume (documented in docker/README + env.example).
Snap-docker hosts must keep env files and volume paths under $HOME (confinement
can't read /tmp — hit on the dev PC). The same env-var names carry the known
/v1-vs-no-/v1 rerank URL convention split; env.example documents it.
