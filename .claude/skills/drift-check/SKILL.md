---
name: drift-check
description: Detect and resolve drift across the R/D/I layers — requirements (docs/compact/requirements.md) vs design (MODULE.md curated sections + DECISIONS entries) vs implementation (code). Four modes (requirements / design / dev-full / dev-module) plus `all`. Interactive per-drift resolution — user decides which layer changes. Never auto-fixes; never auto-cascades. Deferred items are surfaced separately, not flagged as drift.
---

**Judgment skill.** Semantic comparison across the three layers; interactive resolution; writes only after the user resolves each drift and approves the diff.

## Scope — what counts as R, D, I

- **Requirements** — `docs/compact/requirements.md` (FR / NFR / Deferred sections).
- **Design** — `src/**/MODULE.md` curated sections (Owner, Purpose, Public surface, Invariants, Key choices, Non-goals, Depends on / Depended on by, Deferred) **plus** `docs/compact/DECISIONS.md` entries (all ADRs are treated as architecture-level for drift comparison; COMPACT does not currently tag entries by phase).
- **Implementation** — code under `src/` (and any per-language equivalent surfaced by `structure-conventions.md`).

## Arguments

- *(none)* — prints usage + the last-run line from STATUS.md, exits.
- `requirements` — session discussion / recent commits vs `requirements.md`.
- `design` — design artifacts vs `requirements.md`.
- `dev-full` — all modules' code vs their `MODULE.md` + relevant ADRs.
- `dev-module <name>` — one module's code vs its `MODULE.md`.
- `all` — runs `requirements` → `design` → `dev-full` in sequence. User approves descent at each step; never auto-cascades.

## Read-only inputs

- `docs/compact/requirements.md` — **required** for all modes except `dev-module`.
- `docs/compact/STATUS.md` — read last-drift-check marker; read active phase.
- `docs/compact/PROJECT.md` — for identity context (not compared against).
- `docs/compact/DECISIONS.md` — design mode reads all ADR entries.
- All `MODULE.md` files relevant to the mode.
- The codebase (for `dev-full` / `dev-module`).
- `docs/compact/structure-conventions.md` — for per-language module resolution in `dev-full`.

If `requirements.md` is missing for a mode that needs it, abort:

> "`docs/compact/requirements.md` not found. It's produced during the requirements phase. For new projects, run `/switch-phase requirements` and populate it. For projects that pre-date the `requirements.md` artifact, copy the template from `.claude/skills/project-init/templates/requirements.md` to `docs/compact/requirements.md` and populate — `--re-init` preserves state files and will not create it."

## Writeable surface

- `requirements.md` — only on user-approved edits resolving drift.
- `src/**/MODULE.md` curated sections — only on user-approved edits resolving drift.
- `docs/compact/STATUS.md` — update the last-drift-check marker (see step 6) and append any deferred items the user newly chose to mark.
- **Code** — only when the user chooses "update implementation to match design." Shown as a diff; user confirms.
- `docs/compact/DECISIONS.md` — only when resolving drift by capturing a new ADR (e.g. "design changed — log why"). Appends only; never edits existing entries.

## Never write

- New `MODULE.md` files. If a module's code has no MODULE.md, that's a finding for `regen-map` / architecture phase, not drift-check.
- Existing `DECISIONS.md` entries (append-only log is preserved).
- Any file outside the writeable surface above.

## Procedure

### 1. Preflight

Parse the mode argument.

**Empty** → print:

```
drift-check — usage
  /drift-check requirements         — session / recent commits vs requirements.md
  /drift-check design               — design artifacts vs requirements.md
  /drift-check dev-full             — all modules' code vs MODULE.md
  /drift-check dev-module <name>    — one module vs its MODULE.md
  /drift-check all                  — R → D → I, with approval at each descent

Last run: <marker from STATUS.md, or "never">
```

…and exit.

**Unknown mode** (not one of `requirements` / `design` / `dev-full` / `dev-module` / `all`) → abort:

> "Unknown mode: `<mode>`. Valid modes: requirements / design / dev-full / dev-module \<name\> / all. Run `/drift-check` with no arguments for usage."

**`dev-module` with no `<name>`** → abort:

> "`/drift-check dev-module` requires a module name. Usage: `/drift-check dev-module <name>`."

**`dev-module <name>` where `<name>` does not resolve to an existing MODULE.md** (per per-language canonical paths from `structure-conventions.md`) → abort:

> "No `MODULE.md` found for module `<name>`. Available modules: \<list from MAP.md\>."

Otherwise load STATUS.md (phase, last-drift-check marker) and the inputs for the chosen mode. Abort on missing `requirements.md` as described in "Read-only inputs."

### 2. Compare

**Retrofit skeleton handling (universal).** In `design`, `dev-full`, and `dev-module` modes, any `MODULE.md` beginning with `<!-- retrofit: skeleton -->` is treated as pending curation. Do not compare its TODO-placeholder curated sections against requirements or code — the comparison would be dominated by "no owning module" noise that reflects unfinished retrofit work, not real drift. Surface a one-line note per skeleton (`<module>: skeleton pending curation; skipped from comparison`) and move on. The module re-enters normal drift comparison once the sentinel is removed (per the retrofit curation workflow in `architecture.md`). If `dev-module <name>` targets a skeleton, abort with a clear message rather than running a no-op.

Perform semantic comparison appropriate to the mode:

- **requirements mode:** diff recent session activity (files touched this session, recent commits since last drift-check, uncommitted `docs/compact/` edits) against `requirements.md`. Look for: new behaviors discussed/implemented that lack an FR/NFR; requirements whose wording no longer matches observed intent; Deferred items that have been picked up without being moved out of Deferred.
- **design mode:** for each requirement (FR / NFR), identify the design artifacts (MODULE.md curated sections, DECISIONS entries) that carry it. Look for: requirements with no corresponding design element; design elements that contradict a requirement; ADRs whose decision conflicts with a current requirement.
- **dev-full mode:** for each module named in MAP.md, run the dev-module comparison. Aggregate.
- **dev-module mode:** compare module code against its MODULE.md — Public surface (declared vs actual), Invariants (stated vs enforced), Non-goals (declared vs violated), Depends on (declared vs imported). Also check relevant DECISIONS entries linked from Key choices.

### 3. Classify each finding

For every candidate difference, classify:

- **`[ALIGNED]`** — match found; silent, not surfaced in output.
- **`[DEFERRED]`** — the mismatch is captured in a Deferred section (`requirements.md → Deferred`, `MODULE.md → Deferred`, or inline `TODO(deferred: <reason>)` in code). Surface as a one-line note; **no resolution prompt.** The user is already aware.
- **`[DRIFT-N]`** — mismatch not tracked as deferred. Numbered sequentially within this run. Requires resolution.

### 4. Resolution prompt (per drift)

For each `[DRIFT-N]`, present:

```
[DRIFT-N] <Layer A> vs <Layer B>
  <Layer A (file:line)>: "<evidence>"
  <Layer B (file:line)>: "<evidence>"

Resolution:
  [a] Update <Layer A> to match <Layer B>
  [b] Update <Layer B> to match <Layer A>
  [c] Both change to: <user specifies>
  [d] Skip — resolve manually later
  [e] Mark as deferred (enter reason + revisit trigger)
```

- **Never auto-decide direction.** The user picks.
- For option `[c]`, capture the user's specified text verbatim; do not paraphrase.
- For option `[e]`, append to the appropriate Deferred section (`requirements.md → Deferred` if the drifting item is a requirement; `MODULE.md → Deferred` if it's a design element; inline `TODO(deferred:)` in code if it's an implementation gap). Include the user's reason + revisit trigger.

Collect all resolutions before applying any edits.

### 5. Apply edits (batch)

After all drifts have been resolved (or skipped), show the combined diff — all requirements.md edits, all MODULE.md edits, any DECISIONS appends, any code edits. User confirms.

- **Removed requirements** are struck through in place: `~~**FR-3** — <text>~~` (removed YYYY-MM-DD: <reason>). IDs are never reused.
- **New requirements** added during resolution get the next sequential ID.
- **DECISIONS appends** (e.g. "design changed — log why") follow the standard ADR format; assign the next `D-XXX` ID.
- **Code edits** are presented as normal file diffs; if more than ~20 lines change, prompt the user to implement manually and mark the drift resolved as "pending manual code change in STATUS.md Flags" instead.

On `abort`, discard all pending edits. Nothing is written.

### 6. Update STATUS.md marker

Update (or insert) a single line near the top of STATUS.md:

```
Last drift-check: YYYY-MM-DD — mode: <mode> — <N drift(s) resolved, M deferred surfaced>
```

Also append any newly-deferred items the user chose to mark (step 4, option `[e]`) to the appropriate artifact's Deferred section. STATUS.md's marker line is metadata; it's updated in place, not appended.

### 7. Summary + cascade prompt

Print:

- Drifts resolved: `<count>` (with per-direction breakdown)
- Drifts skipped: `<count>` (added to STATUS.md Flags as "drift-check: <N> unresolved from <mode> run")
- Deferred items surfaced: `<count>`
- Files edited: `<list>`
- Next recommended action (if `mode ≠ all`):
  - After `requirements`: "Descend to `/drift-check design`? (yes/no)"
  - After `design`: "Descend to `/drift-check dev-full`? (yes/no)"
  - After `dev-full` or `dev-module`: nothing — deepest level reached.

**Never auto-descend.** The cascade prompt is a yes/no question; on `no`, stop cleanly.

For `mode = all`, the prompt is part of the flow: after each level completes, ask for descent approval before continuing.

## Deferred — where items live

Each artifact carries its own `## Deferred` section; no central registry.

- **`requirements.md → Deferred`** — deferred requirements. Entry format:
  ```
  - **FR-N** — <requirement> (deferred: <why> — revisit: <trigger or date>)
  ```
- **`MODULE.md → Deferred`** (optional section, added as needed) — planned-but-unbuilt behaviors for that module.
- **Inline `TODO(deferred: <reason>)`** in code — OK for small, self-contained items.

Drift-check reads all three and classifies matching items as `[DEFERRED]`, not `[DRIFT]`. If the user asks to promote a deferred item into an active requirement mid-session, that's a normal edit — move it out of Deferred and re-number nothing.

## Rules

- **Read-only until the user resolves.** Detection phase writes nothing.
- **Never auto-decide direction.** Every drift gets a resolution prompt.
- **Never auto-cascade.** `all` mode prompts at each descent.
- **Deferred ≠ drift.** Surface deferred items as one-line notes; no prompt.
- **IDs are stable.** Never renumber FR/NFR. Removed requirements are struck through, not deleted.
- **Preserve + prefix retrofit IDs.** If the project came from `--retrofit` and already had numbered requirements, those IDs stay (e.g. existing `REQ-042` is preserved verbatim). Only new additions use the COMPACT default (`FR-N` / `NFR-N`).
- **Never invent rationale** when logging a DECISIONS entry during drift resolution. Ask the user; mark `TODO` if unanswered.
- **Code edits >~20 lines are flagged for manual implementation.** Drift-check is a design-alignment skill, not a refactoring tool.
- **Output should be PR-quality.** Every drift is cited with `file:line`; a reviewer can skim the log.
