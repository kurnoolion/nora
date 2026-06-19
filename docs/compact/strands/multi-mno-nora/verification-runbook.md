# Multi-MNO pipeline verification runbook

End-to-end verification of the per-cell multi-MNO / multi-release ingestion
implemented in this strand (D-DRAFT-6..10, 12). Run on the **work PC** (real
corpora + mappings live there). This runbook **is** the landing-gate evidence:
full ingestion + incremental adds + the negative/no-leak cases must all pass on a
real two-MNO corpus before `/land-strand multi-mno-nora`.

> Redaction: `VZW-OA` for the public Verizon Open-Access corpus (OA context),
> `<MNO-B>` for the second MNO, `MMMYYYY` releases (e.g. `Feb2026`), `<MNO0>` /
> `<PREFIX>` placeholders. `$ENV` = NORA env dir. Real names/plan codes stay on
> the work PC.

What this exercises:
- **D-DRAFT-6** — per-cell layout `out/<stage>/<mno>/<rel>/`; MMMYYYY enforcement.
- **D-DRAFT-7** — per-cell profile binding (`profiles.json`); fail-loud coverage.
- **D-DRAFT-8** — incremental skip + `--mno`/`--release`/`--force`.
- **D-DRAFT-9** — global taxonomy fingerprint cache.
- **D-DRAFT-10** — cross-MNO no-leak in resolve.
- **D-DRAFT-12** — SIRA adapter reads the nested layout.

---

## 0. Setup — a two-cell env

```
$ENV/
  input/
    VZW-OA/Feb2026/      <- the 5 OA PDFs (migrated from VZW/OA-baseline)
    <MNO-B>/<MMMYYYY>/   <- the MNO-B PDF
  profiles.json
```

- **OA migration:** place the OA PDFs at `input/VZW-OA/Feb2026/` (the old
  `input/VZW/OA-baseline/` free-form release is retired — MMMYYYY is now
  enforced). `VZW-OA` is its own MNO.
- **`$ENV/profiles.json`** — bind each cell to its profile:
  ```jsonc
  { "bindings": [
      { "mno": "VZW-OA",  "release": "*", "profile": "customizations/profiles/bs_d7a2c81f.json" },
      { "mno": "<MNO-B>", "release": "*", "profile": "customizations/profiles/bs_5114ac92.json" }
    ],
    "default": null }
  ```
- **Mappings** present for the placeholdered profiles
  (`customizations/mappings/bs_5114ac92.json` maps `<MNO0>` → the real prefix;
  `bs_d7a2c81f.json` likewise). Work-PC only.
- `git pull origin main` includes through `f28bfe6` (D-DRAFT-6..10/12 + close-session).

---

## A. Full ingestion (both cells, one run)

```bash
python -m core.src.pipeline.run_cli --env-dir "$ENV" --start extract --end graph
#   add --skip-taxonomy / --skip-graph / --rag-only to match how OA was built,
#   or run the full chain for a complete check.
```

Per-stage verification:

```bash
# 1. per-cell directories exist for BOTH cells
for s in extract profile parse resolve; do
  echo "== $s =="; ls -d "$ENV"/out/$s/*/*/ 2>/dev/null
done
ls "$ENV"/out/taxonomy/taxonomy.json "$ENV"/out/graph/knowledge_graph.json  # GLOBAL (flat)

# 2. profile materialized + substituted per cell (no leftover <MNO0>)
for p in "$ENV"/out/profile/*/*/profile.json; do
  echo "$p"; grep -c '<MNO0>' "$p"   # expect 0
done

# 3. parse: per-cell trees carry mno/release + stamps
for t in "$ENV"/out/parse/*/*/*_tree.json; do
  jq '{mno, release, detection_mode, build_context, fp: (.profile_fingerprint|length>0), reqs: (.requirements|length)}' "$t"
done
#   VZW-OA -> detection_mode "heading"; <MNO-B> -> "leading_id_body", build_context "path_and_content"

# 4. graph spans BOTH MNOs (the union)
jq '[.nodes[].mno // empty] | unique' "$ENV"/out/graph/knowledge_graph.json  # expect both MNOs
```

---

## B. Incremental behavior

```bash
# B1. Re-run unchanged -> everything skips
python -m core.src.pipeline.run_cli --env-dir "$ENV" --start extract --end parse
#   expect parse stats: docs=0, skipped=<N>; taxonomy (if run) source="cache"

# B2. Scope to one MNO -> only that cell reprocesses
python -m core.src.pipeline.run_cli --env-dir "$ENV" --mno "<MNO-B>" --start extract --end parse
#   only <MNO-B> trees rewritten; VZW-OA untouched (check mtimes)

# B3. Force -> reprocess in scope
python -m core.src.pipeline.run_cli --env-dir "$ENV" --mno "<MNO-B>" --force --start parse --end parse

# B4. Add a NEW cell (new release or MNO), re-run the SAME full command
#     -> only the new cell is parsed; global graph/taxonomy rebuild over the union
mkdir -p "$ENV"/input/<MNO-B>/<NEWMMMYYYY>/   # drop docs; add a binding if new MNO
python -m core.src.pipeline.run_cli --env-dir "$ENV" --start extract --end graph
```

Verify the taxonomy cache directly:

```bash
cat "$ENV"/out/taxonomy/.corpus_fingerprint   # changes only when the tree set changes
```

---

## C. Negative / correctness cases

```bash
# C1. MMMYYYY enforcement (D-DRAFT-6) — a non-MMMYYYY release dir fails loud
mkdir -p "$ENV"/input/VZW-OA/OA-baseline/ && cp <one pdf> "$ENV"/input/VZW-OA/OA-baseline/
python -m core.src.pipeline.run_cli --env-dir "$ENV" --start extract --end extract
#   expect EXT-E004 (not MMMYYYY). Remove the bad dir afterwards.

# C2. Uncovered-cell fail-loud (D-DRAFT-7) — temporarily remove a binding
#   -> profile stage fails PIP-E003 naming the uncovered <mno>/<rel>.

# C3. Cross-MNO no-leak (D-DRAFT-10) — resolve is per-cell
for x in "$ENV"/out/resolve/*/*/*_xrefs.json; do echo "$x"; done
#   confirm a <MNO-B> cross-plan ref resolves only within <MNO-B>, never to a
#   same-numbered VZW-OA requirement (spot-check a known cross-plan ref).
```

---

## D. SIRA adapter reads the nested layout (D-DRAFT-12)

```bash
python -m sandbox.adapter.nora_to_beir --env-dir "$ENV" --output "$DB" \
  --multi-cell --wipe-all-derived
#   expect one BEIR cell per (mno, release): $DB/VZW-OA__Feb2026/, $DB/<MNO-B>__<rel>/
ls -d "$DB"/*/raw/corpus.jsonl
```

(The per-req Context check for `<MNO-B>` is in `mno-b-spec.md` § Runbook.)

---

## Sign-off checklist (landing gate)

- [ ] Full run produces per-cell `extract/profile/parse/resolve` + global
      `taxonomy/graph` for **both** MNOs.
- [ ] No `<MNO0>` left in any materialized profile; req_ids substituted in trees.
- [ ] `detection_mode` / `build_context` / `profile_fingerprint` stamped correctly
      per cell.
- [ ] Graph spans both MNOs (union).
- [ ] Incremental: unchanged re-run skips; new cell builds only itself; `--mno`
      scopes; `--force` reprocesses; taxonomy hits cache when unchanged.
- [ ] MMMYYYY fail-loud (EXT-E004); uncovered-cell fail-loud (PIP-E003).
- [ ] Cross-MNO no-leak confirmed in resolve.
- [ ] SIRA adapter emits one cell per (mno, release) from the nested layout.

When all boxes are checked on the real two-MNO corpus, the strand is ready for
`/land-strand multi-mno-nora` (D-DRAFT-1..12 promote to canonical DECISIONS).
