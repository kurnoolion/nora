# CLAUDE.md

This file provides guidance to Claude Code (and Cline) when working with code in this repository.

## Project

**NORA** — Network Operator Requirements Analyzer. AI system combining a unified Knowledge Graph with targeted RAG for intelligent querying, cross-referencing, and compliance analysis of US MNO device requirement specifications across multiple MNOs (Verizon, AT&T, T-Mobile) and quarterly releases.

See `docs/compact/PROJECT.md` for the 1-page identity and `TDD_Telecom_Requirements_AI_System.md` for the canonical technical design.

## Session-start ritual

This project uses **COMPACT** — a portable scaffold for team AI-partnered software development. The AI scaffolding lives under `.claude/skills/compact/` and the durable state lives under `docs/compact/`.

At the start of every session, invoke `run the session-start skill` (or `/session-start`). It loads Tier 1 context (`PROJECT.md` / `STATUS.md` / `MAP.md` / the active phase prompt) and asks what you're working on. Re-invoke mid-session after Claude Code auto-compaction or any time context feels stale.

## Key skills

- `/session-start` — hydrate context at start of every session
- `/switch-phase <requirements | architecture | development>` — adopt the phase persona
- `/regen-map` — regenerate `MAP.md` + Structure sections when code structure changes
- `/drift-check <requirements | design | dev-full | dev-module <name> | all>` — audit for drift between requirements, design, and implementation
- `/close-session` — end-of-session ritual: triages decisions, updates STATUS, audits MODULE.md edits, proposes commit (this is the **only** place memory is made)
- `/project-init --re-init` — regenerate phase prompts after project-level changes (state files preserved)

## State files

- `docs/compact/PROJECT.md` — identity + Contributors table
- `docs/compact/STATUS.md` — active phase, done / in progress / next, flags
- `docs/compact/requirements.md` — FR / NFR / Deferred (authority for what the system must do)
- `docs/compact/MAP.md` — module table + Mermaid dependency graph (regenerated)
- `docs/compact/DECISIONS.md` — append-only ADR log
- `docs/compact/structure-conventions.md` — what's a module, visibility mapping
- `docs/compact/design-inputs/` — TDD, README, SESSION_SUMMARY, SETUP_OFFLINE (archival design inputs)
- `docs/compact/retrofit-snapshot.md` — archival scan of existing codebase at retrofit time
- `docs/compact/phases/{requirements,architecture,development}.md` — phase personas loaded by `/switch-phase`
- `src/<module>/MODULE.md` — per-module contracts (16 skeletons seeded by retrofit — see STATUS for curation progress)

## Current state

Retrofitted on 2026-04-21 via `/project-init --retrofit`. Active phase: **architecture**. 16 MODULE.md skeletons are seeded with `<!-- retrofit: skeleton -->` sentinels — curation is in progress. See `docs/compact/STATUS.md` for the current work list.

## Repository layout

- `src/` — Python source (16 packages; one MODULE.md per package)
- `tests/` — pytest suite (one `test_<module>.py` per package)
- `docs/compact/` — COMPACT state files
- `.claude/skills/compact/` — COMPACT skills (session-start, switch-phase, regen-map, drift-check, close-session, project-init)
- `data/` — extracted / parsed artifacts (gitignored)
- `environments/` — per-environment configs (gitignored except `.gitkeep`)
- `web/` — Web UI runtime state (config, job queue DB, metrics DB; gitignored)
- `profiles/` — document profiles (committed, human-editable JSON)
- Repo-root PDFs (LTE*.pdf, TDD*.pdf) — source docs, gitignored

## Conventions

- Python; no `pyproject.toml` — `requirements.txt` + `src/` layout with `__init__.py` per package
- Public surface: top-level identifiers without a leading underscore (plus `__init__.py` re-exports when `__all__` is used)
- CLI per module: `src/<module>/<module>_cli.py` with `main()` entrypoint
- Protocol-based abstractions: `LLMProvider`, `EmbeddingProvider`, `VectorStoreProvider`
- No proprietary document content in logs, error messages, compact reports, or test fixtures
