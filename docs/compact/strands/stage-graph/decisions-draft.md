### D-DRAFT-1 — 2026-11-08 — Stages split into a corpus tier and a variant tier by one rule; recipes name variant stages only

**Context.** For the foreseeable future every stack shares extract through parse and differs only in the stages that call a language model or an embedder. The first draft let a recipe list any stage, so the sharing depended on recipes happening to agree.

**Decision.** A stage is corpus-side when it calls no model and every input of its is corpus-side; otherwise variant-side. The corpus prefix (extract, profile, parse, resolve) is one fixed chain for every stack on a host. A recipe names variant stages only. Graph is variant-side because it reads taxonomy.

**Why.** The sharing becomes a constraint the code enforces instead of an outcome of matching recipes. A list of corpus stages was rejected in favour of the rule so a stage that gains a model call moves tiers by definition.

**Consequences.** Resolve runs always (cheap, deterministic). Standards lands corpus-side by the rule (deterministic over resolve) although it is optional and disabled. Moving a stage across the boundary changes every downstream fingerprint once.

### D-DRAFT-2 — 2026-11-08 — One fingerprint rule; corpus stages carry their own code version, variant stages the image's

**Context.** The draft rule allowed "the image's code version, or a per-stage version string" without choosing. If the image version enters the parse fingerprint, every image rebuild for a prompt change invalidates every parse on the host, and stacks on different image builds never share.

**Decision.** `fp = sha256(stage, code version, sorted params, input fps, external-input fps)[:16]` for every stage. A corpus stage hashes a version string its module owns, bumped when its logic changes. A variant stage hashes `NORA_CODE_VERSION`. Impact scope is derived from the inputs, never configured.

**Why.** Corpus outputs must survive image rebuilds that do not touch them; variant outputs are per recipe anyway, so the coarse version costs nothing there.

**Consequences.** Forgetting to bump a corpus stage's version serves stale output under a matching fingerprint; a test pins each version string to a hash of the stage module's source as a reminder. The two bespoke skip rules (parse's profile fingerprint, taxonomy's corpus fingerprint) are replaced.

### D-DRAFT-3 — 2026-11-08 — The corpus prefix's external inputs live under one corpus root, not in the build

**Context.** The profile bindings are copied into each build from the seed build and the user annotations live in the build, so both are per stack. Two stacks could parse one release from different bindings: correct, unshared, and silent.

**Decision.** `<corpus>/` (`CORPUS_DIR` in every stack's wiring env, mounted read-only as `NORA_CORPUS_DIR`) holds `profiles.json` and `annotations/`. With it, `cycle.sh start` binds new cells there and copies nothing into the build. Bound profiles stay in `customizations/profiles/`, documents under `REQUIREMENTS_DIR` (already shared); parse corrections feed the profile miner, not parse, so they stay put. Overview prompts and SIRA prompt sets stay with the build: they feed variant stages.

**Why.** A shared prefix needs shared inputs; the alternative, detecting divergence after the fact, would only report what this prevents.

**Consequences.** A profile edit reaches every stack on its next ingest. The parse review page still writes annotations to the env dir it serves; pointing it at the corpus root is open.

### D-DRAFT-4 — 2026-11-08 — Recipe per stack, in its serving env; an ingest builds that stack's recipe only

**Context.** Ruling 1 of the design note. Each stack has its own web, jobs database, runner and registry (D-306, D-318, D-319). The draft's "per stack" assumed one submit fans out to every stack.

**Decision.** `NORA_RECIPE` in the stack's serving env names a recipe under `customizations/recipes/`. An ingest on a stack runs the corpus prefix (found or computed in the stage store) and that stack's variant stages. No runner knows another stack. The same release ingested on a second stack finds the prefix complete.

**Why.** The fingerprints already give the sharing; fan-out would add a runner that knows every stack's envs, or a shared jobs database, for a convenience. Choosing recipes on the page was rejected: it invites a set no stack serves.

**Consequences.** A release reaches two stacks by two submits. The Ingest page shows found versus computed stages.

### D-DRAFT-5 — 2026-11-08 — One stage store per host; first complete result wins; GC consults every registry on the host

**Context.** Ruling 2 of the design note. Registries are per stack; two stacks' runners run as one build user on one host.

**Decision.** One `<stages-root>` per host, named in every stack's wiring env. Outputs are written under a temp name and renamed; a `<fp>.building` marker lets a second runner wait on the first, with a timeout; on a race the first rename wins and the second records the winner's fingerprint. An output is removable only when no manifest under any builds root and no release set in any registry on the host references it.

**Why.** Fingerprints separate corpora (document bytes enter extract), so a store per corpus would add a root and a stack-to-corpus rule for isolation already present. One tree is one mount, one backup, one GC pass.

**Consequences.** Cell names are visible across stacks on the host. A forced run keeps its own output and leaves the store's first result. Builds keep their outputs (D-DRAFT-8), so removing a store entry loses sharing, never an artifact.

### D-DRAFT-6 — 2026-11-08 — Variant stages reuse per unit by text fingerprint; a redo bumps `generation`

**Context.** Strict cascading makes every parser fix re-enrich every cell at an hour or more each. Language-model outputs are not reproducible, so "same fingerprint, same bytes" cannot hold, and a redo with unchanged inputs has no fingerprint to run under.

**Decision.** The stage fingerprint identifies the output as a whole. Inside a variant stage a unit (a requirement for enrich, a plan for taxonomy) is reused from any earlier output of the same stage and parameters when its text fingerprint is unchanged; provenance records the source. The store keeps the first complete result per fingerprint. Each variant stage's recipe params carry `generation`; bumping it is the one way to recompute unchanged inputs.

**Why.** The taxonomy ledger and SIRA's incremental promote already reuse by text; the design keeps that instead of paying the cascade. A redo knob outside the fingerprint was rejected: it would make two outputs with one identity.

**Consequences.** A parse fix that changes structure but not text costs a parse and a cheap pass. The Overlay, serving knobs and golden eval stay outside every fingerprint (composed or applied at serve time).

### D-DRAFT-7 — 2026-11-09 — Step 2 imports no legacy outputs; old builds stay as they are

**Context.** The 2026-11-08 inventory: one real build per stack, no manifests, identical bindings across stacks; served sets do not depend on builds (D-318); an incremental ingest touches one cell.

**Decision.** Step 2 adds the corpus root and the store and leaves existing builds untouched. No legacy output is imported into the store.

**Why.** An import would stamp fingerprints on outputs made by older code with no check, and nothing needs them before a full rebuild. A later import tool may admit a legacy build's corpus-prefix outputs for stages whose package is unchanged between the build's commit and the tip.

**Consequences.** The first ingest after cutover computes its cell's corpus prefix once for the host. Stack A joins the self-serve wiring in the same host edit that adds the two roots.

### D-DRAFT-8 — 2026-11-09 — Builds keep their `out/` tree; the store is cloned into it by hard link or copy

**Context.** The baseline (§5) had builds hold manifests only and the pipeline resolve every path through the store. About thirty modules outside the pipeline read `<build>/out/<stage>/<cell>/` by layout. The batch containers mount the build and the store separately, and the kernel refuses a hard link across mounts.

**Decision.** A stage reuses a cell by fingerprint (the build's manifest, then the store); a stored output is cloned into the build's cell directory; a completed one is cloned into the store, made read-only and renamed into place. Clones are hard links on one mount and copies across mounts. Before a stage writes a cell, files sharing an inode or read-only are replaced by private copies. The vector store and unversioned variant stages are not stored.

**Why.** It gives the cross-stack sharing with no change outside the pipeline. Manifest-only builds were rejected for this step: a cross-module change that adds no sharing. Symlinks were rejected: their targets differ between host and container paths.

**Consequences.** On the host the store costs one copy per stored output. A re-run can no longer edit a promoted label through its hard links (it could before).

### D-DRAFT-9 — 2026-11-10 — A recipe is frozen in the build and steers the driver; models stay in env files unless a recipe names one

**Context.** Enrichment is not a pipeline stage: the cycle driver runs the SIRA lane, configured by the sira-batch env file and a taxonomy directory it sets per cell. The release set recorded only a scheme tag.

**Decision.** `customizations/recipes/<name>.json` names variant stages (taxonomy, enrich) and their parameters (`generation`, optional `model`, the enrich `prompt_scheme`, whether enrich reads the taxonomy). `cycle.sh start --recipe` (the runner passes the stack env's `NORA_RECIPE`) freezes it as `<build>/RECIPE.json`; the driver, the taxonomy fingerprint, promote and publish read it there. The release set records the recipe's name and digest and the build's stage fingerprints. A recipe's scheme wins over the runner config's. A build without a recipe runs and hashes as before.

**Why.** Freezing makes a build reproducible after the recipe file changes. Moving every model choice into recipes was rejected for now: endpoints and keys must stay in env files, and splitting a model from its endpoint would make a wrong pairing easy; the override is there when a recipe needs it. The validator is standard library only because the host-side driver and runner import it.

**Consequences.** A recipe without taxonomy unsets the taxonomy directory for the lane, so an env file cannot supply one. A generation bump changes the variant fingerprint; per-unit reuse keeps the cost of the re-stamp to what actually changed.

### D-DRAFT-10 — 2026-11-10 — Enrich is a driver-side stage: fingerprinted and recorded, not stored

**Context.** Enrichment runs in the SIRA lane under the cycle driver, not in the pipeline, and its outputs (SIRA cells) are large and already reused per requirement by SIRA's incremental promote.

**Decision.** `enrich` gets a stage spec in a driver table outside the pipeline's order: inputs parse and, when the recipe says enrich reads it, taxonomy; external the MNO's published document prompt (the lane's resolution rule); parameters the lane's model and the SIRA revision, plus the recipe's enrich parameters. `verify-enrich` records the fingerprint per completed cell before its gate, through `run_cli --stamp enrich`. Enrich outputs are never put in the stage store.

**Why.** Recording makes the release set say exactly how its Cells were built and lets a later redo key on the fingerprint. A pipeline stage for enrich was rejected: the lane's container, env file and gate are the driver's, and moving them would change every host step for no gain. Storing enrich outputs was rejected for now: they are the largest artifact and the sharing they would give across stacks needs equal recipes, which today's stacks do not have.

**Consequences.** A recipe without taxonomy gives enrich a fingerprint without the taxonomy input, so turning taxonomy on or off is a different output. A stamp failure stops the verify gate (`CYC-E071`).

### D-DRAFT-11 — 2026-11-10 — A served cell is redone by a per-cell generation at the corpus root, as a new ingest

**Context.** A served cell can only be rebuilt by hand today: the page refuses a served release, and the documents stage refuses an existing cell. With fingerprints, an unchanged cell is never recomputed, so a redo needs an input that changes on purpose.

**Decision.** `<corpus>/generations.json` holds a generation per cell. The page's Redo on a served ingest creates a new ingest row (`redo_of`) and a job with `params.redo`; the runner bumps the cell's generation and runs from `start` with a new build. A generation above 1 enters the cell's variant fingerprints only; generation 1 enters nothing. The original stays served until the redo is served; abandon and purge of a redo keep the documents.

**Why.** A per-cell value redoes one cell and nothing else; the recipe's `generation` would redo every cell of a stack on its next build. Redoing in place (re-running the served ingest's build) was rejected: the served set would lose its build and the rollback target its provenance. A generation outside the fingerprint was rejected: two outputs would share one identity.

**Consequences.** Redo needs the corpus root. A bump survives an abandoned redo; the next redo bumps again, which is harmless. The page also shows each stage's origin (found or computed) from the build's manifest, copied onto the ingest row by the runner.

### D-DRAFT-12 — 2026-11-10 — Store GC is by hand, keeps what anything on the host names, and refuses on doubt

**Context.** The store only grows; each code version and each redo adds entries.

**Decision.** `stage_store gc` keeps every entry named by a manifest under the given builds and labels roots or by a release set's `stage_fps` in the given registries, plus entries younger than a minimum age (24 hours) and entries under a live building marker. It is a dry run unless `--apply`. It refuses with no source, a missing source, or an unreadable manifest or registry.

**Why.** Removing an entry costs only a recompute, never an artifact, so the rule can be simple. Refusing on an unreadable source was chosen over skipping it: a skipped source could name anything. A scheduled GC was rejected for now: the host has no record of every stack's roots, so the person running it names them.

**Consequences.** A stack left off the command loses sharing, not data; since step 4 (D-DRAFT-14) its builds hold no copies, so they lose stored outputs too, which promote refuses on and a re-run recomputes. Nothing calls it automatically. A `--keep-from` directory holding no manifest is warned on, not refused: builds from before step 2 hold none, and refusing would block every stack that has them.

### D-DRAFT-13 — 2026-11-10 — A rollback is refused when its label is no longer the active set

**Context.** A flip done by hand left an earlier ingest row `served` while the registry served a newer set; its Roll back would have flipped to that row's previous label and dropped the newer set.

**Decision.** `run_rollback` reads the registry's active label and refuses when it differs from the ingest's label.

**Why.** Silently rolling back past a newer set loses it from serving with no record on the page. Repairing the stale row automatically was rejected: which row is right after a hand flip is the administrator's call.

**Consequences.** A row left stale by a hand flip keeps a Roll back button that refuses; the fix is in the registry or a new ingest.

### D-DRAFT-14 — 2026-11-11 — Builds hold manifests, not outputs; every reader asks a resolver

**Context.** Builds keep `out/` as clones of stored outputs (D-DRAFT-8). Across the container mounts a clone is a copy: about 342 MB per build, N copies for N parallel builds.

**Decision.** `locate(build, stage, cell)` answers where an output is: the build's `out/` when present, else the store entry the manifest names. Readers outside the pipeline move onto it, promote assembles labels through it, then the pipeline stops cloning. Supersedes D-DRAFT-8.

**Why.** A relink pass after each stage was rejected: every build still pays the copy and the compare, and N copies exist until it runs. One mount holding the store beside the builds was rejected: hard links stop at one filesystem, so builds on a second host could not share, and compose paths change.

**Consequences.** Labels keep their layout, so serving does not change. Builds from before the change stay readable through their `out/`. A re-run of a stored stage is a new fingerprint, never an edit; `link_view` goes, `privatize` stays for builds from before the change and for a taxonomy cell seeded from the store. A build depends on its store entries: GC must name its builds root, and promote refuses a cell named by the manifest and held nowhere. sira-batch mounts the store read-only. Taxonomy, whose per-plan reuse lived in the build, seeds a new fingerprint from the newest matching store entry.

### D-DRAFT-15 — 2026-11-11 — Extract reuses a document's IR by its source digest across store entries

**Context.** One changed document gives the cell a new extract fingerprint. Within a build, extract skipped a document whose IR was newer than its source; across builds nothing was reused, so every document was extracted again.

**Decision.** Extract records each IR's source sha256 in `.sources.json`. A cell computing under a new fingerprint takes the IR and images of each unchanged document from the cell's newest store entry whose code version, parameters and other externals are equal. `--force` takes nothing.

**Why.** A per-document store entry was rejected: the cell stays the unit every reader and the manifest know. Matching by modification time across builds was rejected: copies and new checkouts change it while the bytes stay equal.

**Consequences.** Entries stored before the change offer nothing. The taxonomy correction enters the graph and vector store fingerprints; it feeds no per-cell stage, so no stored output moves with it.

### D-DRAFT-16 — 2026-11-11 — Documents belong to the ingest that staged them; the corpus bindings are written under a lock

**Context.** A second stack's runner shares the requirements directory and the corpus `profiles.json`. Abandon removed a cell's documents unconditionally, and purge checked only its own stack's active set; two starts could each rewrite the bindings and lose the other's.

**Decision.** The runner records `documents_staged` when it copies a cell's documents. Abandon and purge remove them only with that record and while no registry on the host (its own and `peer_registry_roots`) serves the cell; an unreadable registry keeps them. `cycle.sh start` holds a host-wide `flock` from reading the bindings to writing them.

**Why.** One requirements directory per stack was rejected: the corpus is one set of documents, and two copies could drift. A marker file inside the cell was rejected: extract's fingerprint hashes every file there.

**Consequences.** An ingest row from before the record keeps its documents on abandon; they are removed by hand. A runner's config must list the other stacks' registries.

### D-DRAFT-17 — 2026-11-11 — Stack A keeps the single-prompt design as recipe `sira-generic`

**Context.** Stack A is the single-prompt arm of an A/B test (`scheme-v1`: SIRA's one document prompt, no taxonomy) against stack B's per-plan prompts with taxonomy (`scheme-v2`). Recipes could only express per-MNO prompts, with or without a taxonomy block.

**Decision.** A recipe parameter `params.enrich.prompt` (`per-mno` default, `generic`). Recipe `sira-generic`: enrich only, `prompt: generic`, `prompt_scheme: scheme-v1`. Under `generic` the prompts gate checks and publishes no per-MNO prompt, the lane unsets `NORA_SIRA_DOC_PROMPT_DIR`, and the enrich fingerprint's `doc_prompt` is `generic`.

**Why.** Recipe `sira-plain` (per-MNO prompts with an empty block) was rejected for stack A: it changes the prompt as well as dropping taxonomy, so the A/B no longer compares the designed arms. An env-only switch (unsetting the prompt dir by hand) was rejected: the gate and the fingerprint would not know.

**Consequences.** The generic path is one requirement per call, several times slower than batched. `generic` with `taxonomy: true` is invalid.

### D-DRAFT-18 — 2026-11-12 — The extractor switches enter extract's fingerprint when on

**Context.** `DOCLING_OCR` and `NORA_EXTRACT_TEXT_TABLES` change what extract produces and come from a stack's batch env file. Two stacks share the stage store, so a switch on one stack and off on the other would put different outputs under one fingerprint.

**Decision.** A switch that is on enters extract's externals (`extract_switches`); off or unset adds nothing.

**Why.** Adding every switch unconditionally was rejected: it would move every stored extract fingerprint for no change in output. `DOCLING_ARTIFACTS` stays out: it is a path, the same mount on every stack.

**Consequences.** Turning a switch on recomputes extract, and everything after it, for every cell that stack builds; turning it off again finds the earlier entries.
