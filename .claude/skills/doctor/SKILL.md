---
name: doctor
description: Audit the COMPACT scaffold for internal consistency. Runs deterministic checks on schema authorities (generative from README), stale references, skill inventory, step monotonicity + step-reference resolution, tool-neutral framing, path canonicalization, and cross-file reference resolution. Read-only; never auto-fixes. Invoked unconditionally by close-session every session; also runnable manually when maintaining the scaffold.
---

**Read-only.** Checks, reports, exits. Fixes are the user's job.

## Scope

Audits the **scaffold root** — the parent directory of this SKILL.md's directory.

- If this file lives at `<repo>/skills/compact/doctor/SKILL.md`, root is `<repo>/skills/compact/`.
- If this file lives at `<project>/.claude/skills/doctor/SKILL.md`, root is `<project>/.claude/skills/`.

Some checks (notably Check 8) are source-repo-only and auto-skip in installed-project context.

## Checks

Run all. Never stop early on a failure. Report at the end.

### 1. Stale string inventory

Grep scaffold root for `TRAINING.md`, `docs/ai/`, `RISKS.md`.

- **Pass:** no matches, except self-references inside this SKILL.md.
- **Fail:** list each `file:line`.

### 2. MODULE.md schema quorum

**Generative.** `README.md`'s MODULE.md schema example block is the canonical source. Extract the bold section names (lines matching `**<Name>**` inside that code block) at runtime — that's the authoritative list every other authority must agree on. Track which extracted names are marked optional in README (via an inline HTML comment containing the word "optional", or parenthetical "*(optional)*" markup).

Then verify every extracted section name appears in the other four authorities:

- `project-init/base-prompts/00-swdev-project-customizer.md` — artifacts table row for `src/<module>/MODULE.md`.
- `project-init/templates/structure-conventions.md` — "Module doc schema" section.
- `close-session/SKILL.md` — Step 4 "Diff curated sections" parenthetical list.
- `regen-map/SKILL.md` — "Never write" list.

- **Pass:** every extracted section name is named in each authority. Names flagged optional in README may be marked optional elsewhere; others must be named unconditionally.
- **Fail:** list `(file, missing sections)` per authority.
- **Fail (README parse):** if README's schema example block is missing, has no bold section names, or the fence boundaries can't be located, report it — doctor can't validate downstream authorities without a canonical source.

### 3. Interview topic-label agreement

Compare the primary label (first 2-3 words) of each of the 7 topics in:

- `project-init/SKILL.md` — Step 4 interview list.
- `project-init/base-prompts/00-swdev-project-customizer.md` — "Interview me about" list.

- **Pass:** matching primary label for all 7 topics.
- **Fail:** list `(topic N, label_skill, label_customizer)` for each mismatch.

### 4. Skill inventory parity

Count directories with a `SKILL.md` under scaffold root. Cross-reference claims in:

- `README.md` — "A set of N skills..." statement and Skills table row count.
- `compact/SKILL.md` — "The N sub-skills" statement and sub-skills table row count. **Sub-skill count excludes the `compact` entry-point itself** (sub-skills = total - 1).
- `COMPACT_Overview.md` — Skills-at-a-glance table row count.
- `COMPACT_Overview.md` — directory layout code block (inside the `.claude/skills/` subtree).

- **Pass:** all four sources agree (accounting for the `compact` entry-point exclusion in sub-skill count).
- **Fail:** show actual vs claimed per source.

### 5. Step monotonicity in multi-step skills

For each skill whose procedure uses `### N. <title>` numbering (`close-session`, `regen-map`, `project-init`, `session-start`, `drift-check`), extract heading numbers.

- **Pass:** monotonic sequence 1, 2, 3, ..., K with no gaps or duplicates.
- **Fail:** show the gap, duplicate, or non-monotonic section per skill.

### 6. Tool-neutral narrative

Grep all `.md` files in scaffold root for `Claude's ` (possessive with trailing space).

- **Pass:** zero matches outside Claude-specific sections. Allowed context: slide headings or sections explicitly naming Claude Code (`Setup on Claude Code`, `Cline gotchas`, dual-tool comparisons). This check does not auto-classify — every hit gets reported.
- **Fail:** list each `file:line` for human review.

### 7. Path canonicalization

Grep for `docs/` paths that don't start with `docs/compact/`.

- **Pass:** no violations. Every `docs/X` reference has `X = compact` (or a subpath under compact).
- **Fail:** list each `file:line` + the offending path.

### 8. Symlink health (source-repo only, optional)

If scaffold root resolves to `<repo>/skills/compact/` **and** `<repo>/.claude/skills/` exists as a directory, check that `<repo>/.claude/skills/compact` is a symlink resolving to a path containing `SKILL.md`.

The symlink is optional in source-repo context — utils isn't a COMPACT-using project, and the symlink only matters if someone has set up local skill discovery inside the source repo. When `<repo>/.claude/skills/` doesn't exist, skip.

- **Pass:** `<repo>/.claude/skills/` exists and the `compact` symlink resolves to an existing SKILL.md.
- **Skip:** scaffold root doesn't match the source-repo layout (installed context), **or** `<repo>/.claude/skills/` doesn't exist (symlink not set up — fine).
- **Fail:** `<repo>/.claude/skills/` exists but the `compact` symlink is broken or missing inside it.

### 9. Sibling-skill references resolve

Extract `/<skill-name>` references from all SKILL.md files and base prompts. For each unique skill name, verify `<scaffold-root>/<skill-name>/SKILL.md` exists.

- **Pass:** every referenced skill has a SKILL.md.
- **Fail:** list `(file:line, dangling-ref)`.

Ignore `/project-init --re-init` flag syntax and Claude Code built-in slash commands (`/help`, `/clear`, etc.).

### 10. Directory-layout parity

Extract the set of skill names from three sources:

- `README.md` Skills table (column 1).
- `COMPACT_Overview.md` directory layout code block (entries inside `.claude/skills/`).
- `COMPACT_Overview.md` Skills-at-a-glance table (column 1).

- **Pass:** identical set across all three.
- **Fail:** show set differences per source.

### 11. Step-reference resolution

Back-references of the form `Step N` / `step N` that cite a step in a *different* file (self-references are covered by Check 5's monotonicity pass). Typical sites: Check 2's authority list (`close-session/SKILL.md — Step 4 ...`); Check 3's authority list (`project-init/SKILL.md — Step 4 interview list`); narrative cross-refs in any SKILL.md.

For each match where a citing line names a target file and a specific step number:

- Resolve `<target-file>` to its path under scaffold root.
- Verify a `### N.` heading exists in that file.

- **Pass:** every cited step resolves to an existing numbered section.
- **Fail:** list `(citing-file:line, target-file, requested-step-N, available-steps)` per mismatch.

Ignore matches inside HTML comments and inside fenced example blocks — those aren't authority claims. Also ignore the literal strings in doctor's own check-numbering narrative (this file's own `### N.` headings are the monotonicity subject of Check 5).

### 12. Retrofit snapshot scope

`docs/compact/retrofit-snapshot.md` is produced once by `project-init --retrofit` and is archival — no scaffold skill should take an ongoing dependency on it at runtime. Grep scaffold root for `retrofit-snapshot.md`.

- **Pass:** all matches live inside one of: `project-init/SKILL.md` (the file it produces), `project-init/base-prompts/00-swdev-project-customizer.md` (wires phase prompts to read it), `README.md` / `COMPACT_Overview.md` (documentation), `doctor/SKILL.md` (this check).
- **Fail:** list `(file:line)` for unexpected references — e.g. `session-start`, `close-session`, or `regen-map` naming retrofit-snapshot as a load target. The retrofit path has then bled into steady-state operation.

## Output format

```
COMPACT Doctor — Scaffold Consistency Audit
Root: <resolved path>

✓ 1. Stale string inventory
✓ 2. MODULE.md schema quorum — 5 authorities, all consistent
✗ 3. Interview topic-label agreement
    Topic 3 label mismatch:
      project-init/SKILL.md:40            → "Stakeholder map & contribution surfaces"
      00-swdev-project-customizer.md:17   → "Team & contribution structure"  (stale)
✓ 4. Skill inventory parity — 8 skills, 7 sub-skills, all claims consistent
...

Summary: 10 passed, 1 failed. Review failures above.
```

Exit status conceptually: failures present → user must address (or defer via `STATUS.md` Flags) before committing scaffold changes.

## Rules

- **Read-only.** Never edit a file. Never suggest a fix in this skill; surfacing drift is the full contract.
- Run every check. Never short-circuit on a failure.
- On ambiguity (Check 6 tool-neutrality), report for human review; do not classify.
- Prefer `<file>:<line>` over dumping full matches.
- If scaffold root contains additional skill directories not known to this SKILL.md, treat them as valid and include them in inventory counts — never flag as unknown.
- This skill is expected to evolve as the scaffold evolves. New invariants get added here, not enforced by scattered prompt edits elsewhere.
