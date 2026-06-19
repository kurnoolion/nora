# Multi-MNO ingestion — design note

Design for the strand goal: ingest **multiple MNOs' requirements** through the
NORA pipeline — both **full** ingestion (all MNOs/releases placed under `input/`
in one run) and **incremental** ingestion (a new MNO or release dropped in *after*
the initial run, ingested without redoing what's already done).

Captures the design behind **D-DRAFT-6..12** (see `decisions-draft.md`). Mirrors
the `(MNO, release)` **cell** model the `multi-mno-sira` strand established
(SIRA D-DRAFT-3..7).

> Redaction: `VZW-OA` for the public Verizon Open-Access corpus (OA context — the
> allowed Verizon naming), `<mno-b>` / `<mno-c>` for other MNOs, `MMMYYYY` (e.g.
> `Feb2026`) for releases, `<PREFIX>-<PLAN>-<NUM>` for req ids. Real proprietary
> names/values stay on the work PC.

---

## 1. The cell — the unit of everything

A **cell** = one `(MNO, release)` pair (e.g. `(VZW-OA, Feb2026)`,
`(<mno-b>, Mar2025)`). It is NORA's unit of layout, partitioning, profile binding,
resolution scope, retrieval index, ordering, and provenance — the same unit SIRA
uses (D-DRAFT-3), so the two systems share one vocabulary.

The cell key comes from the **input directory convention**
`input/<MNO>/<MMMYYYY>/` — the directory name is both the release **label** and
the **sort key** (`Feb2026 → 2026-02`; "latest release" = max key). This is
SIRA D-DRAFT-5, now adopted by the NORA pipeline.

---

## 2. Stage classification — per-cell vs global

| Layout | Stages | Why |
|---|---|---|
| **Per-cell** `out/<stage>/<mno>/<rel>/` | extract, profile, parse, resolve, **vectorstore** | output is document- or retrieval-scoped to one cell |
| **Global** `out/<stage>/` | standards, **taxonomy**, graph, eval | shared across cells: the KG layer + its shared inputs + cross-cell eval |

```
<env_dir>/
  input/<mno>/<MMMYYYY>/<doc>            # source, per cell
  profiles.json                         # binding manifest (D-DRAFT-7)
  out/
    extract/<mno>/<rel>/*_ir.json        # per cell
    profile/<mno>/<rel>/profile.json     # per cell (resolved+substituted)
    parse/<mno>/<rel>/*_tree.json        # per cell
    resolve/<mno>/<rel>/*                # per cell  -> structural mno-scoping
    vectorstore/<mno>/<rel>/             # per cell  -> own collection + BM25
    standards/                           # GLOBAL (MNO-agnostic 3GPP specs)
    taxonomy/taxonomy.json               # GLOBAL (one union feature set)
    graph/knowledge_graph.json           # GLOBAL (all cells, cross-cell edges)
    eval/                                # GLOBAL (cross-MNO Q&A)
```

---

## 3. Release convention & MMMYYYY validation (D-DRAFT-6)

MMMYYYY is **universal** — every corpus is a `(MNO, MMMYYYY)` cell, with **no**
free-form exception. Validation is **unconditional, fail-loud at ingest**, via a
shared **core** util `release_key(name) -> (label, order_key)` (raises on
non-MMMYYYY) that `infer_metadata_from_path` calls.

**Supersedes SIRA D-DRAFT-12.** SIRA kept MMMYYYY validation *sandbox-side*
solely to protect the legacy free-form `OA-baseline` release in core. By
**promoting Verizon OA to its own cell** `input/VZW-OA/Feb2026/`, that holdout is
gone — so the convention is universal, validation can be unconditional in core,
and the logic belongs in a shared core util (module boundary intact: sandbox →
core; SIRA's `sira_preflight` calls the same util). D-DRAFT-12's *intent*
(fail-loud early, no boundary inversion) stands; only its "lives sandbox-side"
placement is amended.

**One-time migration (work PC):** `input/VZW/OA-baseline/` →
`input/VZW-OA/Feb2026/`, re-extract → re-ingest. Cell key
`(VZW, OA-baseline)` → `(VZW-OA, 2026-02)`; req_ids unchanged (eval + integration
ground-truth hold); mno/release-keyed chunks + graph nodes re-key (graph +
vectorstore rebuild). `VZW-OA` also cleanly separates the public OA corpus from a
future *proprietary* VZW corpus (its own MNO).

---

## 4. Profile binding (D-DRAFT-7)

Each cell needs its own profile (`VZW-OA` → `bs_d7a2c81f`; `<mno-b>` →
`bs_5114ac92`). A manifest `<env_dir>/profiles.json` maps `(mno, release) →
profile`:

```jsonc
{
  "bindings": [
    { "mno": "VZW-OA",  "release": "*", "profile": "customizations/profiles/bs_d7a2c81f.json" },
    { "mno": "<mno-b>", "release": "*", "profile": "customizations/profiles/bs_5114ac92.json" }
  ],
  "default": null
}
```

The **profile stage** resolves bindings and **materializes each cell's resolved +
substituted profile** to `out/profile/<mno>/<rel>/profile.json`; **parse** reads
each cell's profile from its own directory. Precedence per cell: `--profile`
override → exact `(mno, release)` → `(mno, "*")` → `default` → fail-loud. A bare
`--profile` synthesizes a one-cell wildcard binding (single-MNO back-compat).

---

## 5. The two flows — one command (D-DRAFT-8)

Per-cell stages (extract/profile/parse/resolve/vectorstore) are **idempotent +
scopable**: skip a cell whose outputs are present and inputs unchanged (parse
stamps `profile_fingerprint` so a profile/mapping edit invalidates exactly that
cell); `--mno` / `--release` scope to specific cells; `--force` reprocesses.
**Global stages (taxonomy/graph/eval) always rebuild over all cells.**

**Full ingestion** (all cells in `input/`, all bound in `profiles.json`):
```bash
run_cli --env-dir $ENV --start extract --end graph
```

**Incremental** (drop `<mno-c>` or a new release later):
```bash
# add its binding to profiles.json, drop docs in input/<mno-c>/<MMMYYYY>/, then the SAME command:
run_cli --env-dir $ENV --start extract --end graph
#   per-cell stages skip unchanged cells -> only the new cell is built
#   global stages (taxonomy fingerprint-cached, graph) rebuild over the union
```
Or explicit: `--mno <mno-c> --start extract --end resolve`, then `--start
taxonomy --end graph`. Same merged result; no scratch env (supersedes the
`mno-b-spec.md` runbook workaround).

---

## 6. Per-cell resolve = structural MNO-scoping (D-DRAFT-10)

`resolve` runs **per cell** over only that cell's trees, so a cross-plan reference
can never match across MNOs or releases (plan codes / numbers aren't globally
unique). The multi-MNO no-leak property is a **layout invariant**, not resolver
code. Cross-cell relationships (release-diff, shared features) live in the
**global graph**, not the resolver.

**Assumption / watch item:** cross-references stay within a `(mno, release)` cell.
If a release is found to cite a *prior* release of the same MNO, resolve widens to
per-MNO-across-release (flagged, not built).

---

## 7. Global taxonomy (D-DRAFT-9)

One **global** taxonomy (`out/taxonomy/taxonomy.json`) derived over **all** cells
→ a shared feature set the global graph links every cell's reqs to. This is what
makes cross-MNO comparison ("compare VZW-OA vs `<mno-c>` on IMS registration")
work without fuzzy cross-cell feature alignment. Derivation is gated on a
**corpus fingerprint** (skip when the tree set is unchanged) and run at
**temperature=0** for reproducibility — resolving the standing
taxonomy-non-determinism flag for the multi-MNO path.

---

## 8. Per-cell vectorstore + query-side cell routing (D-DRAFT-11)

`vectorstore` is **per cell** — one store + BM25 per `(mno, release)`. This
isolates BM25 IDF/DF statistics (avoiding the cross-cell blending that buries the
release-diff signal — SIRA D-DRAFT-3; the dense component is statistics-agnostic)
and enables balanced fusion for comparison + release-diff.

The NORA **query pipeline becomes cell-aware**: resolve scope → select target
cell(s) → retrieve per cell → merge into one pool keyed on the composite
`(mno, release, chunk_id)` (chunk ids stay cell-local — SIRA D-DRAFT-4) → rerank →
synthesize. The global graph supplies scope/candidate routing; per-cell stores
supply retrieval; "latest release" / release-diff resolve over MMMYYYY cell order.

**This expands the strand into the query side** (currently a single ChromaDB
collection). It unblocks the deferred `QueryType.COMPARISON`. Sequence it **after**
the ingestion decisions (6–10) land.

---

## 9. SIRA adapter coupling (D-DRAFT-12)

The SIRA adapter reads NORA parse output via `_load_trees` globbing
`out/parse/*_tree.json` (flat). The nested layout (§2) breaks that — update
`_load_trees` to walk `out/parse/<mno>/<rel>/*_tree.json`. Downstream `(mno,
release)` partitioning + `--multi-cell` emission are unchanged. **Cross-strand
lockstep:** landing D-DRAFT-6 requires this adapter update in the same migration.

---

## 10. Affected modules & new shapes

| Module | Change |
|---|---|
| `pipeline` | per-cell I/O for extract/profile/parse/resolve/vectorstore; `run_profile` → resolve/validate/materialize bindings; `run_parse` reads per-cell profile + fingerprint skip; `run_taxonomy` global + fingerprint cache + temp=0; per-cell loops for resolve/vectorstore; new `--mno`/`--release`/`--force` flags |
| `env` | `ProfileBindings` loader/resolver; `EnvironmentConfig.profile_bindings` |
| `extraction` (core) | `release_key()` util; `infer_metadata_from_path` enforces MMMYYYY |
| `parser` | stamp `profile_fingerprint` on `RequirementTree` (additive) |
| `resolver` | runs per cell (no internal change beyond per-dir invocation) |
| `vectorstore` | per-cell build (own collection + BM25 per cell) |
| `query` | cell routing + per-cell retrieve + composite-id merge + rerank |
| `sandbox/adapter` | `_load_trees` walks nested `out/parse/<mno>/<rel>/` |
| *(new file)* | `<env_dir>/profiles.json` |

---

## 11. Implementation sequence

1. **D-DRAFT-6** — per-cell layout + MMMYYYY core util + OA→`VZW-OA/Feb2026`
   migration. Foundational.
2. **D-DRAFT-7** — per-cell profile binding (`profiles.json` → per-cell profile).
3. **D-DRAFT-10** — per-cell resolve (structural mno-scoping); falls out of §6.
4. **D-DRAFT-8** — per-cell skip + `--mno`/`--release`/`--force` (incremental).
5. **D-DRAFT-9** — global taxonomy fingerprint cache + temp=0.
6. **D-DRAFT-12** — SIRA adapter nested-`out/parse` read (lockstep with #1).
7. **D-DRAFT-11** — per-cell vectorstore + query-side cell routing (query-side;
   after ingestion lands).

Steps 1–6 deliver full + incremental **ingestion** (the strand's core goal);
step 7 delivers cell-aware **retrieval** (and `QueryType.COMPARISON`).

---

## 12. Back-compat & landing gate

- Single-MNO is just a **one-cell** env (`--profile` still works); no free-form
  path remains.
- **Landing gate:** no `/land-strand multi-mno-nora` until a real multi-MNO /
  multi-release set is ingested **both** ways (full + incremental), the global
  graph + per-cell stores are verified, and the cross-MNO no-leak resolver case is
  confirmed. The OA→`VZW-OA/Feb2026` migration is a prerequisite step.
- **Cross-strand:** D-DRAFT-6 amends SIRA D-DRAFT-12 (MMMYYYY → core util) and
  D-DRAFT-12 couples to the SIRA adapter — both reconcile when `multi-mno-sira`
  next lands.

---

## Related decisions

- **D-DRAFT-1..5** — MNO-B leading-id model + generic Context (make the corpora
  parseable that this design ingests).
- **D-DRAFT-6** — per-cell layout + universal `(MNO, MMMYYYY)` convention.
- **D-DRAFT-7** — per-cell profile binding.
- **D-DRAFT-8** — incremental cell ingestion.
- **D-DRAFT-9** — global taxonomy + fingerprint cache.
- **D-DRAFT-10** — structural per-cell resolve (mno-scoping).
- **D-DRAFT-11** — per-cell vectorstore + query-side cell routing.
- **D-DRAFT-12** — SIRA adapter nested-`out/parse` read.
- **SIRA D-DRAFT-3..7, 12** — the cell model NORA mirrors; D-DRAFT-12 amended here.
