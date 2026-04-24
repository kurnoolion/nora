# Structure conventions

Defines what counts as a "module" in this repository and how Python visibility maps to `pub` / `internal` for the `regen-map` skill.

Populated during `project-init --retrofit` from the tech-stack answer in `project-init-interview.md` (topic 2). Edit as conventions evolve.

## Module definition

Each directory under `src/` that contains an `__init__.py` is a module. A module's `MODULE.md` lives at `src/<module>/MODULE.md`.

Tests for a module live at `tests/test_<module>.py` (pytest). CLI entrypoints live at `src/<module>/<module>_cli.py` with a `main()` function.

Nested packages (e.g. `src/web/routes/`) are currently treated as part of their parent module's contract unless their public surface is large enough to warrant splitting; in that case they may be promoted to first-class modules with their own MODULE.md. Flag the promotion as a hard-flag event and log a DECISIONS entry.

Files at the repo root (`create_presentation.py`, `update_presentation.py`, `setup_env.sh`) are operational scripts, not modules — they do not get MODULE.md files and are not included in `MAP.md`.

## Visibility mapping

Python has no language-level `pub` / `internal` distinction. The convention for this project:

- **pub**: top-level identifiers (`class Foo`, `def bar`, module-level constants) whose name does not start with an underscore
- **internal**: identifiers whose name starts with a single underscore (`_helper`, `_InternalType`); nested inside a class unless re-exported; or listed in the module's `__init__.py` `__all__` as absent when `__all__` is used
- **pub (curated)**: identifiers re-exported through the module's `__init__.py` — either via explicit imports or via `__all__`. When a module uses `__all__`, the curated list is authoritative for its public surface (see `src/corrections/__init__.py` for the canonical example).

When a module's `__init__.py` is empty, the public surface is the union of un-underscored top-level identifiers across all `.py` files in that module's directory.

## Protocol boundaries

The following Protocols are durable contracts; implementations live in dedicated modules and must not be imported directly by client code (clients import the Protocol, not the implementation):

- `LLMProvider` — `src/llm/base.py`; implementations: `OllamaProvider`, `MockLLMProvider`
- `EmbeddingProvider` — `src/vectorstore/embedding_base.py`; implementation: `SentenceTransformerEmbedder`
- `VectorStoreProvider` — `src/vectorstore/store_base.py`; implementation: `ChromaDBStore`

Changing a Protocol signature is a hard-flag event — log a `D-XXX` entry and switch back to architecture phase before implementing.

## Module doc schema

Each module has `src/<module>/MODULE.md` with the following curated sections (plus a regen-only Structure section):

- **Owner** *(optional)* — single contributor owning the module; omit if shared or unassigned.
- **Purpose** — 1-2 sentences; cite FR / NFR IDs served (e.g. *"serves FR-3, FR-7"*).
- **Public surface** — signatures + semantics. Includes Protocol implementations callers rely on.
- **Invariants** — what callers can count on (threading, state, ordering, error-code contract).
- **Key choices** — each linked to DECISIONS.md by `[D-XXX]`.
- **Non-goals** — deliberate omissions.
- **Structure** — regen-only; bounded by `<!-- BEGIN:STRUCTURE -->` / `<!-- END:STRUCTURE -->`; never hand-edited.
- **Depends on** / **Depended on by** — links to other MODULE.md.
- **Deferred** *(optional)* — planned-but-unbuilt behaviors for this module. Read by `drift-check` to classify matching items as `[DEFERRED]` instead of drift.

## Depends on / Depended on by — semantics

These sections capture **either** direct code imports **or** artifact/data consumption (e.g., a module reading JSON produced by another module). A module may legitimately declare a peer as a dependency without importing any of its symbols, when the coupling is through a shared on-disk artifact. `regen-map` and `drift-check` treat both forms as valid — a declared-but-not-imported edge is not flagged as drift on its own.

## Description source

Used by `regen-map` to generate per-file one-liners in the **Project File Structure** section of `MAP.md`.

- Python files (`*.py`): first line of the module docstring. If absent, no description.
- Shell scripts (`*.sh`): first line of the top comment block after the shebang. If absent, no description.
- Directories with a `MODULE.md`: first sentence of the **Purpose** section.
- Other files and directories: no automatic description (path-only row).

Rows are alphabetical within each directory. Files and directories intermix alphabetically.

## Retrofit skeleton sentinel

MODULE.md files seeded by `project-init --retrofit` begin with the marker `<!-- retrofit: skeleton -->`. While present, `close-session` treats curated-section edits as expected (not hard flags). Remove the sentinel once the MODULE.md is fully curated; from that point, normal audit rules apply.
