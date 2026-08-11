# docker/ — NORA + SIRA distribution (strand: docker-distro, phase 1)

Design: `docs/compact/strands/docker-distro/docker-distro-design.md`.
Four CPU-only images, two views (dev/ops) via compose profiles (the `dev`
profile — bind-mounts + dev-shell — lands in phase 4; `serve`/`ingest` are
live); images ship as Release-asset tarballs on the internal GitHub
(`push.sh` / `pull.sh`).

## The four images

| Image | Profile | Runs | Purpose | Reads (mounts) | Writes (mounts) |
|---|---|---|---|---|---|
| **nora-web** | serve | uvicorn `:8000`, long-lived | Web UI + API: query, compare, compliance views; jobs/metrics/config; feedback capture | `/data/env` (promoted nora label: vectorstore, graph, taxonomy) | `/data/web-state` (per stack), `/data/feedback` + `/data/corrections` (pooled) |
| **sira-query** | serve | uvicorn `:8040`, long-lived | Per-query retrieval service: BM25 over the cells + LLM query-enrichment / rerank | `/data/db` (promoted sira label: `<MNO>__<MMMYYYY>` cells), `/data/corrections` (ro — overlay applied at cell load) | — |
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
    │                                  #   (+ prompts/ + RECIPE.md when the build is a
    │                                  #   variant lineage — see "Variant lineages")
    ├── sira-builds/<build>/           # SIRA db_roots (cells) — NEVER inside the repo
    │                                  #   tree (git clean would wipe the enrichment cache)
    ├── serve/<label>/                 # promoted hardlink snapshots (promote.sh):
    │   ├── MANIFEST.json              #   nora/out/{vectorstore,graph,taxonomy} +
    │   ├── nora/out/...               #   sira/<cells>. Stacks mount THESE — builds
    │   └── sira/...                   #   can be rebuilt/wiped without touching serving.
    ├── web-state-a/                   # per stack: nora_jobs/metrics/config.db
    ├── web-state-b/                   #   (stack identity = directory, not filename)
    ├── feedback/                      # ONE pooled dir for ALL stacks (D-120)
    ├── corrections/                   # ONE pooled dir: enrichment-review overlay
    │                                  #   (web writes, sira-query reads at load)
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
    CORRECTIONS_DIR=/home/<you>/nora-data/corrections
    MODELS_DIR=/home/<you>/nora-data/models
    GOLDEN_DIR=/home/<you>/nora-data/eval-golden   # pooled golden eval set; like corrections, never inside a serve label

Create the directories before `up` (docker creates missing bind sources
root-owned, and the app then can't write its DBs). Then bring the stack up —
**from source** (dev PC, Dockerfile verification):

    docker compose --profile serve up -d --build
    curl localhost:8040/healthz  # cells loaded
    open http://localhost:8000

— or **from a published release** (any internal host; the normal ops path):
next section.

## Bring up from a published release (no source build)

Serving hosts run the images published by `push.sh` (see §Build +
distribute) — they never build from source, and need only this `docker/`
directory (scripts + env files), not the whole repo checkout at the release
sha. `pull.sh` reads `GHHOST` / `GHORG` / `GHREPO` / `GHTOKEN` (PAT with
`repo` scope) from `./.env`:

    # 1. fetch + docker-load every image tarball on the release:
    ./pull.sh images-<sha>              # or: ./pull.sh images-<sha> nora-web
    docker image ls                     # loaded as <prefix>/<name>:<tag> —
                                        #   exactly the names push.sh published

    # 2. point compose at the loaded images — in the stack .env:
    #      IMAGE_PREFIX=<prefix>
    #      IMAGE_TAG=<tag>
    #    (compose resolves image: ${IMAGE_PREFIX}/<name>:${IMAGE_TAG};
    #     the defaults local/dev refer to source builds)

    # 3. up WITHOUT --build — passing --build would rebuild from the local
    #    source tree and shadow the release images:
    docker compose --env-file <stack .env> --profile serve up -d
    curl localhost:8040/healthz         # cells loaded
    curl localhost:8000                 # web UI answering

Ingest jobs on that host use the same loaded images automatically (same
`IMAGE_PREFIX`/`IMAGE_TAG` in the builds wiring env) — `compose run` never
rebuilds an image that is already present.

Switching a running stack to a newer release = `./pull.sh images-<newsha>`,
update `IMAGE_TAG` (if it changed), then `up -d` again — compose recreates
only the containers whose image changed. Rollback = re-point
`IMAGE_PREFIX`/`IMAGE_TAG` at the previous release's images (still in the
local docker cache unless pruned; otherwise `./pull.sh` the old tag).

## Ingest a new release — the full cycle

Parse → derive prompts → publish → taxonomy → enrich → promote. The ordering
matters: the Cline prompt-derivation skill reads the **parsed** corpus, and
the taxonomy stage consumes the **derived** corpus overviews. The taxonomy
cache auto-busts when overview files change (fingerprint includes their
hash), so nothing needs `--force` bookkeeping.

Ingest jobs run against the BUILD dirs — never against a promoted serve
label. Placeholders: `<MNO>` / `<MMMYYYY>` = cell coordinates as they appear
under `requirements/`; `<you>` = your home dir; `<build>` = new build name
(e.g. `2026-08-a`).

### Phase 0 — pre-flight (once per cycle)

Make a builds-oriented wiring env once: `cp .env.stack-a .env.builds`
(a FULL copy — compose validates every required path var in the whole file
even for ingest-only runs, so all SEVEN path vars must be present).

```bash
cd docker

# 0.1 snapshot current images as a rollback point (BEFORE git pull, so the
#     tag records the sha the images were built from):
for i in nora-web nora-pipeline sira-query sira-batch; do
  docker tag local/$i:dev local/$i:pre-<cycle>
done
TAG="images-pre-<cycle>-$(git rev-parse --short HEAD)"
DRY_RUN=1 ./push.sh "$TAG" local/nora-web:dev local/nora-pipeline:dev \
  local/sira-query:dev local/sira-batch:dev        # check sizes first
./push.sh "$TAG" local/nora-web:dev local/nora-pipeline:dev \
  local/sira-query:dev local/sira-batch:dev

# 0.2 pull the latest code (internal remote) and rebuild the ingest images —
#     lane code rides IN the images, so any code change must be baked in
#     BEFORE the lanes run:
git pull
docker compose --env-file .env.builds --profile ingest build nora-pipeline sira-batch

# 0.3 create the new build dirs and stage the source docs
mkdir -p /home/<you>/nora-data/nora-builds/<build>
mkdir -p /home/<you>/nora-data/sira-builds/<build>
# source corpus (if not already present for these cells):
#   /home/<you>/nora-data/requirements/<MNO>/<MMMYYYY>/*.pdf|docx|xlsx

# 0.4 profile bindings — a new env dir fails loud (PIP-E003) without them:
cp /home/<you>/nora-data/nora-builds/<previous-build>/profiles.json \
   /home/<you>/nora-data/nora-builds/<build>/profiles.json
# (or author one: each <MNO>/<MMMYYYY> cell -> a repo-relative
#  customizations/profiles/*.json path — those are baked into the image;
#  a single-profile run can bypass via --profile <path>)

# 0.5 repoint the builds wiring env at the new dirs (only these two lines):
#     .env.builds:  NORA_ENV_DIR=/home/<you>/nora-data/nora-builds/<build>
#                   SIRA_DB_ROOT=/home/<you>/nora-data/sira-builds/<build>
```

### Phase 1 — parse the corpus (ALL cells, one shot)

Run the ingestion lane with taxonomy skipped — deriving a taxonomy now would
waste an expensive LLM pass on a context-less prompt; it runs in Phase 4
with the overviews attached. Standards (LLM-free) runs here. (Standards
downloads specs over the PUBLIC network at run time — the baked
classical-TLS config gets python HTTPS through PQ-hello-resetting DPI, but
if a host truly has no container egress: add `--skip-standards`, or run the
standards stage bare-metal.)

Without `--mno`/`--release` the lane processes **every** `<MNO>/<MMMYYYY>`
cell found under the requirements mount — one command covers the whole
corpus (every cell needs a `profiles.json` binding, Phase 0.4, or the run
fails loud with PIP-E003):

```bash
# all cells, detached, console log on the host like ingest.sh does it:
TS=$(date +%Y%m%d_%H%M%S)
docker compose --env-file .env.builds --profile ingest run -d --rm -T nora-pipeline \
  sh -c "mkdir -p /data/env/reports && exec python -m core.src.pipeline.run_cli \
    --env-dir /data/env --lane ingestion --skip-taxonomy \
    > /data/env/reports/lane-ingestion-ALL-$TS.log 2>&1"
tail -f /home/<you>/nora-data/nora-builds/<build>/reports/lane-ingestion-ALL-$TS.log

# sanity: one parse dir per cell, trees inside:
ls /home/<you>/nora-data/nora-builds/<build>/out/parse/*/*/
```

**Incremental ingestion** is the default behavior (scope+skip): re-running
the SAME command after dropping new requirement docs (a new release dir, a
whole new MNO, or replaced files in an existing cell) re-processes **only
the new/changed cells** — extract reuses IRs newer than their sources,
parse reuses trees whose profile fingerprint still matches. Options when
you want something narrower or stronger:

```bash
./ingest.sh <MNO> <MMMYYYY> -- --skip-taxonomy     # one cell only (targeted)
./ingest.sh -f <MNO> <MMMYYYY> -- --skip-taxonomy  # force-redo one cell
# force-redo everything: add --force to the all-cells command above
# ingest.sh options: -f force, -l nora, -e <env-file>, --fg, DRY_RUN=1;
# it runs detached with the console log landing in <build-env>/reports/
```

New cells also need their `profiles.json` binding added before the run.

### Phase 2 — derive per-MNO prompts (Cline)

**Fresh corpus:** run once per ingested MNO. Give Cline:

> Follow cline-playbooks/derive-prompts.md for MNO `<MNO>`, version v02.
> Use env_dir /home/`<you>`/nora-data/nora-builds/`<build>`.

`<MNO>` must match the cell naming exactly (spelling + case). Each run
writes four files to `customizations/prompts/`:
`{doc,query,relevance}_requirement_<MNO>_v02.txt` + `corpus_overview_<MNO>_v02.txt`.

**Incremental corpus additions** — prompts are per-MNO with releases pooled
(releases of one MNO are near-duplicates), so:

| What arrived in Phase 1        | Cline action                                    |
|--------------------------------|-------------------------------------------------|
| New MNO                        | Run the playbook for that MNO (required — its cells otherwise fall back to the config prompt, loudly) |
| New release of an existing MNO | Nothing required. Re-derive with a bumped version (`v03`) only on material corpus change — the resolvers pick the highest version automatically |
| Docs replaced within a cell    | Same as new release: re-derive only if material |

A re-derived overview auto-busts the taxonomy cache in Phase 4 (fingerprint
includes overview hashes) — no `--force` bookkeeping.

Spot-check before committing:
- doc prompt contains `{taxonomy_block}`, `{requirements}`, `{max_n}` and
  **no** `{doc_text}` (the batched path auto-activates on this shape);
- overview file is plain prose, no placeholders.

Then commit — **work PC / internal remote only** (D-062 trust boundary; see
`customizations/prompts/README.md`):

```bash
git add customizations/prompts/ && git commit -m "prompts: per-MNO v02 set (<build>)" && git push
```

(The commit is the version-control record; Phase 3 publishes the files to
the runtime.)

### Phase 3 — publish prompts + set the knobs

The prompt resolvers read their directories at RUN time, so there are two
ways to deliver prompt files — pick one:

**A. Mounted (recommended — per-build-env, no rebuild).** Copy the prompt
set into the build env dir, which is already mounted at `/data/env`:

```bash
cp customizations/prompts/*_<MNO>_v*.txt /home/<you>/nora-data/nora-builds/<build>/prompts/
```

Each build env carries its own prompt set — different envs can run
different prompt recipes from the SAME images, and a prompt update is a
file copy + re-run. An env whose `prompts/` dir is empty (or whose
`*_PROMPT_DIR` vars are unset) runs the generic fallback prompts — that
too is a recipe, stated explicitly.

**B. Baked (legacy).** The files also ride inside the images
(`COPY customizations/`); rebuild after committing them:

```bash
docker compose --env-file .env.builds --profile ingest build nora-pipeline sira-batch
```

Set once in the per-service env files (paths shown for option A; for
option B use `/app/customizations/prompts` instead):

```
# .env.nora-pipeline
NORA_TAXONOMY_OVERVIEW_DIR=/data/env/prompts

# .env.sira-batch
NORA_SIRA_DOC_PROMPT_DIR=/data/env/prompts
NORA_SIRA_TAXONOMY_DIR=/data/env/out/taxonomy
# NORA_SIRA_BATCH_* only if deviating from defaults (cap 50k / ctx 64k /
# 90 resp-tokens/req / 3.5 chars-per-token / 2 retries / 2 concurrent).
# For reasoning models whose untagged thinking leaks into responses:
# NORA_SIRA_BATCH_REASONING_SENTINEL=1 (===FINAL_ANSWER=== instruction +
# marker-aware parsing; raise RESP_TOKENS_PER_REQ too — thinking consumes
# response budget)
# NORA_SIRA_BATCH_MAX_REQS=1 forces single-req mode: same batched prompt
# (taxonomy block included) + retry/trace machinery, one LLM call per req.
# Values >1 hard-cap reqs/batch; never loosens the budget-derived cap.
```

### Phase 4 — taxonomy

Taxonomy derives WITH corpus context. It is a **global** stage — one run
derives the union feature set over every cell's trees; there is no per-cell
taxonomy step. When serving SIRA lanes only, taxonomy is the sole remaining
NORA stage: resolve/standards/graph/vectorstore feed the NORA-native
retrieval lanes and can be skipped entirely.

Multi-release + resilience semantics:

- **The unit of extraction is a plan, not a file.** A doc whose chapters
  are each a plan (single-doc MNOs) is split into per-plan subtrees before
  extraction: one focused LLM call per plan (the prompt outline is capped
  at 200 lines — unsplit, everything past the cap was invisible), one
  `<plan_id>_features.json` per plan (what SIRA's taxonomy-block lookup
  needs). Any old empty-prefix `_features.json` is cleaned up
  automatically.
- **Newest release wins — per plan.** When a plan appears in several
  releases of one MNO, only the newest release's copy is extracted —
  MMMYYYY release dir names are parsed to `YYYYMM` (`Jul2026` → `202607`)
  so comparison is chronological, not alphabetical. This applies to
  chapter-plans inside multi-plan docs too. Older copies count as
  `superseded` in the stage stats and cost no LLM calls. Expect
  `*_features.json` count ≈ distinct plans across all MNOs (chapter-plans
  included), not plans × releases.
- **Sporadic LLM/server errors don't kill the run.** A failed doc is
  recorded in `out/taxonomy/extraction_state.json` and the run continues
  (stage ends `WARN` with `TAX-W004: N of M docs failed`).
- **Re-running the same command IS the retry.** A re-run skips docs already
  extracted (unchanged tree + unchanged overviews) and re-attempts only
  failed/new ones. No `--force` needed after a degraded or killed run — a
  run with failures never stamps the cache fingerprint. `--force` remains
  the full-redo hammer.

```bash
TS=$(date +%Y%m%d_%H%M%S)
docker compose --env-file .env.builds --profile ingest run -d --rm -T nora-pipeline \
  sh -c "mkdir -p /data/env/reports && exec python -m core.src.pipeline.run_cli \
    --env-dir /data/env --start taxonomy --end taxonomy --no-skip-taxonomy \
    > /data/env/reports/stage-taxonomy-$TS.log 2>&1"
```

`--no-skip-taxonomy` is load-bearing: `config/llm.json` has shipped with
`skip_taxonomy: true` — the explicit flag wins over any config/env skip,
so the run can't silently no-op.

Verify:

```bash
E=/home/<you>/nora-data/nora-builds/<build>
grep "Corpus context" $E/reports/stage-taxonomy-*.log | head  # one per doc, right MNO's overview
grep "TAX-W003" $E/reports/stage-taxonomy-*.log               # must be EMPTY
grep "TAX-W004\|TAX-E001" $E/reports/stage-taxonomy-*.log     # failures -> re-run same command to retry
ls $E/out/taxonomy/*_features.json | wc -l                    # ≈ DISTINCT plan count (newest release only)
# per-doc ledger: ok/failed + error per doc
python3 -c "import json; d=json.load(open('$E/out/taxonomy/extraction_state.json'))['docs']; \
  print({s: sum(1 for v in d.values() if v['status']==s) for s in ('ok','failed')})"
```

Repeat run + verify until the `failed` count is 0 (sporadic server errors
are expected; each re-run only retries what failed).

If the same units stay `failed` across re-runs, replay their exact prompts
and capture the raw LLM responses (refusal prose, truncation, endpoint
errors) with the taxonomy debug CLI — outputs under
`<env_dir>/reports/tax_debug/` contain corpus content, so review locally
and redact before sharing:

```bash
python -m core.src.taxonomy.tax_debug --env-dir /data/env --dry-run  # prompts only
python -m core.src.taxonomy.tax_debug --env-dir /data/env            # + raw responses
```

Plans left without a taxonomy are fail-soft downstream: Phase 5 enriches
them without a `{taxonomy_block}` (logged `No taxonomy for plan` once per
plan) — quality cost only, never a failure.

**Variant — full NORA retrieval stack wanted** (nora-web native query
lanes): run the whole `nora` lane per cell instead — `./ingest.sh -l nora
<MNO> <MMMYYYY>` — which adds standards/graph/vectorstore/eval. Taxonomy
runs on the first cell's lane and hits cache on the rest. The eval stage
no-ops/warns without user eval questions in a fresh env — expected. This
can also be done LATER: parse output persists, one `--lane nora` run
backfills the stack, and taxonomy hits its cache (fingerprint unchanged).

### Phase 5 — SIRA batched enrichment (per cell, smallest first)

Run the SMALLEST cell first and inspect before fanning out. (Needs the
ENRICH LLM routing in `.env.sira-batch` — URL WITH `/v1` + model name;
unset vars make SIRA try to launch a local sglang server, which the
trimmed images deliberately do not carry.)

```bash
docker compose --env-file .env.builds --profile ingest run --rm -T sira-batch \
  python -m sandbox.sira_lane --env-dir /data/env --db-root /data/db \
  --run-name <run-name> --only <MNO>__<MMMYYYY> --wipe-stale-index \
  2>&1 | tee ~/sira-<run-name>-pilot.log
```

`--wipe-stale-index` is the incremental-safe wipe (keeps the `runs/`
enrichment cache); `--wipe-all-derived` is the full-rebuild hammer that
re-enriches everything.

Coarse corpus rows (`doc:`/`section:`-prefixed rollups) are SKIPPED by
default — only per-req rows hit the LLM. Skipped rows are traced as
`skipped_doc_chunk` / `skipped_section_chunk` (benign; counted as
covered by verify). Opt in with `--enrich-doc-chunks` /
`--enrich-section-chunks` (same flags on `sira_lane` and `sira_multi`;
env form `NORA_SIRA_BATCH_ENRICH_DOC_CHUNKS=1` etc.). To enrich them
AFTER a default-skip build, flip the flags AND add
`--include-skipped` to the retry so the skipped rows are evicted back
into scope (see the retry section).

Inspect the pilot cell before continuing (host
paths — the sira-build dir is directly visible):

```bash
D=/home/<you>/nora-data/sira-builds/<build>/<MNO>__<MMMYYYY>/runs/doc-enrich/<run-name>
grep -c '{taxonomy_block}' $D/prompt.txt   # 1 -> per-MNO BATCHED template resolved
                                           # (0 -> fell back to generic v01: check
                                           #  NORA_SIRA_DOC_PROMPT_DIR + that the dir
                                           #  holds the files — mounted <env>/prompts/
                                           #  or rebaked image, per Phase 3)
# batch shapes: n_reqs, prompt_tokens_est, closed_by (prompt|response|end),
# oversized, status per batch:
head -5 $D/batches*.jsonl; wc -l $D/batches*.jsonl
# failure histogram (statuses: batch_error / missing_in_batch_response / ...):
python3 -c "import json,collections,glob; print(dict(collections.Counter( \
  json.loads(l).get('status','?') for f in glob.glob('$D/trace.failed*.jsonl') \
  for l in open(f))))"
```

Healthy pilot: batches mostly `closed_by=response` (~150 reqs/batch at
defaults), few/no `missing_in_batch_response` after retries, phrase quality
spot-check via `sira_debug phrases --filter <SUBDOMAIN>` shows
subdomain-appropriate vocab. Then run ALL remaining cells in one shot —
drop `--only`, keep the SAME `--run-name` (the pilot cell resumes by
doc_id and skips everything already enriched):

```bash
docker compose --env-file .env.builds --profile ingest run --rm -T sira-batch \
  python -m sandbox.sira_lane --env-dir /data/env --db-root /data/db \
  --run-name <run-name> --wipe-stale-index
```

### Verifying an enrichment build

Four tools, one flow: **verify** (is anything wrong?) → **--failed**
(what failed, where?) → **--trace** (why this one?) → retry (next
section). The verify layer is READ-ONLY and paste-safe by design —
counts, statuses, and verdicts only, never req ids or corpus content —
so its output can be shared verbatim. The triage layer prints real ids
and is local-only.

**1. Sweep every cell** (the standard post-build check):

    docker compose --env-file .env.builds --profile ingest run --rm -T sira-batch \
      python -m sandbox.sira_multi --verify \
      --db-root /data/db --run-name <run-name>
    # restrict: --only <MNO>__<REL>[,...]

Per cell: batch status / closed_by / round histograms (with
single-req-mode detection), kept/failed reconciliation (kept AND failed
split by row type — req vs coarse doc/section chunks — plus duplicates,
kept∩failed, a sanitized top-errors histogram), coverage vs the corpus
(uncovered also split by type), and the kept↔enrichment resume
invariant (all zeros on a healthy run). Verdict per cell + a summary line. Exit 1 when
any cell FAILs (structural breaks); `--strict` also fails on WARN
(quality signals: parse_error batches, unanswered reqs, non-benign
failures).

Batch stats cover the LATEST invocation only — batches files are
append-only across every resume/retry of a run name, so cumulative
stats would carry old-era parse errors forever. A `history:` line shows
how many earlier invocations/batches were excluded; a clean retry pass
therefore reports PASS on its own merits. `trace.failed` numbers are
always current (retry evicts before re-running).

**2. One cell / A-B equivalence**:

    docker compose --env-file .env.builds --profile ingest run --rm -T sira-batch \
      python -m sandbox.sira_incremental verify-run \
      --dataset /data/db/<MNO>__<REL> --run-name <run-name>

Add `--compare-run <other-run-name>` to diff per-doc phrase sets between
two runs of the same cell (e.g. batch mode vs `--max-reqs 1`) as Jaccard
agreement — the strongest check that batching introduces no cross-req
contamination.

**3. Lane gate** — append `--verify` to any `sira_lane` command to run
the sweep automatically after the build; non-zero exit when a cell FAILs.

**4. Triage WHICH reqs failed** — LOCAL-ONLY (real req ids / plan
codes; redact before sharing):

    docker compose --env-file .env.builds --profile ingest run --rm -T sira-batch \
      python -m sandbox.sira_enrich_inspect --failed \
      --db-root /data/db --run <run-name>
    # --cell <MNO>__<REL> for one cell; --limit 20 for more ids per group;
    # then drill into one req: `sira_enrich_inspect <req_id> --trace`

Failed reqs per cell, grouped status → plan, biggest problem first.

Reading the failure statuses: timeout / connection / HTTP statuses are
transient endpoint trouble — retry fixes them. `all_filtered` rows are NOT
errors (the enrichment filter kept nothing for that doc); retrying reproduces
them identically, so `retry-failed` excludes them by default — pass
`--include-all-filtered` only after changing the prompt or the LLM.
`skipped_doc_chunk` / `skipped_section_chunk` rows record build policy
(coarse chunks excluded by default), not failures — `retry-failed` leaves
them alone unless you pass `--include-skipped` together with the
`--enrich-*-chunks` build flags. `llm_refused` rows mean the endpoint
PERMANENTLY refuses that input (deterministic non-answer, marker-detected —
see the fallback section below); they too stay put unless `--include-refused`.

### Permanent refusals — fallback LLM

Some endpoints deterministically refuse certain inputs: instead of an
answer they return a fixed notice, identical on every retry — burning
retry rounds on them is pure waste. The refusal detector + fallback
model handles these:

- **Detect**: set `NORA_LLM_REFUSAL_MARKERS` (in `.env.sira-batch` AND
  `.env.sira-query`; `||`-separated prefixes). A response that starts
  with a marker and carries no JSON payload is a permanent refusal.
  Marker values are deployment-specific — env files only, never commit
  them.
- **Fallback (batch)**: set `NORA_SIRA_ENRICH_FALLBACK_LLM_URL` (+`_MODEL`,
  URL WITH `/v1`) in `.env.sira-batch` — typically a local model. A
  refused call is re-sent once to the fallback (same prompt/budget); the
  batch row is tagged `"llm": "fallback"` and verify shows a
  `fallback-answered N` count. Markers set but no fallback → reqs fail
  fast as `llm_refused` (no retry-round burn).
- **Fallback (query service)**: set `NORA_SIRA_QUERY_FALLBACK_LLM_URL`
  (+`_MODEL`, `_API_KEY`; URL WITHOUT `/v1`) in `.env.sira-query` —
  covers query enrichment and chat rerank; `/healthz` reports
  `refusal_fallback {configured, used}`. Recreate the serve stack after
  editing.
- **Retry existing `llm_refused` rows** after configuring the fallback
  (rebake `sira-batch` first — the patch layer changed):

      docker compose --env-file .env.builds --profile ingest run --rm -T sira-batch \
        python -m sandbox.sira_lane --env-dir /data/env --db-root /data/db \
        --run-name <run-name> --wipe-stale-index \
        --retry-failed --include-refused --max-reqs 1 --verify

Taxonomy-lane fallback is deferred (TBD) — the same detector will plug
into the core LLM provider path later. If the
same docs fail repeatedly with the same status, inspect their trace lines —
oversized docs vs endpoint token limits are the usual culprits.

### Retrying failed enrichments

Doc-enrichment resumes by doc_id inside the pinned run dir
(`<cell>/runs/doc-enrich/<run-name>/`), so failures — and interruptions
(power loss, killed container) — never mean starting over. Re-running the
same command with the SAME `--run-name` resumes; docs listed in the run
dir's trace files (kept AND failed) are skipped. When verification
(previous section) shows failed docs worth retrying:

    # evict genuine failures + re-run + re-verify in ONE command —
    # SAME --run-name. Resume skips every kept doc; only the evicted
    # ones hit the LLM:
    docker compose --env-file .env.builds --profile ingest run --rm -T sira-batch \
      python -m sandbox.sira_lane --env-dir /data/env --db-root /data/db \
      --run-name <run-name> --wipe-stale-index --retry-failed --max-reqs 1 --verify
    # (per-cell standalone form: `python -m sandbox.sira_incremental
    #  retry-failed --dataset /data/db/<MNO>__<REL> --run-name <run-name>
    #  --stage doc-enrich`, then re-run the lane exactly as before)

`--max-reqs 1` on the retry pass runs each evicted req as its own LLM call
(single-req mode) — same batched prompt (taxonomy block included) and
retry/trace machinery, but a req that failed inside a large batch (endpoint
truncation, one bad neighbor poisoning the parse) gets a clean solo shot.
It exports `NORA_SIRA_BATCH_MAX_REQS` to the build; values >1 cap
reqs/batch, and the cap never loosens the response-budget-derived limit.
Skip the flag to retry with normal batch packing.

To enrich the coarse `doc:`/`section:` chunks a default build skipped,
combine the opt-ins with the skipped-row eviction in one lane pass:
`--retry-failed --include-skipped --enrich-doc-chunks
--enrich-section-chunks`. Without `--include-skipped` the skipped rows
stay in the resume trace and the build never revisits them; without the
`--enrich-*-chunks` flags the retry just re-skips them.

After a hard interruption (power loss, killed container), add `--heal-torn`
to the sira_lane resume command — before the lane runs, it repairs each
cell's run dir: torn (half-written) trailing JSONL lines are dropped, a
kept row whose enrichment row was lost is evicted so the doc re-enriches,
and orphan enrichment rows are dropped so phrases don't merge twice.
`--heal-torn` and `--retry-failed` compose (heal runs first) — one command
covers "power loss mid-run AND old failures to retry". Standalone form,
per cell: `python -m sandbox.sira_incremental heal-torn --dataset
/data/db/<MNO>__<REL> --run-name <run-name>`.

Both flags are for INCREMENTAL runs (no wipe, or `--wipe-stale-index`,
which keeps the `runs/` cache). Under `--wipe-all-derived` the adapter
deletes `runs/` before enriching, so the lane skips them with a note —
a full rebuild re-enriches everything anyway.

(Full scenario matrix: `sandbox/README.md` §2 — same commands, containerized.)

### Phase 6 — promote + serve

```bash
# SIRA-only path: promote the sira build alone (out/ has no vectorstore/
# graph to snapshot; taxonomy already did its job feeding enrichment —
# serving doesn't read it). NORA-native query lanes will have no data on
# this label. Add --nora-build after the Phase-4 full-stack variant.
./promote.sh --serve-root /home/<you>/nora-data/serve --label <YYYY-MM-DD-a> \
    --sira-build /home/<you>/nora-data/sira-builds/<build>

# stack .env: NORA_ENV_DIR/SIRA_DB_ROOT -> serve/<label>/{nora,sira}, then
# RECREATE (a plain `restart` does NOT re-read .env or the env_files —
# recreate does):
docker compose --env-file <stack .env> --profile serve up -d
curl localhost:8040/healthz          # cells loaded
# web UI: releases visible, enrichment model header correct
# (NORA_SIRA_ENRICH_MODEL_NAME override in .env.sira-query if the served
#  model name needs pinning)
```

If the batch used a pinned run name, point the query service at the same
enrichment run: `NORA_SIRA_DOC_ENRICH_RUN=<run-name>` in `.env.sira-query`,
then recreate.

First promotion note: `--nora-build` / `--sira-build` accept ANY dir in build
shape — including pre-docker bare-metal artifacts (an env_dir with
`out/{vectorstore,graph,taxonomy}`, a db_root with `<MNO>__<MMMYYYY>` cells) —
so existing deployments promote their current data as the first label.

Rollback levers, most→least granular:
- serve data: repoint stack .env at the previous serve label, `up -d`;
- enrichment only: previous `--run-name` is still on disk (runs are pinned);
- images: `./pull.sh` the Phase-0 snapshot tag, or retag the local
  `:pre-<cycle>` aliases back to `:dev`.

Known limit: per-MNO **query/relevance** prompts are generated but the
query-time service still loads a single pair — per-cell selection there is
a deferred change (strand journal 2026-07-24).

## Variant lineages — comparing ideas, not code

To evaluate a pipeline idea (per-MNO derived prompts, taxonomy-context
enrichment, a future nora3 hypothesis), run it as a **variant lineage**:
one code line and one image set for everything, with each variant defined
entirely by its *recipe* — prompt set + knobs + wiring env — and owning a
complete artifact lineage built from the **shared raw corpus**.

```
input/<MNO>/<release>/            SHARED raw corpus — the controlled variable
nora-builds/<variant>/            per-variant build env (artifacts + prompts/ + RECIPE.md)
sira-builds/<variant>/            per-variant sira db_root
serve/<variant>-<label>/          immutable promoted labels, per (variant, release)
docker/.env.builds.<variant>     ingest wiring: where artifacts land + which knobs
docker/.env.<variant>            serve wiring: stack -> its labels + runtime env files
```

Rules that keep the comparison honest:

1. **An idea lives in config/data, never in a code branch.** The resolvers
   are built for this: per-MNO prompts are files resolved at run time
   (generic fallback when absent), taxonomy blocks activate only when
   their dir is wired, fallbacks only when their env vars are set. A new
   idea that needs code ships dormant behind a knob defaulting to the old
   behavior — so every variant, old and new, runs the SAME latest images
   and the eval isolates the idea, not the code drift.
2. **Never share build dirs between variants.** A new release is ingested
   once per active variant — same corpus, that variant's wiring:
   `./ingest.sh -e .env.builds.<variant> <MNO> <MMMYYYY>` (+ its sira
   lane), then promote into that variant's own label. N× pipeline cost is
   the price of a clean comparison.
3. **Change one variable per new variant.** Seed `nora<N+1>` by copying
   the previous recipe (wiring envs + prompts/) and altering only the
   idea under test. Record it in `nora-builds/<variant>/RECIPE.md` — one
   short paragraph: what this variant embodies ("generic prompts, no
   taxonomy context" / "Cline per-MNO prompts v3 + taxonomy blocks").
   `MANIFEST.json` in each label ties artifacts to source builds + git
   sha; RECIPE.md ties them to the hypothesis.
4. **Evaluation state is pooled; everything else is per-variant.**
   `GOLDEN_DIR` (same golden samples scored against every stack) and
   `FEEDBACK_DIR` (attributable A/B, D-120) are shared across stacks by
   design — same corpus + same samples + same code, recipe is the only
   difference.

Retiring a variant = stop its stack; its builds and labels stay on disk
as the historical record.

Each variant's stack gets its own reverse-proxy path prefix
(`NORA_WEB_ROOT_PATH=/nora1`, `/nora2`, …) — see "Serving behind a
reverse proxy (Caddy)" below.

## Two stacks, two LLMs (A/B)

The two-variant instance of the lineage pattern above (with the LLM as
the recipe delta). Same images; each stack gets its own wiring env + its
own service runtime files. Per stack: STACK_NAME, ports, its `web-state-<x>/` directory, and the
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

## Serving behind a reverse proxy (Caddy)

nora-web can be served under a path prefix behind a reverse proxy. Two
pieces, one per side:

1. **Proxy side** — the proxy must PASS THE PREFIX THROUGH unchanged
   (do NOT strip it). Starlette's `root_path` contract expects the ASGI
   path to include the prefix — routing strips it internally, and the
   mounted `/static` files resolve against the stripped remainder. A
   prefix-stripping proxy (`handle_path` / `uri strip_prefix`) breaks
   exactly the mounts: every `/static/...` asset 404s while regular
   routes appear to work. Use a plain `handle` with a path matcher;
   `flush_interval -1` keeps the SSE surfaces live (job log streaming,
   `/api/test/ask-stream`):

   ```caddyfile
   example.test {
       redir /nora-v2 /nora-v2/ 308
       @nora-v2 path /nora-v2/*
       handle @nora-v2 {
           reverse_proxy 127.0.0.1:8001 {
               flush_interval -1
           }
       }
       redir /nora-v1 /nora-v1/ 308
       @nora-v1 path /nora-v1/*
       handle @nora-v1 {
           reverse_proxy 127.0.0.1:8000 {
               flush_interval -1
           }
       }
   }
   ```

   (For a proxy reached by IP over plain HTTP, use a port-only site
   label — `:8080 { ... }` — which matches any Host and disables
   automatic HTTPS.)

2. **App side** — tell each stack its prefix so every generated link,
   redirect, form target, and SSE URL carries it. In that stack's
   `.env.nora-web.<x>`:

   ```bash
   NORA_WEB_ROOT_PATH=/nora-v2
   ```

   The env var overrides `config/web.json`'s `root_path` (the file is
   baked into the image; the prefix is per-deployment). Values are
   normalized (leading slash added, trailing slash dropped). Then
   `up -d` to recreate — env-file changes need a recreate, not a
   restart.

Sanity checks:
- `curl -sL http://127.0.0.1:8001/nora-v2/test | grep data-root-path`
  shows the prefix (the app accepts prefixed paths natively once
  `NORA_WEB_ROOT_PATH` is set — direct access works WITH the prefix
  in the URL, e.g. `http://<host>:8001/nora-v2/`);
- `curl -sI http://127.0.0.1:8001/nora-v2/static/css/style.css`
  returns 200 (statics are the piece a mis-configured stripping proxy
  breaks first);
- through the proxy, page links and the team-mode `/test` redirect stay
  under `https://<host>/nora-v2/...`.

Team mode composes with this: gated experts land on
`<prefix>/test`, and `/admin-unlock?token=...` redirects stay inside the
prefix.

## Enrichment review (domain experts)

`/enrichment-review` on nora-web lets domain experts browse SIRA's
per-requirement enrichment keywords (MNO → Release → Plan), delete/add
words (with campaign labels + reasons), and hit **Apply** — which reloads
the affected cell on the serving sira-query so they can immediately
re-test queries on the Test page. Corrections live as a delta overlay
under `CORRECTIONS_DIR` (never inside builds or serve labels; they survive
re-enrichment and re-promotion). With both a/b stacks live, set
`NORA_SIRA_QUERY_URLS` in `.env.nora-web.<x>` so one Apply reloads both
(see env.nora-web.example). Team-mode gate admits the page.

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

    # any other internal host: §Bring up from a published release
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
