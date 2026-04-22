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

### 5. Self-check

For each MODULE.md written, diff against the original. If **any byte outside the Structure markers changed**, REVERT the file and record the violation as a skill bug.

### 6. Print summary

- Structure sections updated: `<count>`
- DRAFT modules (architecture phase): `<list>`
- Drift detected: `<list: module, declared vs actual>`
- NEW modules needing MODULE.md: `<list>`
- ORPHANED MODULE.md: `<list>`
- MAP.md changed: yes/no
- Self-check reverts: `<list>` (should be empty; if not, treat as a skill bug)

Ask the user to review the diff before committing.

## Rules

- **Drift is a signal, not a trigger.** Never auto-fix drift.
- **Never create MODULE.md.** New modules are an architectural act; they require a phase switch.
- **Never delete MODULE.md.** Orphans are flagged for human decision.
- **Alphabetical determinism everywhere** — output must be byte-identical for byte-identical input.
- `structure-conventions.md` is required; do not attempt to guess module layout without it.
