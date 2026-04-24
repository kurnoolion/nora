---
name: regen-map
description: Regenerate the Structure section of each MODULE.md and rebuild MAP.md from the current codebase. Touches only mechanically-derived content. Phase-aware: MODULE.md without code is [DRAFT] during architecture phase, [ORPHANED] otherwise. Flags drift and orphans without auto-resolving.
---

**Read-code-write-doc only.** Structure is derived from code on every run; this skill never reformats what already exists in MODULE.md.

Scope: whole repo by default. Optional arg: comma-separated module names to regenerate only those.

## Read-only inputs

- `docs/compact/structure-conventions.md` — **required**. Defines what a "module" is in this repo and how language-native visibility maps to `pub` / `internal`. For polyglot repos, one section per language. Abort if missing.
- `docs/compact/STATUS.md` — read `Active phase` for DRAFT vs ORPHANED semantics.
- All `MODULE.md` files (per the per-language canonical paths in structure-conventions).
- The codebase.

## Writeable surface — only these

- The Structure section of each MODULE.md, bounded by `<!-- BEGIN:STRUCTURE -->` and `<!-- END:STRUCTURE -->` markers.
- `docs/compact/MAP.md` (wholly derived).

## Never write

- Any other section of MODULE.md (Owner, Purpose, Public surface, Invariants, Key choices, Non-goals, Depends on / Depended on by, Deferred).
- `DECISIONS.md`, `PROJECT.md`, `STATUS.md`.
- Any code.

## Procedure

### 1. Preflight

Load `structure-conventions.md`. If missing, abort: "Run `/project-init` or create `docs/compact/structure-conventions.md` before running regen-map."

**Detect polyglot format.** If the file contains `## <language>` sections (e.g. `## Rust`, `## TypeScript`) each with `### Module definition` and `### Visibility mapping` subsections, treat as polyglot: iterate per language in step 2, applying that language's rules to its canonical module paths. Otherwise treat as single-language: apply the flat `## Module definition` and `## Visibility mapping` rules to all modules.

Also read any `## Cross-language edges` section verbatim for use in step 4.

Read `STATUS.md` to determine the active phase.

### 2. For each module M with a MODULE.md

Resolve M's language from its path (polyglot) or apply the single flat ruleset. Then:

a. Check whether M has corresponding code.

b. **If M has code:**
   - Scan code. Extract classes / structs / methods with visibility (pub / internal per that language's mapping). **Include trait / interface implementations** (e.g., `impl Clone for Store`, `impl Iterator for X`).
   - Render Structure deterministically:
     - Alphabetical by container; then alphabetical by member.
     - Each line: `name` — kind — visibility — 1-line purpose.
   - Replace content **between** the Structure markers. Leave adjacent bytes (including whitespace) byte-identical.
   - Compare declared **Public surface** in MODULE.md vs actual public items in code. **Record drift; do not edit.**

c. **If M has MODULE.md but no code:**
   - If active phase is `architecture`: mark `[DRAFT]`. **Skip Structure regeneration. Skip Public-surface drift check.** This is doc-first design intent.
   - Otherwise: mark `[ORPHANED]`. Record for summary. **Do NOT delete the file.**

### 3. Detect new modules

Code exists, no MODULE.md → record for summary. **Do NOT create MODULE.md.** In polyglot repos, apply each language's module-definition rule to its own scope.

### 4. Regenerate MAP.md

Write `docs/compact/MAP.md` with:

- **Single-language**: one module table, alphabetical.
- **Polyglot**: one `## <language>` subheading per language, each with its own alphabetical module table. Modules never cross tables.

Each table row:
  - Module name linked to its MODULE.md
  - Purpose (first sentence of MODULE.md's Purpose section)
  - Status marker: `[DRAFT]` / `[NEW]` / `[ORPHANED]` / none

- Mermaid flowchart: nodes alphabetical, edges derived from each module's **Depends on** section. In polyglot mode, annotate nodes with language tags (e.g. `auth[auth · rust]`).
  - Prefix every node **id** with `m_` (e.g. `m_graph[graph]`) so module names that collide with Mermaid reserved keywords — notably `graph`, `end`, `subgraph`, `class`, `default`, `style` — don't break the chart. The display label stays as the bare module name.
- If structure-conventions had a `## Cross-language edges` section, copy it verbatim into MAP.md as a `## Cross-language edges` section below the flowchart.
- Header note: "Generated YYYY-MM-DD by regen-map. Do not hand-edit."

### 5. Regenerate Project File Structure section

Append a `## Project File Structure` section to MAP.md (after the Mermaid flowchart and any Cross-language edges).

- **Walk** the repo from root. Apply these exclusions:
  - Anything matched by the repo's `.gitignore` (respect it; do not re-implement file-level ignore).
  - Hidden entries (leading `.`), except `.claude/` if the project explicitly lists it — default is skip.
  - Well-known build/cache noise: `__pycache__/`, `*.pyc`, `.DS_Store`, `node_modules/`, `venv/`, `.venv/`, `dist/`, `build/`, `target/`.
- **Describe** each kept entry using the **Description source** rule from `structure-conventions.md`:
  - Directory with a `MODULE.md` (at the language's canonical path) → first sentence of its Purpose section.
  - File → per-language rule (e.g. Python: first line of the module docstring; Shell: first line of the top comment block after the shebang). If the rule yields nothing, emit a path-only row.
  - Other directories / file types without a rule → path-only row.
- **Order** alphabetically within each directory; files and directories intermix alphabetically (deterministic).
- **Render** as a single fenced block using box-drawing characters (`├──`, `└──`, `│   `) rooted at the repo's directory name. Align description column with two spaces of padding after the longest rendered path. Descriptions use `#` comment prefix.
- **Introductory sentence**: `_Alphabetical, regenerated by regen-map. Directory descriptions come from MODULE.md Purpose; file descriptions come from the per-language description-source rule in structure-conventions.md._`
- If `structure-conventions.md` lacks a `Description source` subsection, emit the section header with a single line: `_Description source not defined in structure-conventions.md; populate it to enable this section._` — do not guess.

### 6. Self-check

For each MODULE.md written, diff against the original. If **any byte outside the Structure markers changed**, REVERT the file and record the violation as a skill bug.

MAP.md is wholly derived; no self-check needed beyond confirming the file was written.

### 7. Print summary

- Structure sections updated: `<count>`
- DRAFT modules (architecture phase): `<list>`
- Drift detected: `<list: module, declared vs actual>`
- NEW modules needing MODULE.md: `<list>`
- ORPHANED MODULE.md: `<list>`
- Project File Structure rows: `<count>` (`<count-with-descriptions>` described, `<count-path-only>` path-only)
- MAP.md changed: yes/no
- Self-check reverts: `<list>` (should be empty; if not, treat as a skill bug)

Ask the user to review the diff before committing.

## Rules

- **Drift is a signal, not a trigger.** Never auto-fix drift.
- **Never create MODULE.md.** New modules are an architectural act; they require a phase switch.
- **Never delete MODULE.md.** Orphans are flagged for human decision.
- **Alphabetical determinism everywhere** — output must be byte-identical for byte-identical input.
- `structure-conventions.md` is required; do not attempt to guess module layout without it.
