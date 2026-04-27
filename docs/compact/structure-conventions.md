# Structure conventions

Defines the repository layout, what counts as a "module", and how Python visibility maps to `pub` / `internal` for the `regen-map` skill.

Populated during `project-init --retrofit` from the tech-stack answer in `project-init-interview.md` (topic 2). Edit as conventions evolve.

> **Transitional state (2026-04-27 onward)**: D-019..D-024 introduce a three-tier code reorg (`core/` + `customizations/` + `config/`) and a per-env runtime directory `<env_dir>`. The layout described below is the **target**. File moves are pending the development-phase reorg session; until that lands, source still lives under `src/` and tests under `tests/`. `drift-check` will surface this drift — the drift IS the migration work item, not unowned divergence.

## Top-level layout

```
<repo_root>/
├── core/                              # AI-generated; manual edits exceptional (D-019)
│   ├── src/                           # Python source — one MODULE.md per package
│   └── tests/                         # pytest suite (test_<module>.py per package)
├── customizations/                    # AI-scaffolded; humans complete / edit (D-019, D-024)
│   ├── profiles/                      # Human-curated document profiles
│   │   └── tests/                     # Co-located tests
│   ├── llm/                           # Proprietary-LLM provider boilerplate
│   │   └── tests/                     # Co-located tests
│   └── (other surfaces as identified)
├── config/                            # Per-module settings (D-021)
│   ├── llm.json                       # Default LLM provider, model, timeouts
│   ├── vectorstore.json               # Embedding model, distance metric, backend
│   ├── web.json                       # Web UI host / port / root_path / path_mappings
│   ├── pipeline.json                  # Default stage range, error-handling
│   ├── (one file per module needing config)
│   └── README.md                      # What each file controls, who reads it
├── environments/                      # Env config JSON (names → env_dir paths)
├── docs/compact/                      # COMPACT state files
├── .claude/                           # COMPACT skills (flat layout)
├── CLAUDE.md  README.md  CONTRIBUTING.md
├── requirements.txt
└── setup_env.sh  download_urls.txt  SETUP_OFFLINE.md
```

## Per-environment runtime layout

A single `<env_dir>` per environment contains all runtime artifacts, partitioned by purpose (D-022). The path is passed via CLI `--env-dir`, environment-config field, or Web UI form (FR-28..FR-30).

```
<env_dir>/
├── input/                             # Source documents (D-023)
│   └── <MNO>/<release>/*.pdf|*.docx|*.xlsx
├── out/                               # Pipeline outputs (replaces top-level data/)
│   └── extracted/  parsed/  resolved/  taxonomy/  standards/  graph/  vectorstore/
├── state/                             # Runtime SQLite DBs
│   ├── nora.db                        # Web UI job queue
│   └── nora_metrics.db                # Metrics
├── corrections/                       # Human profile / taxonomy overrides
├── reports/                           # Compact RPT / MET / FIX / QC outputs
└── eval/                              # User-supplied A/B Q&A xlsx
```

`environments/<name>.json` resolves an env name to its `env_dir`. The legacy `document_root` field is renamed `env_dir` for clarity.

## Module definition

Each directory under `core/src/` that contains an `__init__.py` is a core module. Its `MODULE.md` lives at `core/src/<module>/MODULE.md`. Tests live at `core/tests/test_<module>.py`. CLI entrypoints live at `core/src/<module>/<module>_cli.py` with a `main()` function.

Each top-level directory under `customizations/` is a customization module. Its `MODULE.md` lives at `customizations/<name>/MODULE.md`. Tests are co-located: `customizations/<name>/tests/`.

Nested packages (e.g. `core/src/web/routes/`) are treated as part of their parent module's contract unless their public surface is large enough to warrant splitting; in that case they may be promoted to first-class modules with their own MODULE.md. Flag the promotion as a hard-flag event and log a DECISIONS entry.

Files at the repo root (`setup_env.sh`, `requirements.txt`, etc.) are operational scripts or metadata, not modules — they do not get MODULE.md files and are not included in `MAP.md`.

## Visibility mapping

Python has no language-level `pub` / `internal` distinction. The convention for this project:

- **pub**: top-level identifiers (`class Foo`, `def bar`, module-level constants) whose name does not start with an underscore
- **internal**: identifiers whose name starts with a single underscore (`_helper`, `_InternalType`); nested inside a class unless re-exported; or absent from `__all__` when `__all__` is used
- **pub (curated)**: identifiers re-exported through the module's `__init__.py` — either via explicit imports or via `__all__`. When a module uses `__all__`, the curated list is authoritative for its public surface (see `core/src/corrections/__init__.py` for the canonical example).

When a module's `__init__.py` is empty, the public surface is the union of un-underscored top-level identifiers across all `.py` files in that module's directory.

## Protocol boundaries

The following Protocols are durable contracts; implementations live in dedicated modules and clients import the Protocol, not the implementation:

- `LLMProvider` — `core/src/llm/base.py`. Implementations: `OllamaProvider`, `MockLLMProvider` (in `core/src/llm/`); proprietary providers (in `customizations/llm/`).
- `EmbeddingProvider` — `core/src/vectorstore/embedding_base.py`. Implementation: `SentenceTransformerEmbedder`.
- `VectorStoreProvider` — `core/src/vectorstore/store_base.py`. Implementation: `ChromaDBStore`.

Changing a Protocol signature is a hard-flag event — log a `D-XXX` entry and switch back to architecture phase before implementing.

## Module doc schema

Each module has a `MODULE.md` with the following curated sections (plus a regen-only Structure section):

- **Owner** *(optional)* — single contributor owning the module; omit if shared or unassigned.
- **Purpose** — 1-2 sentences; cite FR / NFR IDs served (e.g. *"serves FR-3, FR-7"*).
- **Public surface** — signatures + semantics. Includes Protocol implementations callers rely on.
- **Invariants** — what callers can count on (threading, state, ordering, error-code contract).
- **Key choices** — each linked to DECISIONS.md by `[D-XXX]`.
- **Non-goals** — deliberate omissions.
- **Structure** — regen-only; bounded by `<!-- BEGIN:STRUCTURE -->` / `<!-- END:STRUCTURE -->`; never hand-edited.
- **Depends on** / **Depended on by** — links to other MODULE.md files.
- **Deferred** *(optional)* — planned-but-unbuilt behaviors for this module. Read by `drift-check` to classify matching items as `[DEFERRED]` instead of drift.

## Depends on / Depended on by — semantics

These sections capture **either** direct code imports **or** artifact / data consumption (e.g., a module reading JSON produced by another module). A module may legitimately declare a peer as a dependency without importing any of its symbols, when the coupling is through a shared on-disk artifact. `regen-map` and `drift-check` treat both forms as valid — a declared-but-not-imported edge is not flagged as drift on its own.

**Cross-tier edges** between `core/` and `customizations/` are legal in either direction (D-020). Cycles between the two tiers are not flagged as drift.

## Description source

Used by `regen-map` to generate per-file one-liners in the **Project File Structure** section of `MAP.md`.

- Python files (`*.py`): first line of the module docstring. If absent, no description.
- Shell scripts (`*.sh`): first line of the top comment block after the shebang. If absent, no description.
- Directories with a `MODULE.md`: first sentence of the **Purpose** section.
- Other files and directories: no automatic description (path-only row).

Rows are alphabetical within each directory. Files and directories intermix alphabetically.

## Retrofit skeleton sentinel

MODULE.md files seeded by `project-init --retrofit` begin with the marker `<!-- retrofit: skeleton -->`. While present, `close-session` treats curated-section edits as expected (not hard flags). Remove the sentinel once the MODULE.md is fully curated; from that point, normal audit rules apply.
