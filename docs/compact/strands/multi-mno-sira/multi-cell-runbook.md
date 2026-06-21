# SIRA multi-cell ingest + enrich runbook

End-to-end recipe to ingest a **multi-MNO / multi-release** corpus into SIRA and
run build + enrichment, then query across cells. Run on the **work PC**. This is
the SIRA-side counterpart to NORA's `verification-runbook.md`, and the landing
gate evidence for this strand.

The multi-cell **code is complete** (adapter `--multi-cell`, `sira_multi.py`
orchestrator, cell-aware `sira_query.service`); this runbook is the operational
path that `SETUP.md`'s single-dataset verify flow doesn't cover.

> Redaction: `VZW-OA` for the public OA corpus (OA context), `<MNO-B>` for the
> second MNO, `MMMYYYY` releases. `$ENV` = NORA env dir; `$DB` = SIRA db_root
> (parent of the per-cell datasets); `$CLONE` = the SIRA clone path. Real
> names/values stay on the work PC.

What this exercises (SIRA D-DRAFT-3..7; NORA D-DRAFT-12 contract):
- per-`(MNO, release)` **cell** BEIR datasets at `$DB/<mno>__<release>/raw/`;
- per-cell BM25 + doc-enrichment (statistics isolated per cell — D-DRAFT-3);
- cell-aware runtime service: scope → resolve cells → retrieve → merge → rerank.

---

## 0. Prereqs

- **One-time SIRA install** complete per `sandbox/SETUP.md` (clone, env, `bm25x`
  Rust build, configs installed, LLM endpoints configured).
- **NORA per-cell parse output exists** — `$ENV/out/parse/<mno>/<rel>/*_tree.json`
  for both MNOs (produced by the NORA pipeline; see
  `multi-mno-nora/verification-runbook.md` §A). The adapter reads this nested
  layout (NORA D-DRAFT-12).
- `git pull origin main` includes the `sira_cells` → core `release_key`
  reconciliation.
- Enrichment LLM env vars set (per-stage routing, `SETUP.md` §5.0):
  ```bash
  export NORA_SIRA_ENRICH_LLM_URL=http://<llm-host>:<port>/v1
  export NORA_SIRA_ENRICH_LLM_MODEL=<model>
  export NORA_SIRA_ENRICH_LLM_TIMEOUT=300
  ```

---

## A. Emit per-cell BEIR datasets

```bash
python -m sandbox.adapter.nora_to_beir \
  --env-dir "$ENV" --output "$DB" --multi-cell --wipe-all-derived
```

- `--multi-cell` → `--output` is the **db_root**; one dataset per cell at
  `$DB/<mno>__<release>/raw/` (double-underscore, source-case preserved —
  `VZW-OA__Feb2026`).
- `--wipe-all-derived` on the **first** ingest (clears any stale SIRA-derived
  `index/ enrichments/ runs/` per cell). Use `--wipe-stale-index` for incremental
  re-emits (keeps the `runs/` enrichment cache).

Verify:
```bash
ls -d "$DB"/*/raw/corpus.jsonl          # one per cell: VZW-OA__Feb2026, <MNO-B>__<rel>
for d in "$DB"/*/; do echo "$d  $(wc -l < "$d/raw/corpus.jsonl") rows"; done
cat "$DB"/*/raw/metadata.json | python -m json.tool | head
```

---

## B. Build + enrich every cell (batch orchestrator)

```bash
# shim running if your enrich routing uses it (SETUP §5); per-stage env routing
# needs no shim. sira_multi runs run_pipeline.py once per cell.
python -m sandbox.sira_multi \
  --db-root "$DB" --sira-clone "$CLONE" \
  --stages prepare,bm25,enrich_corpus
#   --only VZW-OA__Feb2026,<MNO-B>__<rel>   # subset
#   --dry-run                               # print per-cell commands, don't run
```

- Runs SIRA's `scripts/run_pipeline.py` per cell with `data.name=<cell>`, stages
  `prepare,bm25,enrich_corpus` (the corpus-only stages multi-cell needs;
  query-enrich/rerank/eval run live in the service).
- **Per-cell data config is auto-generated.** SIRA's `_with_dataset` re-reads
  `configs/data/<cell>.yaml` by name (it does NOT honor `data.name` as an
  override on a reused config), so `sira_multi` writes
  `$CLONE/scripts/configs/data/<cell>.yaml` from the installed `nora.yaml`
  template (only `name:` differs) before each cell. No manual config needed;
  the `nora.yaml` template must be installed (SETUP.md §4).
- **Incremental:** enrichment cost scales with *changed* docs, not cell count —
  a later release reuses the prior release's enrichment via content-hash resume.

Verify each cell built its index + enrichments:
```bash
for d in "$DB"/*/; do
  echo "== $(basename "$d") =="
  ls "$d"/index/ "$d"/enrichments/ 2>/dev/null | head
done
```

---

## C. Launch the cell-aware service + verify cell loading

```bash
# Terminal: shim (if used) on 8030, then the SIRA query service:
uvicorn sandbox.sira_query.service:app --port 8040
```

On startup the service enumerates cells under `$DB` and loads each cell's BM25
index + doc enrichments (D-DRAFT-7 cell-dict). Check it loaded:
```bash
curl -s http://127.0.0.1:8040/healthz | python3 -m json.tool
#   ok: true, corpus_size: <NN>, query_prompt_loaded: true,
#   n_req_rows / n_doc_rows / n_section_rows > 0
```
Confirm in the **startup logs** that BOTH cells loaded (one BM25Index per cell).

> Memory note (deferred, journal flag): the service loads ALL cells eagerly, so
> RAM scales with total cells. Fine at verification scale (a few cells).
> Lazy-load + LRU-evict is deferred until after this verification — not a blocker.

---

## D. Cross-cell query checks

Requests are `{query, top_k}`; scope (MNOs/releases) is extracted from the query
text, resolved to cells, then retrieve→merge→rerank across the target cells.

```bash
# Single-MNO query → resolves to one cell
curl -s -X POST http://127.0.0.1:8040/sira-query \
  -H 'content-type: application/json' \
  -d '{"query": "<a VZW-OA-specific question>", "top_k": 10}' | python3 -m json.tool
#   results carry VZW-OA provenance; scope resolved to the VZW-OA cell only.

# Cross-MNO query → resolves to both cells, fused
curl -s -X POST http://127.0.0.1:8040/sira-query \
  -H 'content-type: application/json' \
  -d '{"query": "<a question naming BOTH MNOs>", "top_k": 10}' | python3 -m json.tool
#   results span BOTH MNOs; each hit tagged with its (mno, release) cell
#   (composite identity, SIRA D-DRAFT-4); unresolved-scope list surfaced.
```

Checks:
- **Single-MNO → one cell:** every result's cell provenance is that MNO.
- **Cross-MNO → fusion:** results include **both** MNOs' cells; same `req_id`
  across releases stays distinct (composite `(mno, release, doc_id)`).
- **Latest-release default:** an MNO-only query with no release resolves to that
  MNO's newest cell (max MMMYYYY order key).

---

## E. NORA Test-page "SIRA Retrieval" tab (combined web eval)

With the service on 8040, NORA's `/test` page proxies per-query SIRA retrieval
(SETUP §E). This is where the **combined NORA + SIRA web eval** happens — same
corpus, both retrieval stacks side by side.

---

## Sign-off checklist (landing gate)

- [ ] Adapter emits one BEIR cell per `(mno, release)` from the nested
      `out/parse/<mno>/<rel>/` layout.
- [ ] `sira_multi` builds BM25 + doc-enrichment for **every** cell.
- [ ] Service loads all cells at startup (one index per cell); `/healthz` ok.
- [ ] Single-MNO query resolves to one cell; cross-MNO query fuses across both
      with per-cell provenance.
- [ ] `sira_cells` sources MMMYYYY from core `release_key` (D-DRAFT-12 amendment
      realized) — no behavior change vs the old duplicate.
- [ ] Combined web eval (NORA + SIRA tabs) over the shared corpus looks sane.

When green on the real multi-MNO corpus, this strand is ready for
`/land-strand multi-mno-sira` — and at land time, **update D-DRAFT-12** to
reference the core `release_key` util (the placement amendment from
`multi-mno-nora`).

---

## Related

- `multi-mno-nora/verification-runbook.md` — the NORA-side ingestion/query
  verification (produces the `out/parse` this runbook consumes).
- `SETUP.md` — one-time install + the legacy single-dataset verify flow.
- D-DRAFT-3 (per-cell index), D-DRAFT-4 (composite identity), D-DRAFT-6 (per-cell
  BEIR + reused config), D-DRAFT-7 (cell-loop orchestration + service cell-dict),
  D-DRAFT-12 (MMMYYYY validation — amended to the core util).
