---
name: project-init
description: Initialize a new project's AI scaffolding. Optional preflight imports existing design artifacts into `docs/compact/design-inputs/` so the interview and phase prompts can reference them. Runs the 7-topic interview from the vendored meta-prompt, fuses answers with 3 base prompts to produce customized phase prompts (requirements/architecture/development), and scaffolds docs/compact/. Safe to decline. --re-init regenerates phase prompts only; never touches state files (design-inputs preserved). --retrofit adds a codebase-scan preflight for existing projects: detects languages, module boundaries, and public-surface candidates; seeds MODULE.md skeletons, a polyglot-aware structure-conventions.md, and an initial MAP.md; leaves the project at active phase architecture.
---

## Preconditions

The following must exist alongside this SKILL.md (typically `.claude/skills/project-init/` when installed in a project, or `skills/compact/project-init/` in the scaffold source repo):

- `base-prompts/00-swdev-project-customizer.md` — the meta-prompt
- `base-prompts/01-swdev-requirement-gathering.md`
- `base-prompts/02-swdev-architecture-design.md`
- `base-prompts/03-swdev-development-testing-debugging.md`
- `templates/` — skeletons for scaffolded state files

If any base prompt is missing, abort:

> "Base prompt(s) missing: \<list of missing files\>. Recovery: run `/project-init --fetch-latest` to re-vendor from `github.com/kurnoolion/prompts-lib`, or manually copy the four files in `base-prompts/` from that repo into this skill's `base-prompts/` directory."

## Arguments

- *(none)* — fresh init.
- `--re-init` — regenerate phase prompts only; do not touch state files.
- `--retrofit` — fresh init for a project that already has requirements, design, or code. Adds a codebase scan to the preflight, seeds MODULE.md skeletons, and leaves active phase at `architecture`. Mutually exclusive with `--re-init`.
- `--fetch-latest` — re-vendor base prompts from upstream before running.

## Procedure

### 1. Detect existing state

Check `docs/compact/phases/*.md`, `docs/compact/PROJECT.md`, `docs/compact/STATUS.md`.

- Fully present, no `--re-init` flag → abort with: "Project already initialized. Use `/project-init --re-init` to regenerate phase prompts without touching state."
- `--re-init` → load `docs/compact/project-init-interview.md` (if exists) as defaults.
- `--retrofit` and `docs/compact/` already populated → abort:

  > "`--retrofit` is for first-time init, but `docs/compact/` already exists. To regenerate phase prompts against current state without touching state files, use `/project-init --re-init`. To re-scan the codebase from scratch, first move or back up `docs/compact/` (preserving `DECISIONS.md`, `requirements.md`, and any other hand-edited content you want to keep), then re-run `/project-init --retrofit`."
- Otherwise → fresh init (greenfield or retrofit).

### 2. Preflight: import existing design artifacts (optional)

**Skip on `--re-init`.** Existing `docs/compact/design-inputs/` is preserved untouched.

Ask:

> "Do you have existing design artifacts for this project — design docs, product specs, architecture sketches, meeting notes? (These are common if you drafted something in Claude web, ChatGPT, or a doc tool before starting COMPACT.) Paste them inline or give me file paths — I'll copy them into `docs/compact/design-inputs/` as durable context for the interview and phase prompts. Reply `skip` if this is greenfield."

Handle the response:

- **`skip`** → proceed without creating `design-inputs/`.
- **File path(s)** → copy each into `docs/compact/design-inputs/`, preserving filename. Non-markdown formats (`.pdf`, `.docx`) are copied as-is; note to the user that the AI can read markdown/text natively and may have limited access to other formats.
- **Inline paste(s)** → write to `docs/compact/design-inputs/design-<NN>.md`, sequential (`design-01.md`, `design-02.md`, …). If the user provides a title for a paste, use it as the filename (sanitized).
- **Mix** → handle each accordingly; all land under `docs/compact/design-inputs/`.

These files are **inputs the AI consults during the interview and during requirements/architecture phases** — not outputs it edits. The user remains the owner.

### 3. Preflight: scan existing codebase (retrofit only)

**Run this step only on `--retrofit`.** Skip entirely on fresh greenfield init and on `--re-init`.

Scan the repo to produce a draft snapshot the user can confirm during the interview.

**3a. Detect languages.** Look for manifest files and source file extensions:

- `Cargo.toml` → Rust
- `go.mod` → Go
- `pyproject.toml` / `setup.py` / `requirements.txt` → Python
- `package.json` / `tsconfig.json` → TypeScript / JavaScript
- Others (Java `pom.xml`, Ruby `Gemfile`, etc.) → detect and include, but note that per-language scanning in v1 is best-effort outside the four primaries.

Present the list; ask the user to **confirm or add missed languages**. The confirmed list drives 3b and 3c.

**3b. Per-language module discovery.** For each confirmed language, apply the conservative default convention:

- **Rust:** each `src/<mod>/` directory (and each crate under a workspace) is a candidate module.
- **Go:** each directory under `pkg/`, `cmd/`, and `internal/` with `.go` files is a candidate module.
- **Python:** each directory containing `__init__.py` under the top-level package dir is a candidate module.
- **TypeScript:** each directory under `src/` (or `packages/<pkg>/src/` in a monorepo) that exports a public surface is a candidate module.

Collect candidate modules with their canonical MODULE.md paths.

**3c. Public-surface extraction.** For each candidate module, grep for the language's public-visibility markers (`pub fn` / `pub struct` / `pub trait` for Rust; capitalized top-level identifiers for Go; `export` / `export default` for TypeScript; non-underscore top-level names for Python). Capture top-level signatures only — not implementation details. These become commented-out *candidates* in the MODULE.md skeleton (step 8), never authoritative content.

**3d. Write `docs/compact/retrofit-snapshot.md`.** Archival artifact — produced once, read by the interview and by the customizer's phase-prompt generation, never updated by subsequent skills. Structure:

```markdown
# Retrofit snapshot

Generated YYYY-MM-DD by `project-init --retrofit`. Archival: do not update; re-run retrofit into a fresh project if the codebase shape changes materially.

## Detected languages
- <language> — <manifest file(s)>

## Candidate modules
### <language>
- `<path>/MODULE.md` — <N public items observed>

## Candidate public surface (per module)
### `<path>` (<language>)
- `<signature>`
- `<signature>`
```

### 4. Run the 7-topic interview

Read `base-prompts/00-swdev-project-customizer.md` for interview structure.

**Prefill sources (in precedence order, lower overrides higher):**

1. `docs/compact/design-inputs/*` (if present) — drafts for topics 1-3.
2. `docs/compact/retrofit-snapshot.md` (if `--retrofit`) — strongly seeds topic 2 (languages, module convention, visibility mapping) and flags topic 3 for stakeholder-vs-interface cross-check.
3. `docs/compact/project-init-interview.md` (if `--re-init`) — previous answers as defaults.

Present drafts alongside each topic's question; the user confirms, edits, or rejects. Never treat prefills as authoritative.

Present all 7 topics as a single batch:

1. **What we're building** — system description, core problem
2. **How we're building** — tech stack, frameworks, dependencies; **per-language** module convention + visibility mapping (feeds `structure-conventions.md`)
3. **Stakeholder map & contribution surfaces** — for every stakeholder (devs, TPMs, QA, domain experts, end users): role, technical comfort, contribution type, required interface, feedback loop. Drives Contributors table, architecture (surfaces as modules), and tech-stack choices.
4. **Domain constraints** — regulation, real-time needs, compliance, data sensitivity
5. **LLM access model** — runtime data / artifact visibility; restrictions if any
6. **Pain points** — common failures; what AI should catch
7. **Artifact preferences** — documentation, design, requirements formats

**Cross-topic checks during the interview:**

- If topic 3 names stakeholders needing a UI / form / review queue and topic 2's stack can't support it, surface the gap and resolve before generating phase prompts.
- **On `--retrofit`:** if the scan found UI / server / API code but topic 3 names no corresponding end-user or reviewer stakeholder, flag the mismatch — either the stakeholder is missing from the answer, or the code is serving a surface the team hasn't acknowledged.

### 5. Persist answers

Write answers to `docs/compact/project-init-interview.md` with section headings per topic. If `docs/compact/design-inputs/` is non-empty, add a top-level **Design inputs** section. If `docs/compact/retrofit-snapshot.md` exists, add a top-level **Retrofit snapshot** section referencing it. This file is the source of truth for `--re-init`.

### 6. Customize phase prompts

For each base prompt (`01-...`, `02-...`, `03-...`), follow **all rules** in `00-swdev-project-customizer.md` — Customization rules, Base-section → 5-section mapping, Contribution surfaces as first-class design, Progressive loading, Sibling skills, Observability scaling, EIP calibration, Design inputs wiring, Retrofit inputs wiring, and the per-phase wiring sections. The customizer is the single source of truth; this skill is the orchestrator that runs it.

Write customized prompts (~400-600 words each) to:

- `docs/compact/phases/requirements.md`
- `docs/compact/phases/architecture.md`
- `docs/compact/phases/development.md`

Each phase prompt must follow the 5-section schema:

```
**Persona**: 1-2 lines.
**Load when entering**: files to prioritize.
**Do**: bullets.
**Don't**: bullets.
**Artifacts**: what this phase produces.
**Exit criteria**: when to switch out.
```

### 7. Scaffold state files (fresh init only)

**Skip this step entirely on `--re-init`.** Never overwrite state files.

Copy from `templates/` and fill in project-specific bits:

| File | Greenfield content | Retrofit content |
|---|---|---|
| `docs/compact/PROJECT.md` | Skeleton; user fills during requirements phase | Skeleton, drafted from `design-inputs/` + `retrofit-snapshot.md` where possible; user refines |
| `docs/compact/STATUS.md` | `Active phase: requirements`, dated today; Next pre-populated with "Fill in PROJECT.md during requirements phase" | `Active phase: architecture`, dated today; Next pre-populated with "Fill MODULE.md skeletons module-by-module (remove `<!-- retrofit: skeleton -->` sentinel when each is curated)" |
| `docs/compact/DECISIONS.md` | Empty header + comment template | Same (reconstructed entries added in step 9 if user opts in) |
| `docs/compact/MAP.md` | Placeholder pointing at `regen-map` | Placeholder (regenerated in step 10) |
| `docs/compact/requirements.md` | Skeleton with FR / NFR / Deferred sections; user populates during requirements phase | Seeded from `design-inputs/` requirements-shaped files if any are present (candidate FR / NFR lines as comments under each section, never as authoritative entries). **Preserve existing IDs verbatim** if the inputs use them (e.g. `REQ-042` stays `REQ-042`); only new additions use `FR-N` / `NFR-N`. Empty skeleton otherwise. |
| `docs/compact/structure-conventions.md` | Derived from tech-stack answer (topic 2); for common stacks (Rust, Go, Python, TypeScript) produce a first draft; for polyglot or unusual stacks, scaffold with explicit prompts | **Polyglot-aware**: one section per confirmed language (step 3a), each with its own Module definition + Visibility mapping. If only one language, single-section format. |

### 8. Seed MODULE.md skeletons (retrofit only)

**Run only on `--retrofit`.** For each candidate module from step 3b:

Create `<module-path>/MODULE.md` with:

```markdown
<!-- retrofit: skeleton -->
# <module>

**Purpose**
TODO — retrofit skeleton; please fill in.

**Public surface**
<!-- Candidates observed in code (to be curated, not copied verbatim): -->
<!-- - <signature> -->
<!-- - <signature> -->
TODO

**Invariants**
TODO

**Key choices**
TODO

**Non-goals**
TODO

<!-- BEGIN:STRUCTURE -->
<!-- Regenerated by regen-map. Do not hand-edit. -->
<!-- END:STRUCTURE -->

**Depends on**
TODO — link peer MODULE.md files.

**Depended on by**
TODO — link peer MODULE.md files.
```

The `<!-- retrofit: skeleton -->` sentinel at the top is the grace marker. `close-session`'s hard-flag audit treats curated-section edits as expected (not flag-worthy) while this sentinel is present. The team removes the sentinel when the MODULE.md is fully curated; from that point, normal audit rules apply.

If a `MODULE.md` already exists at a candidate path, **do not overwrite**. Log it in the summary and move on.

### 9. Backfill reconstructed decisions (retrofit only, opt-in)

**Run only on `--retrofit`.** Ask once:

> "The codebase has choices already baked in (runtime / framework / storage / etc.). Anchor any as `DECISIONS.md` entries? Each one is marked `status: reconstructed` with today's date and notes that rationale was not captured at time of decision. You can edit rationale afterward."

If the user wants to proceed, surface observed choices from the scan (e.g., "uses tokio async runtime," "persists via sqlx + Postgres") as a numbered list. User marks each `log` / `skip`.

For each `log`, draft a DECISIONS entry:

```
## D-XXX — <one-line summary>

- **Date:** YYYY-MM-DD
- **Status:** reconstructed
- **Context:** Retrofit backfill — rationale not captured at time of decision.
- **Decision:** <the choice>
- **Consequences:** TODO — fill in from team knowledge.
```

Append approved entries to `docs/compact/DECISIONS.md`. Never invent rationale.

### 10. Regenerate MAP.md (retrofit only)

**Run only on `--retrofit`.** Invoke the `regen-map` skill. This produces the initial `docs/compact/MAP.md` from the seeded MODULE.md skeletons + code.

Surface regen-map's full output, including any drift reports or orphan flags.

### 11. Print summary

- Files created: `<list>`
- Interview saved to: `docs/compact/project-init-interview.md`
- **Design inputs imported**: `<count>` files in `docs/compact/design-inputs/` (if preflight ran).
- **Retrofit snapshot written**: `docs/compact/retrofit-snapshot.md` (retrofit only).
- **MODULE.md skeletons seeded**: `<count>` (retrofit only). Remove `<!-- retrofit: skeleton -->` sentinel from each once curated.
- **Reconstructed decisions**: `<count>` (retrofit only, if user opted in).
- **Review `docs/compact/structure-conventions.md`** — derived from your tech-stack answer; confirm or edit before the next `regen-map` run.
- **`docs/compact/requirements.md`** scaffolded — populate during the requirements phase (or curate retrofit-seeded candidates in place). `drift-check` uses this as the authority for what the system is supposed to do.
- Next step:
  - Greenfield: `/switch-phase requirements`
  - Retrofit: `/switch-phase architecture` (already set); begin curating MODULE.md skeletons.
- **Retrofit follow-ups (if applicable):**
  - If `design-inputs/` contains requirements documentation (PRDs, spec lists), `/switch-phase requirements` briefly to extract FR / NFR into `docs/compact/requirements.md` before or alongside architecture work. Retrofit does **not** auto-populate requirements.md from code — specs must be captured explicitly.
  - Once enough MODULE.md skeletons are curated to have real Public surface entries, run `/drift-check design`. It surfaces code capabilities that lack an owning FR / NFR in `requirements.md` — the gap a retrofit scan can't detect. Resolve each drift by adding the FR/NFR, marking it Deferred, or reverting the MODULE.md claim.

## Rules

- **Never overwrite** STATUS.md, PROJECT.md, DECISIONS.md, MAP.md, `docs/compact/requirements.md`, `docs/compact/structure-conventions.md`, any file under `docs/compact/design-inputs/`, or `docs/compact/retrofit-snapshot.md` on `--re-init`.
- **On `--re-init`, `structure-conventions.md` is preserved even if the topic 2 answer changed.** If the user's tech stack, module convention, or visibility mapping has materially shifted since last init, prompt them to edit `docs/compact/structure-conventions.md` by hand before the next `regen-map` run — `--re-init` regenerates phase prompts but not this file, because it may carry hand-edits the re-init can't safely reconcile.
- **Never overwrite an existing MODULE.md** during retrofit skeleton seeding.
- Never run the interview without explicit user confirmation.
- Never fetch base prompts from the network without `--fetch-latest`.
- `--retrofit` and `--re-init` are mutually exclusive; reject invocations that pass both.
- If the tech-stack answer is polyglot, `structure-conventions.md` gets one section per confirmed language. This is automatic on `--retrofit`; for greenfield polyglot, scaffold with explicit prompts.
- **Never invent decision rationale** during step 9. If the user can't answer `Consequences`, mark it `TODO`.
