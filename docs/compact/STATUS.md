# Status

**Active phase**: development
**Last updated**: 2026-04-27
**Last drift-check**: 2026-04-27 — mode: dev-full — 4 drift(s) resolved (all batch [a]: MODULE.md → match code post-slice-B/C), 0 deferred surfaced

## Done

- 2026-04-21 COMPACT scaffold retrofitted into existing codebase (project-init --retrofit); 16 Python packages seeded with MODULE.md skeletons; phase prompts generated.
- 2026-04-21 Curated all 16 MODULE.md skeletons (retrofit sentinels removed; commit 8395628).
- 2026-04-23 Flattened `.claude/skills/` layout so COMPACT slash commands are discoverable by Claude Code; user-global bundle source preserved at `~/.claude/compact-src/`.
- 2026-04-23 First `/drift-check dev-full` — 3 drifts deferred (2 in web, 1 in pipeline); Depends-on semantics clarified in `structure-conventions.md` to treat artifact coupling as a valid edge.
- 2026-04-27 Switched to requirements phase. Curated PROJECT.md (5-column Contributors with Validation channel, success criteria with ≥90% weighted-overall accuracy bar, scope clarified to single-MNO v1 + multi-MNO-ready schema, production reverse-proxy constraint added). Wrote `requirements.md` with 24 active FRs + 16 NFRs + 6 Deferred (compliance ×4 + test-case parser + DOC/XLS legacy extraction). Triaged all 7 Open questions to closure.
- 2026-04-27 Captured architectural reorg plan: FR-28..FR-30 added to `requirements.md` for `env_dir` / CLI parameterization; D-019..D-024 queued for next architecture-phase session (three-tier `core/` + `customizations/` + `config/` repo layout; per-env runtime directory containing `input/`, `out/`, `state/`, `corrections/`, `reports/`, `eval/`).
- 2026-04-27 Switched to architecture phase. Appended D-019..D-024 to `DECISIONS.md` covering three-tier code organization (`core/` + `customizations/` + `config/`), bi-directional `core ↔ customizations` dependency, one config file per module, per-env runtime directory `<env_dir>`, source layout `<env_dir>/input/<MNO>/<release>/`, and `customizations/` seeding (profiles + proprietary-LLM boilerplate). Rewrote `structure-conventions.md` with target layout + transitional banner. Updated CLAUDE.md Repository layout to flag the reorg in flight.
- 2026-04-27 `/drift-check design` batch — 9 R-vs-D drifts resolved (all direction [a]: MODULE.md → match requirements + DECISIONS). All 16 MODULE.md updated with FR/NFR citations and D-019..D-024 links; path conventions renamed `<doc_root>` → `<env_dir>` per D-022; extraction MODULE.md now commits to XLSX per FR-1.
- 2026-04-27 Slice A three-tier reorg executed (D-019..D-024) — 5 stages: A.1 file moves (src→core/src, tests→core/tests, profiles→customizations/profiles, web/config.json→config/web.json) + customizations/llm/ seed (`ProprietaryLLMProvider` stub per D-024 option ii); A.2 import rewrite (279 imports across 75 .py files); A.3 CLI rewrite (164 invocations across 21 docs/scripts); A.4 web config path resolution (PROJECT_ROOT depth fix + `config/web.json`); A.5 CLAUDE.md banner removed + MAP.md regenerated for new tree. 375 tests pass.

## In progress

*(empty — no explicit work in flight)*

## Next

- Slice B — `<env_dir>` parameterization (FR-28..FR-30): rename `document_root` → `env_dir` in `EnvironmentConfig` field + property + env-config JSON schema; thread `env_dir` through `PipelineContext` and stage path resolvers; partition outputs into `out/`/`state/`/`corrections/`/`reports/`/`eval/` under `<env_dir>`; move runtime DBs from `web/` to `<env_dir>/state/`; update `infer_metadata_from_path` for `<env_dir>/input/<MNO>/<release>/` layout.
- Slice C — Implement `XLSXExtractor` in `core/src/extraction/` per FR-1; register in extractor registry; co-locate tests under `core/tests/`.
- After B + C land: `/drift-check dev-full` to confirm code matches MODULE.md (currently MODULE.md commits to env_dir + XLSX while code still uses document_root + no XLSX); `/drift-check requirements` to verify FR-28..FR-30 are satisfied.
- `/drift-check design` (later) to surface any code capabilities still lacking an owning FR / NFR.
- Revisit triggers on the three deferred items when they fire (see `core/src/web/MODULE.md` and `core/src/pipeline/MODULE.md` Deferred sections).

## Flags

- 2026-04-27 [requirements] Latency / throughput / memory NFRs not yet set; defer until first work-laptop full-pipeline run produces real telemetry, then promote thresholds to NFR entries in `requirements.md`.
- 2026-04-27 [requirements] KG sharding / persistence: networkx single-process graph fits v1 (~1k nodes, ~12k edges) but needs re-evaluation when 2nd MNO corpus is added. Revisit at start of post-v1 multi-MNO ingestion.
