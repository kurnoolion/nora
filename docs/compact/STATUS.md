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
- 2026-04-27 Slice B executed (`<env_dir>` parameterization, FR-28..FR-30, D-022, D-023): `EnvironmentConfig` field/property/method renames (`document_root` → `env_dir`, aggressive per-partition methods); `PipelineContext.from_env` / `.standalone` threaded through `<env_dir>` / `out/` / `state/` / `corrections/` / `reports/` / `eval/`; `--env-dir` CLI flag; web runtime DBs under `<env_dir>/state/` (`WebConfig.env_dir` + `state_path()` / `jobs_db_path()` / `metrics_db_path()` helpers); extraction `infer_metadata_from_path` adapted for `<env_dir>/input/<MNO>/<release>/`; doc_type dropped from path inference per Q4 (defaults to "requirement"). 375 tests pass.
- 2026-04-27 Slice C executed (FR-1 XLSXExtractor): per-sheet heading + table-block extraction via openpyxl; registered as `.xlsx` extractor; 9 new tests with `pytest.importorskip("openpyxl")` gating. PDF extractor lazy-imports `fitz` / `pdfplumber` so the registry module loads on environments without pymupdf (matches stages.py pattern). Full suite at 384 passed, 2 skipped, 0 failed.
- 2026-04-27 `/drift-check dev-full` — 4 R/D-vs-I drifts resolved (all batch [a]: MODULE.md aligned to slice-B/C method renames). env Public surface + Invariant `correction_path` → `correction_file`; web Public surface `WebConfig.db_path` → `env_dir` + helpers; extraction Invariant drops PIL. Reorg fully landed and verified across all three layers.

## In progress

*(empty — no explicit work in flight)*

## Next

- Push 13 commits ahead of `origin/main` (slices A + B + C + drift-checks + close-sessions). Likely need to rebase or fast-forward once pushed.
- End-to-end pipeline run against a real `<env_dir>` on the work-laptop or PC (the 5 VZW LTE PDFs at `<env_dir>/input/VZW/Feb2026/`); captures the first concrete telemetry that resolves the "latency / throughput / memory NFRs not yet set" Flag.
- Web UI smoke test — start `python -m core.src.web.app`, exercise correction editor / pipeline form / metrics dashboard against the new `<env_dir>` layout (templates were updated but not user-tested).
- Optional `/drift-check requirements` pass to confirm FR-28..FR-30 are actually exercised by code (dev-full already verified MODULE.md ↔ code alignment).
- `/drift-check design` (later) to surface any code capabilities still lacking an owning FR / NFR.
- Revisit triggers on the three deferred items when they fire (see `core/src/web/MODULE.md` and `core/src/pipeline/MODULE.md` Deferred sections).

## Flags

- 2026-04-27 [requirements] Latency / throughput / memory NFRs not yet set; defer until first work-laptop full-pipeline run produces real telemetry, then promote thresholds to NFR entries in `requirements.md`.
- 2026-04-27 [requirements] KG sharding / persistence: networkx single-process graph fits v1 (~1k nodes, ~12k edges) but needs re-evaluation when 2nd MNO corpus is added. Revisit at start of post-v1 multi-MNO ingestion.
