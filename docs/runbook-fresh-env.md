# Runbook — fresh env: ingestion → prompts → taxonomy → enrichment → serving

The full cycle for a brand-new NORA env with the sira-enrichment-pe prompt
machinery: per-MNO derived prompts, overview-primed taxonomy, batched
doc-enrichment. Supersedes the plain flow in `docker/README.md §Ingest a new
release` **only in ordering** — every individual command there still applies
and is the reference for options/troubleshooting.

Why the ordering matters: the Cline prompt-derivation skill reads the
**parsed** corpus, and the taxonomy stage should consume the **derived**
corpus overviews. So: parse first, derive prompts second, taxonomy third,
enrichment fourth. The taxonomy stage's cache auto-busts when overview files
change (fingerprint includes their hash), so nothing needs `--force`.

Placeholders throughout: `<MNO>` / `<MMMYYYY>` = cell coordinates as they
appear under `requirements/`; `<you>` = your home dir; `<build>` = new build
name (e.g. `2026-08-a`).

---

## Phase 0 — pre-flight (once)

```bash
cd ~/work/nora/docker

# 0.1 snapshot current images as a rollback point (BEFORE git pull, so the
#     tag records the sha the images were built from)
for i in nora-web nora-pipeline sira-query sira-batch; do
  docker tag local/$i:dev local/$i:pre-enrichment-pe
done
TAG="images-pre-enrichment-pe-$(git rev-parse --short HEAD)"
DRY_RUN=1 ./push.sh "$TAG" local/nora-web:dev local/nora-pipeline:dev \
  local/sira-query:dev local/sira-batch:dev        # check sizes first
./push.sh "$TAG" local/nora-web:dev local/nora-pipeline:dev \
  local/sira-query:dev local/sira-batch:dev

# 0.2 pull the latest code (internal remote) and rebuild ingest images —
#     the batched-enrich + overview-context code must be in the images
#     BEFORE any lane runs
git pull
docker compose --env-file .env.builds --profile ingest build nora-pipeline sira-batch
```

```bash
# 0.3 create the new build dirs and stage the source docs
mkdir -p /home/<you>/nora-data/nora-builds/<build>
mkdir -p /home/<you>/nora-data/sira-builds/<build>
# source corpus (if not already present for these cells):
#   /home/<you>/nora-data/requirements/<MNO>/<MMMYYYY>/*.pdf|docx|xlsx

# 0.4 profile bindings — a new env dir fails loud (PIP-E003) without them:
cp /home/<you>/nora-data/nora-builds/<previous-build>/profiles.json \
   /home/<you>/nora-data/nora-builds/<build>/profiles.json
# (or author one: each <MNO>/<MMMYYYY> cell -> customizations/profiles/*.json)

# 0.5 repoint the builds wiring env at the new dirs (only these two lines):
#     .env.builds:  NORA_ENV_DIR=/home/<you>/nora-data/nora-builds/<build>
#                   SIRA_DB_ROOT=/home/<you>/nora-data/sira-builds/<build>
```

## Phase 1 — parse the corpus (ALL cells, one shot)

Run the ingestion lane with taxonomy skipped — deriving a taxonomy now would
waste an expensive LLM pass on a context-less prompt; it runs in Phase 4
with the overviews attached. Standards (LLM-free) runs here.

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

**Incremental ingestion** is the default behavior (D-DRAFT-8 scope+skip):
re-running the SAME command after dropping new requirement docs (a new
release dir, a whole new MNO, or replaced files in an existing cell)
re-processes **only the new/changed cells** — extract reuses IRs newer than
their sources, parse reuses trees whose profile fingerprint still matches.
Options when you want something narrower or stronger:

```bash
./ingest.sh <MNO> <MMMYYYY> -- --skip-taxonomy   # one cell only (targeted)
./ingest.sh -f <MNO> <MMMYYYY> -- --skip-taxonomy  # force-redo one cell
# force-redo everything: add --force to the all-cells command above
```

New cells also need their `profiles.json` binding added before the run.

## Phase 2 — derive per-MNO prompts (Cline)

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

(Any prompt commit ⇒ image rebake in Phase 3 — the files ride in the images.)

## Phase 3 — rebake images + set the knobs

The prompt files ride inside the images (`COPY customizations/`), so rebuild
after committing them:

```bash
docker compose --env-file .env.builds --profile ingest build nora-pipeline sira-batch
```

Set once in the per-service env files:

```
# .env.nora-pipeline
NORA_TAXONOMY_OVERVIEW_DIR=/app/customizations/prompts

# .env.sira-batch
NORA_SIRA_DOC_PROMPT_DIR=/app/customizations/prompts
NORA_SIRA_TAXONOMY_DIR=/data/env/out/taxonomy
# NORA_SIRA_BATCH_* only if deviating from defaults (cap 50k / ctx 64k /
# 90 resp-tokens/req / 3.5 chars-per-token / 2 retries / 2 concurrent)
```

## Phase 4 — taxonomy (SIRA-only path: this is the ONLY NORA stage left)

Taxonomy now derives WITH corpus context. It is a **global** stage — one
run derives the union feature set over every cell's trees; there is no
per-cell taxonomy step. When serving SIRA lanes only, taxonomy is the sole
remaining NORA stage: resolve/standards/graph/vectorstore feed the
NORA-native retrieval lanes and can be skipped entirely.

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
ls $E/out/taxonomy/*_features.json | wc -l                    # ≈ total plan count, all MNOs
```

**Variant — full NORA retrieval stack wanted** (nora-web native query
lanes): run the whole `nora` lane per cell instead — `./ingest.sh -l nora
<MNO> <MMMYYYY>` — which adds standards/graph/vectorstore/eval. Taxonomy
runs on the first cell's lane and hits cache on the rest. The eval stage
no-ops/warns without user eval questions in a fresh env — expected. This
can also be done LATER: parse output persists, one `--lane nora` run
backfills the stack, and taxonomy hits its cache (fingerprint unchanged).

## Phase 5 — SIRA batched enrichment (per cell, smallest first)

The batched path (per-MNO prompt + per-plan taxonomy blocks) is
unit-tested but this is its first real-LLM exposure — run the SMALLEST cell
first and inspect before fanning out.

```bash
docker compose --env-file .env.builds --profile ingest run --rm -T sira-batch \
  python -m sandbox.sira_lane --env-dir /data/env --db-root /data/db \
  --run-name enrich-pe-v1 --only <MNO>__<MMMYYYY> --wipe-stale-index
```

Inspect the pilot cell before continuing (host paths — the sira-build dir
is directly visible):

```bash
D=/home/<you>/nora-data/sira-builds/<build>/<MNO>__<MMMYYYY>/runs/doc-enrich/enrich-pe-v1
grep -c '{taxonomy_block}' $D/prompt.txt   # 1 -> per-MNO BATCHED template resolved
                                           # (0 -> fell back to generic v01: check
                                           #  NORA_SIRA_DOC_PROMPT_DIR + image rebake)
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
  --run-name enrich-pe-v1 --wipe-stale-index
```

Failed docs: `docker/README.md §Retrying failed enrichments` (same
commands, `--run-name enrich-pe-v1`).

## Phase 6 — promote + serve

```bash
# SIRA-only path: promote the sira build alone (out/ has no vectorstore/
# graph to snapshot; taxonomy already did its job feeding enrichment —
# serving doesn't read it). NORA-native query lanes will have no data on
# this label — consistent with the standing flag excluding them from
# team-eval. Add --nora-build only after the Phase-4 full-stack variant.
./promote.sh --serve-root /home/<you>/nora-data/serve --label <YYYY-MM-DD-a> \
    --sira-build /home/<you>/nora-data/sira-builds/<build>

# stack .env: NORA_ENV_DIR/SIRA_DB_ROOT -> serve/<label>/{nora,sira};
# serve images pick up any code change too:
docker compose --env-file <stack .env> --profile serve up -d --build
curl localhost:8040/healthz          # cells loaded
# web UI: releases visible, enrichment model header correct
# (NORA_SIRA_ENRICH_MODEL_NAME override in .env.sira-query if the served
#  model name needs pinning)
```

Rollback levers, most→least granular:
- serve data: repoint stack .env at the previous serve label, `up -d`;
- enrichment only: previous `--run-name` is still on disk (runs are pinned);
- images: `./pull.sh images-pre-enrichment-pe-<sha>` or retag the local
  `:pre-enrichment-pe` aliases back to `:dev`.

## Deferred / known limits

- Per-MNO **query/relevance** prompts are generated but the query-time
  service still loads a single pair — per-cell selection there is a
  deferred change (strand journal 2026-07-24).
- Both levers (prompt + taxonomy) ship in this ONE re-enrichment by design
  (combined staging, D-DRAFT-3) — quality delta is unattributed until the
  eval-loop strand establishes corrections.
