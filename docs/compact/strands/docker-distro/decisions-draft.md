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

**Amended 2026-07-07 — build locus is the work PC.** The dev PC cannot reach
the internal GHES and no file-transfer channel exists between the PCs, so
images are BUILT on the work PC and pushed from there. Two proxy accommodations
make work-PC builds viable: base images arrive via skopeo
(`skopeo-pull-bases.sh` — `docker pull` is proxy-broken but `skopeo copy →
docker load` is the proven pattern from the dgx/spark stacks), and torch
installs from PyPI (`--build-arg TORCH_INDEX_URL=https://pypi.org/simple`;
download.pytorch.org is allowlist-blocked). Dev-PC builds remain a fast
verification loop only — never the release. Rejected at this step: publishing
tarballs through the public github.com mirror (work PC could pull them, but
binary artifacts on the public repo were not worth it when work-PC builds
work).

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

## D-DRAFT-5 — Lane model: standards after resolve; ingestion/nora/sira lanes

**Context.** The 9-stage pipeline mixed source acquisition with retrieval-stack
construction, and SIRA's ingest (adapter → index/enrich) sat outside the runner
entirely — awkward for docker job orchestration and for the planned hybrid
retrieval (SIRA's enriched BM25 replacing rank_bm25, fused with taxonomy/
graph). `standards` sat after `taxonomy` by historical accident: run_standards
reads only resolve manifests + parse trees (never taxonomy), and skip-resolve
already implied skip-standards.

**Decision.** Reorder `standards` to stage 5 (after resolve), closing the
INGESTION lane (extract, profile, parse, resolve, standards — common source
acquisition/structuring for both retrieval lanes). Stages 6-9 (taxonomy, graph,
vectorstore, eval) are the NORA lane; `run_cli --lane ingestion|nora` is sugar
over --start/--end (PIPELINE_LANES). The SIRA lane stays sandbox-side (D-111
boundary — core never imports sandbox), with `sandbox/sira_lane.py` as its
single entrypoint (adapter + sira_multi, --only/wipe threaded through both) and
a SOURCE.json provenance stamp in every db_root. Lanes are a RUNNER concept:
the env_dir/out/<stage> filesystem layout (D-096) is unchanged.

**Why.** The three lanes map 1:1 onto the docker job types (nora-pipeline
ingestion job, nora-pipeline nora-lane job, sira-batch job) and can run
independently/in parallel after ingestion. Rejected: encoding lanes in the
directory layout (churns every consumer for zero gain); adding SIRA stages to
the core runner (inverts the D-111 boundary).

**Consequences.** Numeric --start/--end shifted (5=standards, 6=taxonomy);
stage NAMES unchanged, docs use names. The hybrid-retrieval future (promote
sira_query/fusion into core behind a retriever Protocol, superseding D-111's
informal scope for those modules) is explicitly deferred to its own strand —
this re-org is what makes it cheap later.

## D-DRAFT-6 — Build/serve data topology: requirements store, build dirs, hardlink serve snapshots

**Context.** Data lived ad hoc: inputs copied per env, SIRA db_roots INSIDE the
repo tree (a `git clean -dx` would have wiped the ~13h enrichment cache), web
state DBs mixed into env_dir/state with per-stack FILENAME suffixes, and
rebuilds mutated what running services were reading. User's design goal: build
dirs hold only build artifacts; serving reads a dedicated, stable surface.

**Decision.** One `nora-data/` root: `requirements/<MNO>/<MMMYYYY>/` = THE
canonical source store, shared by all builds (new EnvironmentConfig
`requirements_dir` override + NORA_REQUIREMENTS_DIR + --requirements-dir;
default stays <env_dir>/input for back-compat); `nora-builds/<build>/` and
`sira-builds/<build>/` = build artifacts only, never inside the repo tree;
`serve/<label>/` = HARDLINK snapshots (`docker/promote.sh`, cp -al) of the
serve-set — nora/out/{vectorstore,graph,taxonomy} env-SHAPED (mounts as
/data/env with zero web change) + sira cells + MANIFEST.json. Labels are
immutable; rollback = repoint the stack .env; per-stack dirs (web-state-<x>)
replace filename suffixes; feedback stays ONE pooled dir (D-120).

**Why.** Hardlinks make promotion instant, ~zero-disk, container-safe (real
files, unlike symlinks), and WIPE-IMMUNE — rebuilding or wiping a build never
mutates a promoted snapshot (verified by deleting the source build). The
serve-set became small and well-defined once Parse Review/corrections were
declared phase-out (query lane needs no out/parse). Rejected: serve-as-copies
(GBs per promotion), serve-as-symlinks (break in containers), serve-as-
pointers-only (leaves serving exposed to in-place rebuilds).

**Consequences.** Requires one filesystem for builds+serve (hardlinks);
promote only when no build is mid-write; old labels GC'd manually. SOURCE.json
in db_roots + MANIFEST.json in labels give provenance both ways. Blue/green
and ingestion-A/B fall out free (two stacks on two labels/builds).

## D-DRAFT-7 — Per-service runtime env files; compose environment: as the precedence guard

**Context.** A single env.example mixed four services' knobs with build/stack
wiring. An audit found the services read ~65 env vars while compose forwarded
~30 — the rest (incl. the D-104 reasoning sentinel, the select-synth pin/synth
group, the D-116/117 rerank-backend group) silently defaulted in containers.
Also found: endpoint vars interpolated as ${VAR:-} rendered EMPTY STRINGS that
would override any env file, and one documented var was a phantom
(NORA_LLM_SHIM_MODEL — nothing reads it; the service reads NORA_LLM_MODEL).

**Decision.** Split config into: `.env` (compose interpolation only — build
args, ports, volume paths, per-service file POINTERS ${*_ENV_FILE:-…}) and one
runtime env file per image (`env.<service>.example` → `.env.<service>`),
injected via per-service compose `env_file:` (optional, per-stack overridable).
Each example documents exactly the knobs its service reads, including per-file
/v1 conventions and default-on knobs. Compose `environment:` blocks carry ONLY
fixed container paths + inter-service URLs — they always win, so runtime files
can never break the container layout. (A shared .env.runtime passthrough was
tried first and superseded the same day by this split.)

**Why.** Containers only receive explicitly-mapped env; per-service files make
the full tunable surface visible where the operator edits, keep unrelated
services' knobs apart, and make A/B stacks a pointer swap. Empty-interpolation
override and phantom-var classes are structurally eliminated (endpoint vars no
longer appear in compose at all).

**Consequences.** A typo'd pointer silently loads nothing (env_file is
required:false) — `compose config` / `/healthz` is the check. Unset var ==
code default == bare-metal behavior; the minimal per-stack file is ~4 lines
(endpoints + explicit toggles like RERANK_ENABLED=false and the per-LLM
sentinel).

## D-DRAFT-8 — Offline image builds on agent-guarded hosts (host-vendored dependencies)

**Context.** On the work PC every network egress from CONTAINER processes is
reset by the endpoint-security agent — pip/apt/rustup/clone fail identically
with direct, proxy, and even --network=host configurations — while HOST
processes are allowed (skopeo, pip, git all work bare-metal). Decisively:
containers CAN reach internal endpoints (probe returned 200), so serving and
push.sh remain viable; only builds needed rework. This supersedes the
proxy-accommodation framing in D-DRAFT-2's amendment (TORCH_INDEX_URL=PyPI +
proxy build args — kept for merely proxy-restricted hosts, insufficient here).

**Decision.** `docker/prep-offline.sh` fetches/builds everything HOST-side into
gitignored `docker/vendor/`: pip-wheels for torch/requirements/docling/the
trimmed SIRA set (pip wheel builds sdists like pytrec_eval), the bm25x wheel
via host maturin (SETUP §2/3 toolchain; SKIP_BM25X escape hatch), and the SIRA
clone at SIRA_REF pre-patched on the host (install_configs.sh gained a
SIRA_CLONE override), plus PREP.json. `OFFLINE=1` builds consume vendor with
ZERO in-container network: wheels are BIND-MOUNTED during RUN (never COPY'd —
nothing added to layers), the SIRA source stage is ARG-selected
(sira-src-${OFFLINE}), and offline mode needs no apt/git/gcc in any stage.
Compose wires vendor as an additional_context and OFFLINE from .env.

**Why.** Host processes are the only allowed network path, so the host is
where fetching must happen; bind-mounts keep multi-GB wheels out of image
layers; stage selection keeps one Dockerfile serving both worlds (dev PC
builds ONLINE by default). Rejected: proxy tricks (agent is process-based, not
network-based), committing vendor (multi-GB git history), separate offline
Dockerfiles (drift).

**Consequences.** Dependency bumps on the work PC require a prep re-run;
vendor is per-host disk (~2.6GB+; PyPI torch drags nvidia wheels — the
base/app image split gains urgency). Runtime external downloads (the standards
stage) stay blocked in containers on such hosts: --skip-standards or run that
stage bare-metal. Validation status at capture: online path green, vendor
harvest done, --network=none build + serve smoke pending (recorded in 0808b2b).
An IT policy change permitting container egress would obsolete this machinery.
