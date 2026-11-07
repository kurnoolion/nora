# Stage graph — design note

Status: 2026-11-06, draft for the two rulings in §9. Nothing here is
built; the self-serve-ingestion strand's layout holds until this one
lands.

## 1. Goal

Several serving stacks build from one corpus and differ only in the
stages after it: one enriches with vanilla prompts and no taxonomy,
another with the per-release taxonomy and taxonomy prompts; a later one
may add the knowledge graph, a vector store, and a serving mode that
fuses them with SIRA. Today each stack has its own build root, so the
shared stages are computed, stored and kept once per stack, and any
change of the split ("shared up to parse") is a layout change.

The goal is a build system in which **what is shared is a consequence of
what is computed, not of where it is stored**: two builds share a stage
output when, and only when, that output would be byte-identical in
intent — same stage code, same parameters, same inputs. A recipe
chooses stages; the system finds what it can reuse.

Out of scope: the serving modes themselves (graph and RAG fusion are a
later strand's content; this one gives them a place), the registry's
one-active-set-per-stack rule, cross-host builds.

## 2. The stage graph

A stage is a node with declared inputs and parameters, computed per
cell (`<MNO>/<release>`) unless marked global.

| Stage | Inputs | Parameters | Notes |
|---|---|---|---|
| `extract` | the cell's documents | code version | per cell |
| `profile` | `extract`, the profile binding | the bound profile's fingerprint | per cell |
| `parse` | `extract`, `profile` | code version | per cell |
| `resolve` | `parse` | code version | per cell, optional |
| `taxonomy` | `parse`, the MNO's overview prompt | overview scheme | per cell (since 2026-11-06), optional |
| `enrich` | `parse`, `taxonomy` (if the recipe uses it) | prompt scheme, model, batching knobs | per cell; the SIRA cell |
| `graph` | `parse`, `resolve`, `taxonomy` | code version | optional, global over the recipe's cells |
| `vectorstore` | `parse` | embedder | optional, per cell |
| `standards` | `parse` | source | optional, global; disabled today |

The graph is data (`core/src/pipeline/stage_graph.py`): each stage
names its inputs and the parameters that enter its fingerprint. The
existing `PIPELINE_STAGES` order is the graph's topological order.

## 3. The fingerprint rule

One rule for every stage, replacing the two bespoke ones (parse's
profile fingerprint, taxonomy's corpus fingerprint):

```
fp(stage, cell) = sha256(
    stage name,
    stage code version,            # the image's NORA_CODE_VERSION, or a per-stage version string
    sorted parameters,             # as the recipe states them
    fp(input, cell) for each input stage,
    fp of external inputs          # document bytes for extract; the profile file; the overview prompt
)[:16]
```

Properties: the same recipe on the same documents reproduces the same
fingerprints; a changed prompt scheme changes `enrich` and everything
downstream, nothing upstream; a changed profile changes `profile`,
`parse` and everything after; two recipes that agree up to `parse` share
every `parse` output by construction. Language-model outputs are not
reproducible, so a fingerprint identifies the *intent* of an output,
not its bytes; that is what the registry's `source_fingerprint` already
does, and it is enough for reuse.

## 4. The stage store

One tree, write-once:

```
<stages-root>/<stage>/<MNO>/<release>/<fp>/      # the output as the stage writes it today
<stages-root>/<stage>/<MNO>/<release>/<fp>.json  # provenance: inputs' fps, parameters, code version, when, by which build
```

Produced under a temp name beside the target and renamed when complete;
never edited after; `.json` provenance written last. A degraded run
(taxonomy with failures) is not renamed into place, so the store holds
complete outputs only; resumable ledgers stay in the build's own work
dir until completion. Garbage collection by hand: an output is
removable when no build manifest and no release set references its
fingerprint (the registry's `gc` rule, one level down).

Global stages (`graph`, `standards`) key on the recipe's cell set
instead of one cell: `<stage>/<fp>/` with the cell list in provenance.

## 5. The build and its manifest

A build is a recipe applied to a set of cells, in its own directory:

```
<builds-root>/<build-id>/
  RECIPE.json          # the recipe (§6), frozen at start
  CYCLE.json           # the baton and phase log, as today
  MANIFEST.json        # {stage: {"<MNO>/<release>": fp}}, filled as stages complete
  work/                # in-progress ledgers, logs, reports (never shared)
  prompts/             # the published prompt set, as today
```

`out/<stage>/<MNO>/<release>` disappears as a directory; the pipeline
context resolves a stage's path for a cell through the manifest
(`ctx.stage_output(stage, cell)` returns the store path), so no stage
code changes beyond the resolver, and no symlinks exist. The batch
containers mount the stages root and the builds root at fixed paths.

Starting a build: for each stage in the recipe, in order, and each cell
in scope, compute the fingerprint from the inputs' fingerprints already
in the manifest; if the store has it, record it and skip; else run the
stage into the store and record it. This is the incremental cycle,
generalised: a new release of a known MNO runs every stage for that cell
once per recipe; a second recipe sharing the first's parse runs nothing
up to `parse`.

## 6. Recipes

A recipe is a small JSON document, committed under
`customizations/recipes/<name>.json`:

```json
{
  "name": "sira-taxonomy",
  "stages": ["extract", "profile", "parse", "taxonomy", "enrich"],
  "params": {
    "taxonomy": {"overview_scheme": "v2"},
    "enrich":   {"prompt_scheme": "scheme-v2", "model": "<model id>", "taxonomy": true}
  },
  "serve": {"mode": "sira", "units": ["cells", "trees", "history"]}
}
```

The vanilla stack is the same document without `taxonomy` and with
`"taxonomy": false` in `enrich`. A future recipe adds `resolve`, `graph`
or `vectorstore` to `stages`, their parameters, and a `serve.mode` the
store service implements. The recipe's name and the stage fingerprints
are recorded on the release set (`prompt_scheme` today becomes
`recipe` plus the fingerprints), so a served set says exactly how it was
built.

Which recipe a stack serves is the stack's env (`NORA_RECIPE`); which
recipes an ingest builds is §9's first ruling.

## 7. Serving

Unchanged in kind. The registry keeps one active set per stack; units
(`cells`, `trees`, `history`) are reused by fingerprint, which now
equals the stage fingerprint of the output they were built from. New
unit kinds, `graph` and `vectors`, get builders from the matching stage
outputs when a recipe serves them, and the store service gains a
`serve.mode` switch; both are later work with a place reserved here.
The serving containers mount no build: the serve root, the corrections,
the state and feedback directories, the golden directory.

## 8. Self-serve ingestion on top

One ingest of a release becomes: the corpus stages once (shared by
fingerprint), then one variant run per recipe that serves the MNO, each
with its own staged set, gate and Serve button; the Ingest page shows a
row per recipe under the ingest. The runner's stage plan becomes a loop
over recipes after `parse`; the queue stays one cycle at a time per
builds root. The driver's verbs keep their names and gain `--recipe`.

## 9. Open rulings

1. **Recipe per stack, or per ingest?** Recommendation: per stack, in
   its env; an ingest fans out to every stack's recipe that serves the
   MNO, and the page shows one staged set per stack. The alternative,
   choosing recipes on the page, invites a set no stack serves.
2. **One stage store per host, or one per corpus?** Recommendation: one
   per host, since fingerprints already separate corpora (the document
   bytes enter `extract`'s fingerprint), and one tree is one thing to
   mount, back up and garbage-collect.

## 10. Order of work

Each step leaves the system working and is landed on its own.

1. **Manifest and uniform fingerprints**, no layout change: every stage
   computes `fp(stage, cell)` by the rule in §3 and records it in
   `MANIFEST.json`; the two bespoke skip rules read the manifest. Tests
   over the fingerprint properties.
2. **The stage store**: outputs move into it, the resolver reads through
   the manifest, `start` plans reuse. The corpus-and-variant sharing
   appears here without a special case.
3. **Recipes** in the driver, the runner and the page: `RECIPE.json`,
   `--recipe`, one ingest to N staged sets, the recipe recorded on the
   release set.
4. **New unit kinds** when a recipe needs them: graph and vector units
   in the registry, a serving mode in the store.

## 11. Invariants

- A stage output in the store is never modified; a different result is
  a different fingerprint.
- A build's manifest is the only way the pipeline finds a stage output;
  a path is never computed from a layout rule.
- A fingerprint is a function of stage, code version, parameters and
  inputs, computed the same way for every stage.
- A release set records the recipe and the fingerprints it was built
  from.
- Serving containers mount no build directory.
