# Stage graph — design note

Status: 2026-11-09. Baseline agreed and the two rulings of §9 taken on
2026-11-08; steps 1 and 2 of §10 built. Step 2 revised one point of the
baseline: a build keeps its `out/` tree, because about thirty modules
outside the pipeline read that layout; the store gives the sharing
(§4, §5). Revision of the 2026-11-06 draft after the analysis of
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

One tree per host (`NORA_STAGES_ROOT`), write-once
(`pipeline/stage_store.py`):

```
<stages-root>/<stage>/<MNO>/<release>/<fp>/          # the output, as the stage writes it
<stages-root>/<stage>/<MNO>/<release>/<fp>.json      # provenance: inputs' fps, parameters, code
                                                     # version, tier, the build that computed it
<stages-root>/<stage>/<MNO>/<release>/<fp>.building  # marker while a producer computes it
```

**What goes in.** Every per-cell stage's complete output, except the
vector store (Chroma rewrites its database file on open) and except a
variant stage run from an image without a code version (the fingerprint
would then not see code changes). Global stages (standards, graph, eval)
stay in the build until a recipe needs them shared (step 4).

**Find, compute, put** (as of step 4, §10c). Before a stage writes a
cell it computes the fingerprint. If the build recorded it, or the store
holds it, the cell is reused and recorded with origin `store` when it
came from another build; nothing is copied into the build, and every
reader finds the output through the build's manifest
(`pipeline/locate.py`). Else the stage computes into the build's cell
directory, and on a clean completion the output moves into the store:
cloned under a temp name, its files made read-only, renamed into place,
and the build's directory removed; provenance is written after the
rename. A degraded run (taxonomy with failures, extract or parse with a
failed document) is never put in the store; its resumable state stays
in the build, where readers find it first. Steps 2 and 3 cloned stored
outputs into every build (a copy across the container mounts, about
342 MB per build); step 4 removed that.

**Write-once.** A stored output is read-only and never written: a
re-run is a new fingerprint. Before a stage writes a cell, every file in
the build's cell directory that shares an inode with another path, or is
read-only, is replaced by a private copy, which protects the store and
labels from a build made before step 4 (whose `out/` holds clones) and
from a taxonomy cell seeded from the store.

**Concurrent producers.** Two stacks ingesting one release at once would
both compute parse. The `.building` marker names the build; a second
producer finding a live marker of another build waits (polling, up to
`NORA_STAGES_WAIT_SECONDS`, default one hour; an older marker counts as
abandoned), then takes the stored output or computes it. If both
finish, the first rename wins; the second drops its own output and
records the winner's. `--force` computes regardless and keeps
its own output; the store keeps the first result (origin `forced`).

**Garbage collection**, by hand, and safe at any time: builds and
labels hold their own links or copies, so removing a store entry loses
only the sharing, never an artifact. Policy (keep what any manifest on
the host names, and the release sets' fingerprints) is step 3's, slice 4
(§10b).

## 5. The build and its manifest

A build is a recipe applied to a set of cells, in its own directory
under the stack's builds root (D-319):

```
<builds-root>/<build-id>/
  RECIPE.json          # the recipe (§6), frozen at start (step 3)
  CYCLE.json           # the baton and phase log, as today
  STAGES.json          # the manifest: {stage: {"<MNO>/<release>": {fp, origin, provenance}}}
  out/<stage>/<MNO>/<release>/   # build-held outputs only (step 4): enrich (out/sira), the vector
                                 # store, graph, taxonomy from an unversioned image, and a degraded
                                 # or forced run; stored outputs live in the store alone
  prompts/  reports/   # as today
```

The manifest is named `STAGES.json`, not `MANIFEST.json`: the promoted
snapshot under the labels root already carries a `MANIFEST.json` of
another shape. A global stage is keyed `*`; its fingerprint carries the
cell set through its inputs. `origin` says where an entry's output came
from: `computed`, `store` or `forced`.

**Why the build kept `out/` until step 4.** The 2026-11-08 baseline had builds hold
manifests only, with the pipeline resolving every path through the
manifest into the store. A survey for step 2 found about thirty modules
outside the pipeline that read `<build>/out/<stage>/<cell>/` by layout:
the SIRA lane, promote, the Trees and History builders, the parse and
resolve review pages, the request browser, golden eval, the debug CLIs.
Moving them all to manifest lookups is a cross-module change that adds
no sharing. Steps 2 and 3 therefore kept the build's cell directories
as clones of the stored outputs. Step 4 (§10c) moved every reader onto
one resolver instead, and the clones went.

Starting a build: for each stage in order and each cell in scope,
compute the fingerprint from the inputs' fingerprints already in the
manifest; reuse, find, or compute as in §4. A new release of a known MNO
computes every stage for that cell once per host; a second stack on the
same release computes nothing in the corpus prefix.

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

The corpus prefix is shared only if its external inputs are shared. The
profile bindings and the user annotations lived in the build directory
and the bindings were copied from the seed build, so they were per
stack: two stacks could parse one release differently, correctly but
without sharing, and nothing would say so.

They move to one corpus root that every stack's wiring env names
(`CORPUS_DIR`, mounted read-only in the batch container as
`NORA_CORPUS_DIR`):

```
<corpus>/profiles.json        # the bindings of every stack on the host
<corpus>/annotations/         # user annotations (D-061), read by parse
```

The bound profile files stay in `customizations/profiles/` (committed,
in the image). The documents stay under `REQUIREMENTS_DIR`, already one
tree shared by both stacks' wiring envs. The parse corrections the parse
review page writes feed the profile miner, not the parse stage, so they
are not a corpus input. The taxonomy overview prompts and the SIRA
prompt sets stay with the build: they feed variant stages.

With a corpus root, `cycle.sh start` reads the bindings there and binds
a new release of a known MNO there; the build holds no `profiles.json`,
`--profiles` is refused, and a seed build needs no bindings. Without
one, everything is as before.

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
2. **Corpus root and the stage store** (built 2026-11-09): bindings
   and annotations read from `<corpus>`; per-cell outputs found in and
   put into the store with provenance and the building marker
   (`pipeline/stage_store.py`, `Fingerprinter.prepare`); builds keep
   `out/` as clones (§5); `cycle.sh` corpus mode; the runner skips a
   served seed without bindings; compose mounts both roots and passes
   `CODE_VERSION`. Both stacks' wiring envs name the two roots. D-319's
   "superseded when stage-graph lands" clause fires here in part: the
   builds roots stay per stack and keep their outputs, and the corpus
   prefix is computed once per host.
3. **Recipes** in the driver, the runner and the page: `RECIPE.json`
   frozen from the stack env's recipe, `generation`, per-unit reuse
   recorded in provenance, the recipe and fingerprints recorded on the
   release set, the page showing found versus computed. In four slices
   (§10b).
4. **Builds hold manifests, not outputs** (scope added 2026-11-11, §10c):
   a resolver answers where a stage's output for a cell is; every reader
   outside the pipeline asks it; promote assembles labels from the store;
   the pipeline stops cloning stored outputs into the build. Revises §5's
   "why the build keeps `out/`" (D-DRAFT-8).
5. **Stack A on the stage graph** (scope added 2026-11-11): its hand-built
   setup rebuilt by the cycle driver with recipe `sira-plain`, on the
   layout of step 4, so both stacks run the same machinery.
6. **Parallel builds** (scope added 2026-11-11): the limits the store does
   not solve. `cycle.sh start` writes the corpus bindings without a lock;
   the runner takes one job at a time; the marker wait defaults to an
   hour; concurrent enrich runs share one model endpoint.
7. **New unit kinds** when a recipe needs them: graph and vector units
   in the registry, a serving mode in the store.

## 10a. Step 2 on the host: migration of what exists (2026-11-09)

The inventory of 2026-11-08 found: two per-stack builds roots with one
real build each, no manifests; both builds bind the same three profiles
(`release=*`, no default) and neither stack has parse corrections or
annotations in a build; the requirements tree is already one directory
shared by both wiring envs; ten serve labels and two cell-DB sets on
disk, two labels read; stack A on the wiring that predates self-serve
ingestion; the one taxonomy-gap cell being re-derived in a hand cycle.

Three facts make the migration small:

- Served sets do not depend on builds (D-318). Registries, labels and
  cell DBs are untouched by step 2.
- An incremental ingest touches one cell. Existing cells are not
  recomputed until something about them changes.
- The bindings are identical across stacks, so one corpus-level
  `profiles.json` is a copy of either build's.

**Plan.**

1. Corpus root: `<corpus>/profiles.json` copied from the active build;
   `<corpus>/annotations/` empty. Both wiring envs gain `CORPUS_DIR`.
2. Stage store: one empty `<stages>/` per host, on the data filesystem;
   both wiring envs gain `STAGES_ROOT`.
3. Stack A joins the self-serve wiring (build dir, share map, token,
   serving env without an env dir) in the same edit; the two roots are
   not added to a wiring the runner cannot use.
4. Batch images rebuilt with `CODE_VERSION` from the tip.
5. Existing builds stay as they are, read-only history. No legacy
   output is imported into the store: an import would stamp fingerprints
   on outputs made by older code with no check, and nothing needs them
   before a full rebuild. If a full rebuild is wanted later, an import
   tool can admit a legacy build's corpus-prefix outputs for stages
   whose package is unchanged between the build's commit and the tip.
6. Clean-up of the unreferenced snapshots, legacy seed builds and cell
   DBs (about 10 GB) is manual and separate.

**Cutover order.** Corpus root, store and envs; rebake the batch images;
the next ingest on stack B computes its one cell and puts the corpus
prefix in the store; stack A follows with its first self-serve ingest.
Nothing is flipped by step 2 itself, and each stack's last served set
stays as the rollback.

## 10b. Step 3 in slices (planned 2026-11-10)

Enrichment is not a pipeline stage: the cycle driver runs the SIRA lane
in the sira-batch container, configured by that container's env file
(endpoint, model, prompt directory) and by `NORA_SIRA_TAXONOMY_DIR`,
which the driver sets per cell. A release set records only a scheme
tag. Step 3 therefore lands in four slices, each leaving the system
working:

1. **Recipe as data, frozen in the build, recorded on the release set.**
   `customizations/recipes/<name>.json`, validated by
   `pipeline/recipes.py` (standard library only: the host-side driver
   and runner import it). `cycle.sh start --recipe <name>` (else the
   wiring env's `RECIPE`) copies it to `<build>/RECIPE.json`. The driver
   reads it: `taxonomy` refuses and `enrich` stops requiring it when the
   recipe has no taxonomy; `enrich` sets the taxonomy directory only when
   `enrich.taxonomy` is true, and passes a model the recipe names;
   `promote` takes the scheme from the recipe. The taxonomy fingerprint
   carries the recipe's taxonomy parameters (`generation`, `model`).
   Promote copies `RECIPE.json` and `STAGES.json` into the label; publish
   records the recipe's name and digest and the build's stage
   fingerprints on the release set (two additive columns). The runner
   passes the stack env's `NORA_RECIPE`. A build without a recipe runs as
   today. Model choice stays in the env files unless a recipe names one;
   endpoints and keys never move into a recipe.
2. **Enrich as a recorded stage** (built 2026-11-10). A stage spec for `enrich` (inputs
   parse and, when the recipe says so, taxonomy; externals the MNO's doc
   prompt; params the recipe's enrich parameters and the model); the
   driver stamps it in `STAGES.json` after `verify-enrich`. Enrich
   outputs stay in the build (large; SIRA's incremental promote already
   reuses per requirement).
3. **The page and the redo** (built 2026-11-10). The runner records each stage's origin
   (computed, store, forced) on the ingest; the Ingest page shows it.
   "Redo a served cell": a per-cell `generation` bump recorded at the
   corpus root, which enters that cell's variant fingerprints only.
4. **Store garbage collection** (built 2026-11-10). `stage_store gc`:
   keep what any manifest under a builds root or labels root, or any
   release set's `stage_fps` in a registry on the host names; also keep
   an entry younger than `--min-age-hours` (default 24: a build may
   have found it and not yet recorded it) and one under a live building
   marker; clear temp dirs, orphan provenance and abandoned markers past
   the same age. Dry run by default. It refuses (`PIP-E006`) with no
   source, a missing one, or an unreadable manifest or registry: a
   source it cannot read could name anything.

## 10c. Step 4: builds hold manifests, not outputs (planned 2026-11-11)

As built in step 2, a build's `out/` holds a clone of every stored
output it uses. The batch containers mount the builds and the store
separately, so each clone is a copy: about 342 MB per build on the
host, extract being 328 MB of it, and N copies for N builds running in
parallel. Step 4 removes the clone. The store holds the only copy of a
stored output; a build holds its manifest and the outputs the store
never takes.

**The resolver.** `locate(build, stage, cell)` returns the directory
holding a stage's output for a cell (`pipeline/locate.py`, standard
library only, with a CLI for `cycle.sh`, `promote.sh` and the runner):

1. the build's `out/<stage>/<MNO>/<release>/` when it exists;
2. else the store entry the manifest names, when the store holds it and
   the entry's origin is not `forced`;
3. else none.

The build is asked first so that every build made before step 4 reads
as today, and so that outputs the store never takes (enrich, graph,
vector store, taxonomy from an unversioned image, a degraded or forced
run) stay where they are. A container passes its own stages root
(`NORA_STAGES_ROOT`); the host passes the wiring env's `STAGES_ROOT`.

**Who changes** (repo survey, 37 files): the pipeline itself (one
change: inputs through `locate`, stored stages committed by
fingerprint, `link_view` and `privatize` removed); eight build-time
consumers (`cycle.sh`, the SIRA adapter, `promote.sh`, the Trees and
History builders, `FileTreeStore`, the golden release CLI); eight debug
and audit tools through one helper. Serving reads labels or nothing and
does not change; labels keep their `nora/out/{parse,taxonomy,graph,
vectorstore}` layout. Nothing on the host outside the repo reads a
build's `out/`.

**Per-document reuse in extract.** Today extract skips a document whose
IR in the build is newer than its source. Without the clone, a cell
with one changed document gets a new fingerprint; its unchanged IRs are
hard-linked from the cell's previous store entry. The store is one
mount in the container, so the links work.

**Promote** builds the label on the host, where the store, the builds
and the serve roots share one filesystem: stored stages hard-linked from
the store, build-held stages from the build, the vector store still
copied.

**Slices**, each leaving the system working:

1. The resolver and its CLI. Every build has `out/` today, so every
   answer is today's path.
2. The build-time consumers and the debug tools move onto it (built
   2026-11-11). The file Trees store (`parse_cell_dir`, `parse_cells`)
   and through it the Trees and History builders; the golden release
   CLI; the SIRA adapter's tree loading; `cycle.sh`'s status counts,
   taxonomy ledgers and the lane's taxonomy dir (resolved on the host
   per cell, then mapped to the containers' mount by `ctr_path`); the
   debug and audit tools through `stage_files` / `stage_dirs`
   (`parse_debug`, `parse_audit`, `profile_debug`, `tax_debug`,
   `embed_debug`, `verify_req_recall --env-dir`). Found on the way:
   these tools read only the flat layout from before per-cell outputs,
   so on any current build they found nothing; they now read every
   cell, and `parse_audit` writes one report directory per cell.
   Unchanged: `retrieval_debug` (the vector store stays in the build),
   `verify_tables` (it takes a directory, not a build), serving.
3. Promote assembles the label through it (built 2026-11-11):
   `promote.sh --stages-root` (passed by `cycle.sh`) links into the label
   the build's own `out/` stages as before, plus every parse and
   taxonomy cell only the store holds, from the entry the manifest
   names, at the label's unchanged layout. An unreadable manifest
   refuses before the label directory exists, since labels are never
   reused.
4. Per-document extract reuse across store entries; check whether a
   taxonomy correction feeds a stage or only the review pages (built
   2026-11-11). Extract records each IR's source digest in
   `.sources.json` beside the IRs. A cell computing under a new
   fingerprint takes the IR (and its images) of every document whose
   source bytes are unchanged from the cell's newest store entry with the
   same code version, parameters and other inputs; only changed documents
   are extracted. Before slice 5 the take is a copy across the container
   mounts; after it, a link inside the store. The taxonomy correction
   feeds no per-cell stage: it replaces only the build-level
   `taxonomy.json` that the graph and vector store stages and the review
   editor read. It now enters those two stages' fingerprints
   (`taxonomy_correction`), as the profile correction enters profile's.
   Found for slice 5: parse's `annotations` external lists the cell's
   documents from the build's `out/extract/`; it must go through the
   resolver once extract output leaves the build.
5. The pipeline writes stored stages into the store and stops cloning;
   a new build's `out/` holds only build-held stages (built 2026-11-11).
   Every input read in the pipeline goes through
   `PipelineContext.stage_input` (the resolver), the global stages take
   file lists; `stamp` moves a clean output into the store and removes
   the build's directory; `prepare` reuses a stored output without a
   copy and removes leftovers that would shadow it; `link_view` is gone.
   The resolver ignores an empty build directory (a stage may create
   one before it finds the output stored). Taxonomy, whose per-plan
   reuse lived in the build's cell directory, seeds a new fingerprint
   from the cell's newest store entry with the same model, code version
   and prompts, so unchanged plans are not asked again; a redo or a
   model change seeds nothing. sira-batch mounts the store read-only
   with `NORA_STAGES_ROOT`, since the lane reads parse trees and
   taxonomy there.

Rejected: a pass that relinks a build's copies to the store after each
stage (N copies exist until it runs, and every build pays the copy and
the compare); one mount holding the store beside the builds (links stop
at one filesystem, so builds on a second host cannot share; compose and
`/data/env` paths change). Both keep the privatize-before-write rule;
under step 4 a re-run is a new fingerprint and never edits a file.

## 11. Invariants

- A stage output in the store is never modified; a different result is
  a different fingerprint; the first complete result for a fingerprint
  is the one kept.
- A cell's output is reused whole only by fingerprint (the build's
  manifest, then the store); the stage's completeness check guards the
  reuse, it never grants it. A stored output lives only in the store; a
  build holds its manifest and the outputs the store does not take, and
  every reader finds either through the resolver.
- A stage never writes a file that shares its inode with the store or a
  label.
- A fingerprint is a function of stage, code version, parameters and
  inputs, computed the same way for every stage; a corpus stage's code
  version is its own, never the image's.
- A recipe names variant stages only; the corpus prefix is the same for
  every stack on a host.
- With a corpus root, the corpus prefix's external inputs (bindings,
  annotations) live there, never in a build.
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
