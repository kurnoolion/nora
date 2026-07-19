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

## 2026-07-10 — Env architecture, host topology, lanes, and the offline-build pivot

### Done this session (20 commits, 2026-07-07→10)
- Build locus moved to the WORK PC (D-DRAFT-2 amended in place): dev PC cannot
  reach the internal GHES and no inter-PC file channel exists; base images via
  `skopeo-pull-bases.sh` (mirrors the proven dgx/spark workaround);
  TORCH_INDEX_URL build arg for the allowlist-blocked CPU index.
- Work-PC bring-up traps fixed + encoded: git exec bits (WSL working tree shows
  777 — set mode in the index), snap-docker cannot read /tmp ("no such file")
  NOR hidden top-level dirs like ~/.cache ("permission denied"), docker group
  needs a session re-login. skopeo scratch → visible $HOME default.
- Env-config architecture: build knobs driven from .env (compose build.args);
  env audit found ~65 vars read vs ~30 forwarded (incl. sentinel D-104, pin/
  synth group, rerank backend D-116/117) → brief shared .env.runtime → replaced
  by the PER-SERVICE split: env.<service>.example → .env.<service>, compose
  env_file per service (${*_ENV_FILE:-…}, per-stack overridable), environment:
  carries ONLY fixed container paths and always wins — which also fixed empty
  ${VAR:-} interpolations silently overriding file values. A/B recipe: per-stack
  wiring env + per-stack web/query files; pipeline/batch files shared.
- Host data topology (user's build/serve design): nora-data/ root with
  requirements/ (ONE canonical source store — new core requirements_dir
  override, default env_dir/input, back-compat tested), nora-builds/,
  sira-builds/ (user migrated all builds; symlink check clean), serve/<label>/
  hardlink snapshots via promote.sh (verified: instant, wipe-immune, immutable
  labels, env-shaped nora/ mounts as /data/env with zero web change; rollback =
  repoint .env). Parse Review/corrections being phased out made the serve-set
  small (vectorstore/graph/taxonomy + sira cells; query lane needs no out/parse).
- Lane model shipped: standards → stage 5 (reads only resolve manifests + parse
  trees; skip-resolve already implied skip-standards), lanes ingestion(1-5) /
  nora(6-9) as `run_cli --lane` sugar (PIPELINE_LANES), sira lane via new
  sandbox/sira_lane.py (adapter+sira_multi, --only/wipe threaded), SOURCE.json
  provenance stamped into db_roots, lane-grouped --list-stages. Filesystem
  layout unchanged (lanes are a runner concept). Suite 1555-1560 green.
- Template hardening from the user's line-by-line env review: /v1 conventions
  now stated in every file that has one (openai-compatible WITH /v1; service
  WITHOUT; batch WITH); sira-batch rerank vars marked eval-flow-only;
  NORA_LLM_SHIM_MODEL exposed as a PHANTOM VAR (nothing reads it — the service
  reads NORA_LLM_MODEL) and fixed; five default-on knobs confirmed
  (scale-topk/fanout/enrich/expansion-weight/query-enrich) = "unset == bare-
  metal behavior".
- THE discovery: the work-PC endpoint-security agent RESETS ALL network egress
  from container processes (even --network=host; host processes allowed —
  skopeo/pip/git work bare-metal), but containers CAN reach internal endpoints
  (user probe: 200) → serving viable, builds must be offline. Shipped
  prep-offline.sh (host-side: pip-wheels everything incl. pytrec_eval + bm25x
  via host maturin; clones SIRA @ pin and applies install_configs on the HOST
  via new SIRA_CLONE override; PREP.json) + OFFLINE=1 Dockerfile branches
  (vendor wheels BIND-MOUNTED during RUN — zero layer cost; ARG-selected
  sira-src stage; no apt/git/gcc anywhere in offline mode) + compose vendor
  additional_context.

### In progress
- Offline-build validation on the dev PC: vendor harvest 30 wheels/2.6GB done
  (incl. torch cp312 + bm25x cp312 extracted from the online builder stage);
  interrupted by shutdown. Resume: SKIP_BM25X=1 ./prep-offline.sh →
  --network=none builds of sira-query + nora-web → serve smoke.

### Next
- Work-PC pass: git pull → prep-offline (host rust/maturin) → OFFLINE=1 build →
  compose up over real serve/<label> data → /healthz → first push.sh release.
- Ask IT whether the endpoint agent can permit container egress on the work PC
  (a policy fix would delete the offline machinery).
- GHES release-asset size limit (admins) — decides split archives.
- Phases 3 (A/B live), 4 (dev profile + dev-shell), 5 (README §1/§3 → compose;
  run_stack.sh fate).

### Flags
- SETUP.md §2 clone still unpinned (fresh bare-metal clones break) +
  install_configs.sh still swallows git-apply stderr — both small, still open.
- standards stage downloads specs at RUN time — blocked in containers on
  agent-guarded hosts: --skip-standards there, or run that stage bare-metal.
- Base/app image split still deferred (matters more now: PyPI torch pulls
  nvidia wheels — vendor is 2.6GB+, images will be bigger than the CPU-index
  dev builds).
- OFFLINE validation pending — 0808b2b commit message records exactly what was
  and wasn't verified at commit time.
- env/pipeline MODULE.md now lag the code: PIPELINE_LANES, requirements_dir/
  input_root, resolve_requirements_dir, --lane/lane_bounds are new public
  surface; Structure sections also stale. Fold into the next regen-map +
  MODULE.md pass (or land-time drift-check dev-full).

## 2026-07-14 — Work-PC validation: DPI root cause, online builds restored, both goals green

### Done this session
- **Container-egress mystery solved** (debug-egress.sh, packet capture): the
  perimeter DPI resets large post-quantum TLS ClientHellos (image OpenSSL 3.5,
  ~1545B); host tools on OpenSSL 3.0 (~315B) pass. IT's IP-whitelist theory
  refuted (bridge TCP connects; host-net also failed); the endpoint-agent
  theory retired. Fix: classical-groups OPENSSL_CONF baked into both
  Dockerfiles → ONLINE builds work on the work PC; nora-pipeline 3.13GB vs
  9.67GB offline. OFFLINE=1 demoted from required to optional.
- **Goal 1 (serve) validated on the work PC**: promote.sh over pre-docker
  artifacts → serve up → healthz → query → enrichment (NORA_LLM_MODEL required
  against real endpoints — template + README fixed).
- **Goal 2 (incremental ingest) validated**: ingestion lane extract→parse in
  docker with parse parity vs the bare-metal reference build.
- **Five silent-corruption bugs flushed out by containerization**, all fixed
  loud + tested: (1) infer_metadata_from_path root-anchored for
  requirements_dir overrides; (2) extracted images → build output (images_root;
  read-only corpus safe); (3) docling convert failures now fail loud (were
  producing tableless parses); (4) torch/torchvision same-index pairing +
  build-time dep-chain canary + headless opencv; (5) reports → <env_dir>/reports
  (were lost to the container overlay).
- **Ops/DX**: push.sh streams uploads (curl -T; OOM fix); SECRETS_ENV_FILE
  overlay (committable per-service envs); JOB_UID/JOB_GID non-root ingest jobs
  (host-owned artifacts); sira-batch /data/env mount gap fixed; ingest.sh
  detached lane launcher; README "The four images" reference + data-flow
  diagram; env.example GH vars commented (empty values clobbered exports).

### In progress
- First push.sh release: classic PAT obtained; upload OOM fixed; user retrying.

### Next
- sira lane in docker (sira-batch first run — exercises the new /data/env mount).
- Stack-b bring-up (env copies + its model name); promote the new build → flip serve.
- Update D-DRAFT-8's premise at land time (offline: required → optional).
- regen-map + env/pipeline/extraction MODULE.md refresh (new public surface:
  images_root, requirements_dir/lanes — see Flags).

### Flags
- The "endpoint agent resets container egress" claim in older journal entries /
  D-DRAFT-8 is superseded by the DPI/PQ-hello root cause (this entry).
- extraction MODULE.md now also lags: images_root param on the extract surface.
- One flaky core test observed (failed once, passed twice on rerun) — unidentified.
- docker pull still broken work-PC side (daemon's TLS stack; likely same DPI
  cause via Go's PQ hello) — skopeo-pull-bases.sh remains the workaround; a
  GODEBUG=tlsmlkem=0 daemon-env experiment is untried.
- Standards-stage note: with classical-TLS baked in, in-container external
  fetches should now pass the DPI — untested; --skip-standards fallback stands.

## 2026-07-16 — Addendum: post-close fixes + scope spillover

Work continued past the 07-14 close while validating on the work PC; all in
this strand's spirit (first-run gauntlet of containerized sira-batch) except
where noted as spillover:

- sira-batch gauntlet cleared, one loud failure at a time: builds/stack env
  file discipline (ingest commands take .env.builds*, never a stack env —
  the label mount has no out/parse by design, which protected the snapshot);
  writable baked clone for JOB_UID (per-cell data configs are written INTO
  the clone — SIRA reads configs/data/<cell>.yaml by name); PYTHONPATH to
  the clone src (bare-metal's editable install has no image equivalent);
  ENRICH routing vars must be set or SIRA tries to launch local sglang
  (absent from trimmed images by design). README runbook added for
  retry-failed enrichments.
- **Spillover (query/web feature work, committed under this session but
  belonging to nora-retrieval-quality / team-eval-pilot scope):**
  - MNO alias extraction token-boundary fix (2939cc7) — substring aliases
    ('att' in 'attach') silently mis-scoped multi-MNO queries; regression
    tests added. Also identified the order-dependent glossary-pin test
    flake (test_query_intent before test_query).
  - Test-page ingested-corpus table (5a1e0e9, e902457, 7070d68): per-cell
    MNO/release/plans/requirements/ingested + Latest badge + Lane column;
    sira-query gained GET /cells so SIRA-only cells (no nora vectorstore)
    are visible — inventory merges both lanes over NORA_SIRA_QUERY_URL.
  Consider folding these into those strands' narratives at their next
  close, or noting at land time.

## 2026-07-18 — Full lifecycle confirmed; per-lane mismatch display

### Done this session
- **Promote-and-flip validated end-to-end on the work PC**: new release
  promoted (nora + sira builds → fresh label), stacks recreated, healthz
  lists the new cell, test-page corpus table shows ALL MNOs — including
  SIRA-only cells — with Latest + Lane badges. The full docker lifecycle
  (ingest → build → promote → serve) is now proven on real data.
- Per-lane mismatch display (87bcc26): both-lane cells whose NORA/SIRA
  counts diverge render both numbers as lane-colored badges — surfaced by
  a real case (stale/planless vectorstore vs fresh SIRA corpus reporting
  0 vs 87 plans). Doubles as a lane-staleness indicator.
- (Everything between the 07-14 close and this entry — sira-batch
  gauntlet, retry-failed runbook, alias fix, corpus table + /cells — is
  recorded in the 07-16 addendum above.)

### In progress
- push.sh release: still pending retry (PAT ready, streaming upload fixed).

### Next
- Stale-vectorstore cleanup: rebuild or delete the nora-lane cells that
  disagree with SIRA (the mismatch badges point at them) — table collapses
  to single numbers once lanes agree.
- Landing sequence when architect-ready: /drift-check dev-full → regen-map
  + MODULE.md refresh (extraction/env/pipeline/web + sira_query surface) →
  /land-strand docker-distro (13 drafts).
- Stack-b bring-up remains untouched.

### Flags
- sira_query service gained public surface (GET /cells) consumed by the
  web test page — a NEW cross-service dependency (web → sira-query) that
  MODULE.md / MAP dependency edges don't yet record. Fold into the
  pre-land refresh.
- Glossary-pin test flake: identified as order-dependent (test_query_intent
  before test_query; passes alone) — root cause (shared retriever state)
  still unchased.
