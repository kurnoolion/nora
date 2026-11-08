# Stage graph — design note

Status: 2026-11-08, baseline agreed; the two rulings of §9 are taken.
Nothing is built yet; the self-serve-ingestion layout holds until step 2
of §10 lands. Revision of the 2026-11-06 draft after the analysis of
what changes each stage's output (§12). A diagram of the host and the
config files accompanies this note (artifact "Stage Graph Host Layout").

## 1. Goal

Several serving stacks build from one corpus and differ only in the
stages that call a language model or an embedder: one enriches with
vanilla prompts and no taxonomy, another with the per-release taxonomy
and taxonomy prompts; a later one may add the knowledge graph, a vector
store, and a serving mode that fuses them with SIRA. Today each stack
has its own build root, so the shared stages are computed, stored and
kept once per stack, and any change of the split is a layout change.

The goal is a build system in which **what is shared is a consequence of
what is computed, not of where it is stored**: two builds share a stage
output when, and only when, that output would be identical in intent:
same stage code, same parameters, same inputs. A recipe chooses the
variant stages; the system finds what it can reuse.

Out of scope: the serving modes themselves (graph and RAG fusion are a
later strand's content; this one gives them a place), the registry's
one-active-set-per-stack rule, cross-host builds.

## 2. The stage graph and its two tiers

A stage is a node with declared inputs and parameters, computed per
cell (`<MNO>/<release>`) unless marked cross-cell.

| Stage | Inputs | Parameters | Tier | Notes |
|---|---|---|---|---|
| `extract` | the cell's documents | per-stage code version | corpus | per cell |
| `profile` | the profile binding, the bound profile | per-stage code version | corpus | per cell; materializes the substituted profile |
| `parse` | `extract`, `profile`, parse corrections | per-stage code version | corpus | per cell |
| `resolve` | `parse` | per-stage code version | corpus | per cell; always run, cheap |
| `taxonomy` | `parse`, the MNO's overview prompt | overview scheme, model, generation | variant | per cell (D-311) |
| `enrich` | `parse`, `taxonomy` (if the recipe uses it), the cell's prompt set | prompt scheme, model, batching, generation | variant | per cell; the SIRA cell |
| `graph` | `parse`, `resolve`, `taxonomy` | code version | variant | cross-cell over the recipe's cells; not used today |
| `vectorstore` | `parse` | embedder, chunking | variant | per cell; not used today |
| `standards` | `resolve` | source | corpus | cross-cell; disabled today |

**The tier rule.** A stage is corpus-side when it calls no language model
or embedder and every input of its is corpus-side. Everything else is
variant-side. The rule, not a list, decides; `graph` is deterministic
but reads `taxonomy`, so it is variant-side, and `standards` is
deterministic over `resolve`, so it is corpus-side although it is
optional and disabled today. A stage that gains a model call or a
variant-side input moves tiers, and every fingerprint downstream of it
changes once.

**The corpus prefix** `extract → profile → parse → resolve` is one fixed
chain that no recipe can vary. Every stack on a host computes the same
fingerprints for it. A recipe names variant stages only.

The graph is data (`core/src/pipeline/stage_graph.py`): each stage names
its inputs, its parameters and its tier. The existing `PIPELINE_STAGES`
order is the graph's topological order.

## 3. The fingerprint rule

One rule for every stage, replacing the two bespoke ones (parse's
profile fingerprint, taxonomy's corpus fingerprint):

```
fp(stage, cell) = sha256(
    stage name,
    stage code version,            # corpus tier: a version string the stage module owns
                                   # variant tier: the image's NORA_CODE_VERSION
    sorted parameters,             # as the recipe states them; generation included
    fp(input, cell) for each input stage,
    fp of external inputs          # document bytes; the resolved profile; the corrections
                                   # that apply to the cell; the prompt files
)[:16]
```

**Code version by tier.** Corpus stages hash a per-stage version string
that the stage's module owns and that is bumped when the stage's logic
changes. If the image version entered the parse fingerprint, every image
rebuild for a prompt change would invalidate every parse on the host,
and two stacks on different image builds would never share. Variant
stages hash the image version: their outputs are per recipe anyway.

**Properties.** The same recipe on the same documents reproduces the same
fingerprints. A changed prompt scheme changes `enrich` and everything
downstream, nothing upstream. A changed profile changes `profile`,
`parse` and everything after, for the cells bound to that profile. Two
recipes agree on every corpus fingerprint by construction. Impact scope
is derived from the inputs, never configured (§12).

**Language-model outputs are not reproducible.** A fingerprint identifies
the *intent* of an output, not its bytes; that is what the registry's
`source_fingerprint` already does, and it is enough for reuse. The
consequences: the store keeps the first complete result for a
fingerprint; a deliberate redo with unchanged inputs bumps the recipe's
`generation` parameter for that stage.

**Lineage.** The last fingerprint identifies the whole chain, because
every link hashes its inputs' fingerprints and its own identity. The
provenance file beside each output (§4) is what makes the chain
traversable; the hash alone cannot be inverted.

## 3a. Reuse inside a variant stage

Strict cascading makes a parser fix expensive: every parse fingerprint
changes, so every enrich fingerprint changes, so every cell re-enriches
at an hour or more each. The remedy already exists in the code: the
taxonomy ledger and SIRA's incremental promote reuse per-unit results
keyed by requirement text.

The design keeps both levels. The stage fingerprint is the identity of
the output as a whole. Inside a variant stage, a unit (a requirement for
`enrich`, a plan for `taxonomy`) is reused from any earlier output of the
same stage with the same parameters when the unit's text fingerprint is
unchanged. A parse fix that changes structure but not text then costs a
parse and a cheap pass over the cell, not a re-enrichment. The stage
records in its provenance which earlier output it reused from.

## 4. The stage store

One tree per host, write-once:

```
<stages-root>/<stage>/<MNO>/<release>/<fp>/        # the output as the stage writes it today
<stages-root>/<stage>/<MNO>/<release>/<fp>.json    # provenance: inputs' fps, parameters, code
                                                   # version, tier, when, by which build, reused-from
<stages-root>/<stage>/<MNO>/<release>/<fp>.building  # marker while a runner computes it
```

Produced under a temp name beside the target and renamed when complete;
never edited after; `.json` provenance written last. A degraded run
(taxonomy with failures) is not renamed into place, so the store holds
complete outputs only; resumable ledgers stay in the build's own work
dir until completion.

**Concurrent producers.** Two stacks ingesting one release at once would
both compute parse. The `.building` marker names the build and the
runner; a second runner finding it waits, as it already waits on a
detached phase, with a timeout after which it computes anyway. If both
finish, the first rename wins and the second discards its copy and
records the winner's fingerprint; the result is the same by intent.

**Garbage collection**, by hand: an output is removable when no build
manifest under any builds root and no release set in any registry on the
host references its fingerprint. A host-level list of serve roots and
builds roots is the input; the registry's `gc` rule, one level down.

Cross-cell stages (`graph`, `standards`) key on the recipe's cell set
instead of one cell: `<stage>/<fp>/` with the cell list in provenance.

## 5. The build and its manifest

A build is a recipe applied to a set of cells, in its own directory
under the stack's builds root (D-319):

```
<builds-root>/<build-id>/
  RECIPE.json          # the recipe (§6), frozen at start
  CYCLE.json           # the baton and phase log, as today
  STAGES.json          # the manifest: {stage: {"<MNO>/<release>": {fp, provenance}}}, filled as stages complete
  work/                # in-progress ledgers, logs, reports (never shared)
  prompts/             # the published prompt set, as today
```

The manifest is named `STAGES.json`, not `MANIFEST.json`: the promoted
snapshot under the labels root already carries a `MANIFEST.json` of
another shape. A global stage is keyed `*`; its fingerprint carries the
cell set through its inputs.

`out/<stage>/<MNO>/<release>` disappears as a directory; the pipeline
context resolves a stage's path for a cell through the manifest
(`ctx.stage_output(stage, cell)` returns the store path), so no stage
code changes beyond the resolver, and no symlinks exist. The batch
containers mount the stages root, the corpus root and the builds root at
fixed paths.

Starting a build: for each stage, corpus prefix first and then the
recipe's stages in order, and each cell in scope, compute the
fingerprint from the inputs' fingerprints already in the manifest; if
the store has it, record it and skip; if it is being built, wait; else
run the stage into the store and record it. This is the incremental
cycle, generalised: a new release of a known MNO runs every stage for
that cell once per recipe; a second recipe on the same release runs
nothing in the corpus prefix.

## 6. Recipes

A recipe is a small JSON document, committed under
`customizations/recipes/<name>.json`, naming variant stages only:

```json
{
  "name": "taxonomy",
  "stages": ["taxonomy", "enrich"],
  "params": {
    "taxonomy": {"overview_scheme": "v2", "model": "<model id>", "generation": 1},
    "enrich":   {"prompt_scheme": "scheme-v2", "model": "<model id>",
                 "taxonomy": true, "generation": 1}
  },
  "serve": {"mode": "sira", "units": ["cells", "trees", "history"]}
}
```

The vanilla stack's recipe lists `enrich` alone with `"taxonomy": false`.
A future recipe adds `graph` or `vectorstore` to `stages`, their
parameters, and a `serve.mode` the store service implements. The
recipe's name and the stage fingerprints are recorded on the release set
(`prompt_scheme` today becomes `recipe` plus the fingerprints), so a
served set says exactly how it was built.

**Which recipe a stack serves is the stack's serving env**
(`NORA_RECIPE`). The runner reads it from the stack env it already
holds. An ingest on a stack builds that stack's recipe and no other
(§9, ruling 1).

## 6a. Corpus-level inputs

The corpus prefix is shared only if its external inputs are shared. Today
the profile bindings file and the parse corrections live in the build
directory and are copied with the seed build, so they are per stack: two
stacks could parse one release differently, correctly but without
sharing, and nothing would say so.

They move to one corpus root that every stack's wiring env names
(`NORA_CORPUS_DIR`):

```
<corpus>/requirements/<MNO>/<release>/   # the documents, as today (NORA_REQUIREMENTS_DIR)
<corpus>/profiles.json                   # the bindings
<corpus>/profiles/*.json                 # the bound profiles
<corpus>/corrections/parse/*.json        # parse corrections
```

The taxonomy overview prompts and the SIRA prompt sets stay with the
build: they feed variant stages. The driver's seed step stops copying
bindings and corrections; the batch containers mount the corpus root.

## 7. Serving

Unchanged in kind. The registry keeps one active set per stack; units
(`cells`, `trees`, `history`) are reused by fingerprint, which now equals
the stage fingerprint of the output they were built from. Two stacks on
one release therefore name the same Trees unit and the same History
unit; only Cells differ. New unit kinds, `graph` and `vectors`, get
builders from the matching stage outputs when a recipe serves them, and
the store service gains a `serve.mode` switch; both are later work with
a place reserved here. The serving containers mount no build (D-318).

**Outside the graph**, by design: the Overlay review edits (composed at
serve time, D-275), the serving knobs, the golden eval (a gate, not a
stage). None enters a fingerprint.

## 8. Self-serve ingestion on top

An ingest on a stack is what it is today: documents, the corpus prefix,
the stack's recipe, convert, publish, staging store, gate, flip. The
difference is inside `start`: each corpus stage is found or computed in
the store, and the variant stages run under the recipe's parameters. No
runner knows another stack exists. The same release ingested on the
second stack finds the corpus prefix complete and runs its variant
stages only; the Ingest page shows which stages were found and which
were computed. The driver's verbs keep their names; `start` reads the
recipe from the stack env.

## 9. Rulings (taken 2026-11-08)

1. **Recipe per stack, no fan-out.** The recipe is the stack's, named in
   its serving env; an ingest builds that stack's recipe. Sharing is
   the stage store's job, not the runner's. One submit fanning out to
   every stack was rejected: it needs one runner that knows every
   stack's envs, or a shared jobs database, for a convenience the
   fingerprints already give. Choosing recipes on the page was
   rejected: it invites a set no stack serves.
2. **One stage store per host.** Fingerprints separate corpora (the
   document bytes enter `extract`), and one tree is one thing to mount,
   back up and garbage-collect. Per corpus was rejected: a root per
   requirements directory and a rule for which stacks belong to which,
   for isolation the fingerprints give already. The cost accepted: cell
   names are visible across stacks on the host, and GC consults every
   registry on the host.

## 10. Order of work

Each step leaves the system working and is landed on its own.

1. **Manifest and uniform fingerprints**, no layout change (built
   2026-11-08): the stage graph with tiers as data
   (`pipeline/stage_graph.py`); every stage computes `fp(stage, cell)`
   by the rule in §3 (per-stage `CODE_VERSION` for the corpus tier,
   pinned to the package source by a test) and records it in
   `STAGES.json` (`pipeline/manifest.py`, `pipeline/fingerprints.py`);
   the extract, parse and taxonomy skip rules read the manifest. Found
   on the way: the taxonomy stage had stopped passing the overview
   directory to the extractor on 2026-11-06; restored.
2. **Corpus root and the stage store**: bindings and parse corrections
   move to `<corpus>`; outputs move into the store with provenance and
   the building marker; the resolver reads through the manifest;
   `start` plans find-or-compute. Both stacks' wiring envs name the
   stages root and the corpus root. The corpus-and-variant sharing
   appears here without a special case. D-319's "superseded when
   stage-graph lands" clause fires here: the builds roots stay per
   stack but hold manifests, not copies.
3. **Recipes** in the driver, the runner and the page: `RECIPE.json`
   frozen from the stack env's recipe, `generation`, per-unit reuse
   recorded in provenance, the recipe and fingerprints recorded on the
   release set, the page showing found versus computed.
4. **New unit kinds** when a recipe needs them: graph and vector units
   in the registry, a serving mode in the store.

## 11. Invariants

- A stage output in the store is never modified; a different result is
  a different fingerprint; the first complete result for a fingerprint
  is the one kept.
- A build's manifest is the only way the pipeline finds a stage output;
  a path is never computed from a layout rule.
- A fingerprint is a function of stage, code version, parameters and
  inputs, computed the same way for every stage; a corpus stage's code
  version is its own, never the image's.
- A recipe names variant stages only; the corpus prefix is the same for
  every stack on a host.
- The corpus prefix's external inputs live under the corpus root, never
  in a build.
- A release set records the recipe and the fingerprints it was built
  from.
- Serving containers mount no build directory.
- The Overlay, the serving knobs and the golden eval never enter a
  fingerprint.

## 12. What changes what

Derived from §2 and §3, recorded here so the team can predict the cost
of a change. "All cells" means every cell on the host for corpus
stages, every cell of the recipe for variant stages.

| Change | First stage affected | Cells affected | Cascade |
|---|---|---|---|
| new or revised document in a cell | `extract` | that cell | everything |
| extractor code (a new PDF or DOCX extractor) | `extract` | all | everything |
| a profile's content | `profile` | the cells bound to it (one release, an MNO, or every MNO on the default) | parse onward |
| parser code | `parse` | all | resolve, taxonomy, enrich (text-unchanged units reused, §3a), Trees, History |
| a parse correction | `parse` | the cells it applies to | as above |
| resolver code | `resolve` | all | graph only (today: nothing served) |
| an MNO's overview prompt | `taxonomy` | that MNO's cells, recipes with taxonomy | enrich where the recipe uses taxonomy |
| taxonomy model, scheme or code | `taxonomy` | all, recipes with taxonomy | as above |
| a cell's SIRA prompt set | `enrich` | that cell | Cells unit |
| enrich model, scheme or SIRA code | `enrich` | all | Cells unit |
| embedder or chunking | `vectorstore` | all, recipes with a vector store | vectors unit |
| a new release of an MNO | every stage, that cell | that cell, plus History for the MNO and graph for the recipe | — |
| Overlay edit, serving knob, golden sample | none | — | — |
