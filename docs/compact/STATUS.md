# Status

**Active phase**: development
**Last updated**: 2026-04-27
**Last drift-check**: 2026-04-27 — mode: design — 9 drift(s) resolved (all batch [a]: MODULE.md → match requirements + DECISIONS), 0 deferred surfaced

## Done

- 2026-04-21 COMPACT scaffold retrofitted into existing codebase (project-init --retrofit); 16 Python packages seeded with MODULE.md skeletons; phase prompts generated.
- 2026-04-21 Curated all 16 MODULE.md skeletons (retrofit sentinels removed; commit 8395628).
- 2026-04-23 Flattened `.claude/skills/` layout so COMPACT slash commands are discoverable by Claude Code; user-global bundle source preserved at `~/.claude/compact-src/`.
- 2026-04-23 First `/drift-check dev-full` — 3 drifts deferred (2 in web, 1 in pipeline); Depends-on semantics clarified in `structure-conventions.md` to treat artifact coupling as a valid edge.
- 2026-04-27 Switched to requirements phase. Curated PROJECT.md (5-column Contributors with Validation channel, success criteria with ≥90% weighted-overall accuracy bar, scope clarified to single-MNO v1 + multi-MNO-ready schema, production reverse-proxy constraint added). Wrote `requirements.md` with 24 active FRs + 16 NFRs + 6 Deferred (compliance ×4 + test-case parser + DOC/XLS legacy extraction). Triaged all 7 Open questions to closure.
- 2026-04-27 Captured architectural reorg plan: FR-28..FR-30 added to `requirements.md` for `env_dir` / CLI parameterization; D-019..D-024 queued for next architecture-phase session (three-tier `core/` + `customizations/` + `config/` repo layout; per-env runtime directory containing `input/`, `out/`, `state/`, `corrections/`, `reports/`, `eval/`).
- 2026-04-27 Switched to architecture phase. Appended D-019..D-024 to `DECISIONS.md` covering three-tier code organization (`core/` + `customizations/` + `config/`), bi-directional `core ↔ customizations` dependency, one config file per module, per-env runtime directory `<env_dir>`, source layout `<env_dir>/input/<MNO>/<release>/`, and `customizations/` seeding (profiles + proprietary-LLM boilerplate). Rewrote `structure-conventions.md` with target layout + transitional banner. Updated CLAUDE.md Repository layout to flag the reorg in flight.

## In progress

*(empty — no explicit work in flight)*

## Next

- `/switch-phase development` to execute the reorg: `git mv` modules to `core/src/`, profiles to `customizations/profiles/`, proprietary-LLM boilerplate to `customizations/llm/`, `web/config.json` to `config/web.json`; update all imports and CLI module paths.
- Fold FR-1 XLSX extraction extension into the development-phase reorg session (both touch `core/src/extraction/`).
- After reorg lands, run `/drift-check requirements` against the new layout to confirm FR-28..FR-30 are satisfied and surface any other code/req gaps.
- Run `/drift-check design` to surface code capabilities that lack an owning FR / NFR.
- Revisit triggers on the three deferred items when they fire (see `src/web/MODULE.md` and `src/pipeline/MODULE.md` Deferred sections).

## Flags

- 2026-04-27 [requirements] Latency / throughput / memory NFRs not yet set; defer until first work-laptop full-pipeline run produces real telemetry, then promote thresholds to NFR entries in `requirements.md`.
- 2026-04-27 [requirements] KG sharding / persistence: networkx single-process graph fits v1 (~1k nodes, ~12k edges) but needs re-evaluation when 2nd MNO corpus is added. Revisit at start of post-v1 multi-MNO ingestion.
