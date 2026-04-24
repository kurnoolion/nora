# Structure conventions

Defines what counts as a "module" in this repository and how language-native visibility maps to `pub` / `internal` for the `regen-map` skill.

Populated during `project-init` from the tech-stack answer (or from `--retrofit` code scan). Edit as conventions evolve.

## Format

- **Single-language repos** use flat `## Module definition` and `## Visibility mapping` sections.
- **Polyglot repos** use one `## <language>` section per language (e.g. `## Rust`, `## TypeScript`), each containing its own `### Module definition` and `### Visibility mapping` subsections. `regen-map` iterates per language.

## Module definition

<!-- Single-language example: "Each top-level directory under src/ is a module. A module's MODULE.md lives at src/<module>/MODULE.md." -->

## Visibility mapping

<!--
Single-language example (Rust):
  - `pub` → pub
  - `pub(crate)` → internal
  - `pub(super)` → internal
  - (no qualifier) → internal
-->

## Polyglot example

<!--
Replace the two flat sections above with per-language sections when this repo uses more than one language. Example:

## Rust

### Module definition
Each directory under `src/` with `mod.rs` or named file is a module. MODULE.md at `src/<mod>/MODULE.md`.

### Visibility mapping
- `pub` → pub
- `pub(crate)` / `pub(super)` → internal
- (no qualifier) → internal

## TypeScript

### Module definition
Each directory under `packages/<pkg>/src/` is a module. MODULE.md at `packages/<pkg>/src/<mod>/MODULE.md`.

### Visibility mapping
- `export` / `export default` → pub
- non-exported → internal
- Interfaces implemented by exported types → pub

## Cross-language edges

User-authored. List edges that cross language boundaries (HTTP / FFI / IPC) that per-language scanners can't infer. `regen-map` reads this section verbatim into MAP.md.
-->

## Description source

<!--
Used by `regen-map` to generate per-file one-liners in the **Project File Structure** section of `MAP.md`. Fill in the per-language rule.

Single-language example (Python):
  - `*.py`: first line of the module docstring. If absent, no description.
  - `*.sh`: first line of the top comment block after the shebang. If absent, no description.
  - Directories with `MODULE.md`: first sentence of the Purpose section.
  - Other files / directories: no automatic description (path-only row).

For polyglot repos, put this subsection under each `## <language>` section with the language-appropriate rule
(e.g., Rust: `//!` crate-level doc comment; TypeScript: leading JSDoc block).

Rows are alphabetical within each directory; files and directories intermix alphabetically.
-->

## Module doc schema

Each module has `src/<module>/MODULE.md` (or per-language canonical path) with the following curated sections (plus a regen-only Structure section):

- **Owner** *(optional)* — single contributor owning the module; omit if shared or unassigned.
- **Purpose** — 1-2 sentences.
- **Public surface** — signatures + semantics. Includes trait / interface implementations callers rely on.
- **Invariants** — what callers can count on (threading, state, ordering).
- **Key choices** — each linked to DECISIONS.md by `[D-XXX]`.
- **Non-goals** — deliberate omissions.
- **Structure** — regen-only; bounded by `<!-- BEGIN:STRUCTURE -->` / `<!-- END:STRUCTURE -->`; never hand-edited.
- **Depends on** / **Depended on by** — links to other MODULE.md.
- **Deferred** *(optional)* — planned-but-unbuilt behaviors for this module. Populated by hand or by `drift-check`; read by `drift-check` to classify matching items as `[DEFERRED]` instead of drift.

## Retrofit skeleton sentinel

MODULE.md files seeded by `project-init --retrofit` begin with the marker `<!-- retrofit: skeleton -->`. While present, `close-session` treats curated-section edits as expected (not hard flags). Remove the sentinel once the MODULE.md is fully curated; from that point, normal audit rules apply.
