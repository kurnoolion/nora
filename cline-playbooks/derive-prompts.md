# Playbook: derive-prompts

**Purpose**: produce corpus-grounded SIRA prompts (`doc_requirement_*.txt`,
`query_requirement_*.txt`, `relevance_requirement_*.txt`) by reading the parsed
requirements corpus, classifying plans into subdomains, harvesting actual
discriminative vocabulary, and weighting the generated examples by subdomain
distribution.

Replaces the manual prompt-writing loop that produced LTE-EMM-biased v01 prompts.
Corpus-derived prompts pattern the LLM after what's actually present — so a
VoWiFi-heavy or Android-API-heavy corpus produces matching prompts automatically.

**Input**: an MNO to scope to (recommended — run once per MNO; strand
sira-enrichment-pe), plus an optional version slug (default `v02`).
Refuses to overwrite an existing version unless `--force` is passed.

**Per-MNO mode**: with an MNO given, only that MNO's cells are scanned
and the outputs are named `<stage>_requirement_<MNO>_<version>.txt`.
The DOC prompt is generated in the BATCHED shape (placeholders
`{taxonomy_block}` + `{requirements}`, req_id-keyed JSON output) —
see the SKILL.md step 7 contract. To cover the whole deployment, run
this playbook once per ingested MNO.

## Step 0 — resolve `env_dir` before reading the corpus

The SKILL.md references `<env_dir>` as the root of the parsed-corpus directory
tree, but doesn't tell you how to resolve it. Resolve it with this chain
BEFORE proceeding to Step 1 of the SKILL.md:

1. **User override.** If the user said something like "use env_dir `/path/to/env`",
   use that path. Skip the rest.
2. **`$ENV_DIR` environment variable.** Try `echo $ENV_DIR`. If non-empty,
   use it.
3. **`environments/*.json` in the repo root.** Each environment file has a
   top-level `env_dir` field. Read all files matching `environments/*.json`:
   - If exactly one file exists, use its `env_dir`.
   - If multiple files exist, list them with their `env_dir` values and ask
     the user which environment to operate on. Do NOT pick by guessing.
4. **Ask the user** with the explicit prompt:
   > "I could not resolve env_dir. Please tell me the absolute path of your
   > NORA environment directory (the one containing `out/parse/`)."

Once resolved, verify the directory exists and contains `out/parse/` with at
least one `*/tree.json` inside. If not, abort and report which check failed —
don't proceed to fall back to `sandbox/adapter/out/<dataset>/raw/corpus.jsonl`
silently. (The skill's Step 1 fallback rule still applies, but only AFTER you
confirm that the user knew the parsed corpus is unavailable and explicitly
wants to use the adapter output instead.)

Surface the resolved path in your first response so the user can correct it
before you spend time scanning hundreds of trees against the wrong corpus.

## Implementation reference

The procedure (read sources, classify plans, harvest vocab per subdomain,
generate prompt files, validate, report) is fully specified in:

  `.claude/skills/derive-sira-prompts/SKILL.md`

Follow that file's steps 1-9 verbatim. This playbook exists so the procedure is
discoverable from `cline-playbooks/` alongside the other corpus-work playbooks.
The single source of truth for the procedure stays in the SKILL.md — if you
need to update the steps (new subdomain, new vocab category), edit the SKILL.md
and re-run this playbook.

## Quick reference (don't re-derive — read SKILL.md for full detail)

The SKILL.md covers:

- **Step 1** — locate parsed corpus (`<env_dir>/out/parse/*/tree.json`, or the
  BEIR-shape `sandbox/adapter/out/<dataset>/raw/corpus.jsonl` fallback).
- **Step 2** — inventory plans by req_count.
- **Step 3** — sample 5-10 representative reqs per plan.
- **Step 4** — classify plans into a fixed taxonomy spanning two families:
  - Standards-body subdomains (LTE-EMM, 5G-NR, VoLTE/IMS, VoWiFi, eSIM, SMS, etc.)
  - Platform/app/UX subdomains (Android Platform API, Android Apps, UI/UX,
    Device HW/modem, Privacy/Security, Carrier services / RCS)

  Many MNO plans straddle both — record primary + secondary tags when so.
- **Step 5** — harvest subdomain-specific vocab via regex (spec citations,
  timers, message types, API class names, CarrierConfig keys, system
  properties, settings menu paths, chipset names, AT commands, etc.).
- **Step 6** — compute proportional weights for example slots.
- **Step 7** — generate prompts using current v01 files as templates;
  preserve placeholders (`{doc_text}`, `{query}`, `{document}`, `{max_n}`,
  `{{"keywords": [...]}}`, `{{"score": <int>}}`) byte-exactly.
- **Step 8** — write `sandbox/prompts/*_requirement_<version>.txt`.
- **Step 9** — report inventory + subdomain distribution + harvested vocab
  samples + next-step commands.

## Output paths

Per-MNO runs write to **`customizations/prompts/`** (committed
customization tier; see commit policy below). After completion, four
files exist:

- `customizations/prompts/doc_requirement_<MNO>_<version>.txt`
- `customizations/prompts/query_requirement_<MNO>_<version>.txt`
- `customizations/prompts/relevance_requirement_<MNO>_<version>.txt`
- `customizations/prompts/corpus_overview_<MNO>_<version>.txt` (standalone
  overview — consumed by NORA's taxonomy extractor as corpus context)

Legacy corpus-wide runs (no MNO) keep writing generic
`sandbox/prompts/<stage>_requirement_<version>.txt` files.

Each has a derivation-header comment naming the corpus, subdomain distribution,
and source paths.

**Commit policy (D-062 precedent):** per-MNO prompt files carry real MNO
names and harvested corpus vocabulary. Commit them ONLY on the work PC,
pushed to the company-internal remote — never from a checkout whose
remote is the public mirror.

## After this playbook finishes

Three follow-up steps the user runs (the playbook does NOT auto-trigger these):

1. **Install into SIRA clone:**
   ```
   bash sandbox/install_configs.sh
   ```
   (Note: `install_configs.sh` currently copies `v01` paths. If you bumped to
   v02, also edit the script's `cp` lines to reference the new file names,
   or update the hydra configs at `sandbox/sira_configs/{enrich,rerank}/nora.yaml`
   to point at the new prompts.)

2. **Re-run SIRA pipeline** (or just the stages whose prompts changed):
   - doc-enrich only — to verify subdomain vocab is now domain-appropriate
   - then query-enrich + rerank if doc-enrich looks right

3. **Validate via sira_debug:**
   ```
   python -m sandbox.sira_query.sira_debug phrases --filter <SUBDOMAIN>
   ```
   The phrases for a VoWiFi req should now contain `epdg`, `ipsec`, `ike_sa`,
   `swu` — not `nas_attach` / `emm_procedure`. Similarly Android-platform reqs
   should yield `telephonymanager`, `carrierconfig`, `subscriptionmanager`.

## Rules

- **Read-only on the parsed corpus.** Never modify `_tree.json` files.
- **Never invent vocabulary** — every term in the generated prompts must trace
  back to harvested corpus content. No speculative spec numbers, no API
  class names that aren't in the corpus.
- **Preserve placeholders byte-exactly.** Breaking any of them makes SIRA's
  hydra template expansion fail at runtime.
- **Don't auto-install** the new prompts into the SIRA clone. That's
  `sandbox/install_configs.sh`'s job, run explicitly by the user.
- **Don't auto-trigger a pipeline re-run.** doc-enrich is a multi-hour
  operation; the user must consent to running it.
- If the taxonomy table in the SKILL.md doesn't fit a plan in your corpus,
  propose a new subdomain in the summary and ask the user before writing.
