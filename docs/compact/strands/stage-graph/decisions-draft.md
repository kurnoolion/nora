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
