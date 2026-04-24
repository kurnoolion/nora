---
name: compact
description: Orient yourself in a COMPACT-equipped project. Shows the methodology overview, lists the sub-skills, and points to the standard flow. Read-only — modifies no files. Invoke when onboarding or when you want a refresher.
---

# COMPACT

**C**ontext · **M**emory · **P**rompt — **A**udited, **C**o-developed, **T**eam-locked.

A portable prompt/context/memory engineering framework for team-based AI-assisted software development. Works identically in Claude Code and Cline.

The name is load-bearing:
- **A compact** — a binding agreement. The markdown files in `docs/compact/` *are* the contract between human, AI, and team.
- **Compact** — tightly-packed, in lockstep. Keeps a team moving together when multiple people are partnered with AI on the same codebase.

## What COMPACT gives you

- **Phase discipline** — requirements / architecture / development as explicit lenses you switch between deliberately.
- **Shared rituals** — `session-start` hydrates context; `close-session` persists decisions, status, and audit findings as diff-reviewed changes.
- **Auditable code** — `MODULE.md` per module (curated contract + regen-generated structure), plus a regenerated `MAP.md` so AI-generated code is never a black box.
- **Immutable decision log** — `DECISIONS.md` anchors *why* choices were made; entries are superseded, never edited.
- **Contributors as first-class** — stakeholder interfaces (TPM, QA, domain expert, end user) are captured in `PROJECT.md` Contributors from Day 1 and drive architecture and tech-stack choices; contribution surfaces are modules, not admin-tool afterthoughts.

## The seven sub-skills

| Skill | When to invoke |
|---|---|
| `session-start` | Auto at session start (via `CLAUDE.md` / `.clinerules`). Hydrates context; asks what you're working on. |
| `project-init` | Once per project. Runs the 7-topic interview and scaffolds `docs/compact/`. `--retrofit` for existing codebases. |
| `switch-phase <phase>` | When shifting between `requirements` / `architecture` / `development`. |
| `close-session` | End of every session. Triages decisions, updates STATUS, proposes commit. Never auto-writes. Emits a soft `drift-check` nudge when layers were touched. |
| `regen-map` | When code structure changes. Updates MODULE.md Structure sections and `MAP.md` from code. |
| `drift-check <mode>` | When requirements, design, and implementation may have drifted apart. Modes: `requirements` / `design` / `dev-full` / `dev-module <name>` / `all`. Interactive; never auto-fixes. |
| `doctor` | Audit the scaffold itself for internal consistency. Read-only. Auto-invoked by `close-session` every session. |

## Standard flow for a new project

1. Copy the scaffold: `cp -r <scaffold-path>/compact/. .claude/skills/`
2. Add `CLAUDE.md` (or `.clinerules`) one-liner: *"At the start of any new conversation, invoke the `session-start` skill."*
3. Open a new session → `session-start` detects uninitialized state and offers `/project-init`.
4. `/switch-phase requirements` → iterate on `PROJECT.md`.
5. `/switch-phase architecture` → draft `MODULE.md` files doc-first.
6. `/switch-phase development` → implement against the contracts.
7. `/close-session` at the end of *every* session. This is where memory gets made.

## Behavior of this skill

**Read-only.** Prints this overview and exits.

If the user is in a COMPACT-equipped project and wants to start work, point them at `/session-start` (which should fire automatically via the CLAUDE.md/.clinerules one-liner). If they're not initialized yet, point them at `/project-init`.

Do not invoke other skills from here. Do not modify files. The point of this skill is orientation, not action.

## Further reading

- `README.md` in this scaffold — full reference and design principles.
- `COMPACT_Overview.md` — Marp deck walking through concepts, workflow, dry run, and dual-tool setup.
- `DECISIONS.md` in any initialized project — the project's immutable decision log.
