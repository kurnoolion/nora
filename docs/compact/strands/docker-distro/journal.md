## 2026-07-07 — Design + phase 1: serve stack built and smoke-tested

### Done this session
- Design doc (`docker-distro-design.md`): two views (developer/operational),
  ONE image set — four CPU-only images (nora-pipeline, sira-batch, sira-query,
  nora-web); GPU externalized to the OpenAI-compatible LLM endpoints (SETUP §2a
  trimmed install has no torch/sglang). Compose profiles serve/ingest/dev over
  env-var'd volumes/ports; multi-stack A/B (two LLMs side by side) via
  `-p <stack> --env-file .env.<stack>` with a pooled feedback volume (D-120
  parity). 5-phase rollout.
- Distribution channel settled: internal GHES has Packages DISABLED (the
  packages PAT scopes are absent from the token UI — the tell), so images ship
  as docker-save tarballs on internal-GitHub Release assets via curl-only
  `push.sh`/`pull.sh`. User probe passed (300 MB asset up+down). Asset size
  limit still TBD with admins; base/app image split designed as the
  no-layer-dedup mitigation.
- Phase 1 shipped (`docker/`): nora.Dockerfile (nora-base → nora-web /
  nora-pipeline+Docling), sira.Dockerfile (bm25x-builder → trimmed sira-base
  with the upstream clone baked + install_configs applied → sira-query /
  sira-batch), docker-compose.yml, env.example (incl. the /v1-vs-no-/v1 rerank
  URL split), push.sh/pull.sh, README. Smoke-tested on the dev PC: both serve
  containers healthy; sira-query (1.39 GB) /healthz graceful on empty db_root;
  nora-web (2.51 GB) serves HTTP 200.
- Five portability lessons encoded in the Dockerfiles: (1) pytrec_eval needs
  gcc → build-then-purge layer; (2) the bm25x wheel must be built against the
  runtime's exact Python → builder based on python:3.12-slim + rustup, not a
  rust image; (3) upstream SIRA main has drifted — per-stage-routing.patch no
  longer applies there → SIRA_REF pinned to 62ec59c (the commit our working
  clones run); (4) python:3.12-slim has no git and install_configs.sh's
  `2>/dev/null` turned that into a misleading "patch does not apply" → git baked
  into the runtime; (5) snap-docker (this dev PC) cannot read paths outside
  $HOME — env files and volumes must live under /home.

### In progress
- Phase 2: validate the distribution channel + real-data serving on the work
  PC (first real `push.sh` from the dev PC → `pull.sh` → `compose --profile
  serve up` over the real env_dir/db_root).

### Next
- Get the GHES release-asset size limit from admins (decides whether the base
  tarball needs split archives).
- Phase 3 (A/B stacks) and phase 4 (dev profile + dev-shell toolbox).
- Phase 5: rewrite README §1/§3 to compose; decide run_stack.sh's fate.

### Flags
- SETUP.md §2 still says `git clone --depth 1` of upstream main — a fresh
  bare-metal clone breaks install_configs today. Pin 62ec59c there too (small
  docs fix, applies beyond docker).
- install_configs.sh swallows `git apply --check` stderr — misdiagnosed a
  missing git binary as a patch failure. Capture stderr in the error path.
- Base/app image split designed but not yet built — do it when push cadence
  makes the 2.5 GB nora tarball annoying.
- docker/ deliberately has no MODULE.md (infra concern, mirrors the sandbox
  informal-scope precedent D-111) — revisit if it grows real code.
