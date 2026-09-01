# Ingestion Guide

How to take a new requirements release from source documents to a
serving stack: one build cycle, end to end, run by one operator on the
build machine with the flip done on the serving host. This is the
operator runbook; `docker/README.md` §"Ingest a new release" is the
reference the driver wraps — go there when you need to run a phase by
hand or understand a flag.

Companion guides: `docs/golden-eval-guide.md` (the eval you run before
flipping production), `docs/enrichment-review-guide.md` (what domain
experts do with the served result).

## Who does what

| Role | Machine | Does |
|---|---|---|
| Ingestion operator (you) | build machine + serving host | runs the cycle, pushes the label, flips stacks, records the log |
| Eval operator | serving host | golden evals; keeps `${GOLDEN_DIR}/baselines/<stack>.txt` current |
| Architect | both (sudo) | image rebakes, profile-binding commits, migrations |
| Domain experts | web only | enrichment review, Eval Studio |

Exactly **one cycle is in flight at a time**. The driver enforces it
(`cycle.sh start` refuses while another cycle is open) — if you hit
that refusal, talk to the owner it names rather than working around it.

## What you need before starting

- Shell on the build machine and the serving host, member of the
  `nora-ops` group on both (check: `id -nG | grep -w nora-ops`).
- `JOB_UID` / `JOB_GID` exported in your shell profile on the build
  machine (check: `echo $JOB_UID` — it should be your own `id -u`).
- ssh from the build machine to the serving host as yourself,
  key-based (check: `ssh -o BatchMode=yes <serving-host> true`).
- The wiring and runtime env files present under `/srv/nora/env/`
  (`.env.builds`, `.env.nora-pipeline`, `.env.sira-batch`, and the
  stack envs `.env.<stack>` on the serving host). You never create
  these; if one is missing, that is an architect task.
- The source documents for the release staged on the requirements
  share as `/srv/nora/requirements/<MNO>/<MMMYYYY>/` (Windows-side
  members drop them there directly). One directory per MNO × release
  — the directory name IS the cell name the rest of the cycle uses,
  spelled `<MNO>__<MMMYYYY>` (e.g. a new release for an existing MNO is
  one new directory beside the old ones).
- Ingest images rebaked for the code you intend to run. The images
  carry the lane code; the architect rebakes them when code changed.
  `cycle.sh start` records the image ids it found in `CYCLE.json`.

Layout you will see on the build machine (`NORA_ROOT=/srv/nora`):

```
/srv/nora/
├── requirements/<MNO>/<MMMYYYY>/   # source docs (NAS share, read here)
├── builds/nora/<build-id>/         # env dir: out/, prompts/, reports/, CYCLE.json
├── builds/sira/<build-id>/         # SIRA cells: <MNO>__<MMMYYYY>/
├── builds/ACTIVE                   # the baton: name of the cycle in flight
├── serve/<label>/                  # promoted snapshots (also on the serving host)
└── env/                            # wiring + runtime env files (never on the share)
```

All commands below run from the checkout's `docker/` directory.

## The cycle at a glance

| # | Phase | Command | Gate | "Done" looks like |
|---|---|---|---|---|
| 0 | open the cycle | `./cycle.sh start <build-id> --profiles <prev-build-id>` | — | CYC start block; `next: parse` |
| 1 | parse | `./cycle.sh parse` | — | `status` shows `parse ok`, parse dirs = cells |
| 2 | derive prompts | Cline playbook (new MNOs only) | — | prompt files committed by the architect |
| 3 | publish prompts | `./cycle.sh prompts` | **yes** | CYC prompts block, `missing=0 misshaped=0` |
| 4 | taxonomy | `./cycle.sh taxonomy` (repeat until `failed=0`) | — | `status` shows `taxonomy ok`, ledger `failed: 0` |
| 5 | enrich | `./cycle.sh enrich` | — | `status` shows `enrich ok` |
| 5v | verify | `./cycle.sh verify-enrich` | **yes** | verify PASS, CYC block `verify_rc=0` |
| 6 | promote + push | `./cycle.sh promote --label <label> --host <serving-host> --scheme <scheme>` | — | `pushed -> <host>:.../serve/<label>`; baton free |
| 6f | flip | on the serving host: `./serve-flip.sh <stack> <label>` | **yes** | healthz `label=<label>`; PROMOTE_LOG line |

Long phases (1, 4, 5) return immediately and run detached. `./cycle.sh
status` is how you find out they finished — re-run it; a phase is `ok`
only when its log ends with `CYC-EXIT rc=0`. Nothing downstream will
run against an unfinished or failed phase.

Build-id convention: `<YYYY-MM>-<letter>` (e.g. `2026-09-a`). Label
convention: `<YYYY-MM-DD>-<letter>`. Both are immutable once used.

## Phase by phase

### 0. Open the cycle

```bash
./cycle.sh start 2026-09-a --profiles 2026-08-a     # bindings copied from the previous build
./cycle.sh status
```

`--profiles` copies `profiles.json` — the mapping from each cell to the
document profile the parser uses. **A cell that did not exist in the
previous build has no binding and parse will fail loud (`PIP-E003`).**
For a new MNO or a structurally new release: author the binding,
have the architect review and commit it, and get a rebaked image or a
`profiles.json` with the new entry before running `parse`. This is a
deliberate checkpoint — bindings encode document-structure judgment.

`start` also repoints `.env.builds` at the new build dirs. Do not edit
that file by hand during a cycle; every verb checks it still points at
the active build.

### 1. Parse

```bash
./cycle.sh parse                    # all cells; add --force to redo everything
./cycle.sh status                   # until: parse ok, parse dirs = cells staged
```

Incremental by default: cells already parsed with an unchanged profile
are reused, so a cycle that adds one release re-parses only that cell.
If the host has no container egress for the standards downloads, add
`--skip-standards`.

Failure: `status` shows `parse failed`. Open the log it names
(`builds/nora/<build>/reports/lane-ingestion-ALL-*.log`), find the
first `PIP-E` code, fix the cause (almost always a missing binding or
a document the extractor cannot open), re-run `parse`.

### 2. Derive per-MNO prompts (only when a NEW MNO arrived)

Prompts are per MNO with releases pooled; a new release of a known MNO
needs nothing here. For a new MNO, run the Cline playbook
`cline-playbooks/derive-prompts.md` against the build env dir
(`/srv/nora/builds/nora/<build-id>`), spot-check the output as the
playbook says, and hand the files to the architect to commit under
`customizations/prompts/`. Phase 3 will not confirm a set that is
mis-shaped, but it WILL let you proceed with a missing set (the cell
falls back to generic prompts) — so read its evidence.

### 3. Publish prompts (gate)

```bash
./cycle.sh prompts
```

Prints, per MNO found in the parse output: which prompt version will be
used, whether the doc prompt has the batched shape, and whether the
overview is placeholder-free — then asks for `yes`. Say yes only when
`missing=0 misshaped=0`, or when you know why a fallback is acceptable
for this cycle (say so in your report). The confirmed set is copied
into `builds/nora/<build>/prompts/`, which the containers read.

### 4. Taxonomy

```bash
./cycle.sh taxonomy
./cycle.sh status                   # taxonomy ok AND ledger {'ok': N, 'failed': 0}
```

One global stage over every cell. Sporadic LLM/server errors are
expected and non-fatal: the run records failed docs and continues.
**Re-running `taxonomy` is the retry** — it re-attempts only the
failed docs. Repeat until the ledger shows `failed: 0`; `enrich`
refuses otherwise (`CYC-E061`). If the same docs stay failed across
three runs, escalate with the CYC block and the `TAX-` codes from the
log; do not paste the log itself (it names documents).

Expect `features=` ≈ the number of distinct plans across all MNOs
(newest release wins per plan), not plans × releases.

**Seeding (optional — skips re-extracting unchanged plans):** a fresh
build starts with a cold taxonomy cache, so every plan re-extracts
even when only one release changed. After `parse` is ok and before the
first `taxonomy` run, copy the previous build's taxonomy output in:

```bash
cp -r builds/nora/<prev-build>/out/taxonomy builds/nora/<build>/out/
./cycle.sh taxonomy
```

The stage validates the seed itself — every cached plan carries a
content hash of the tree it was extracted from, the prompt files are
hashed too, and the stage-level fingerprint is only stamped on
zero-failure runs — so a stale or wrong seed costs nothing: those
plans simply re-extract. Only plans whose winning tree changed (the
new release's plans, typically) hit the LLM. The stage stats show
whether the seed took: expect `cached` ≈ plans untouched by the new
release, `extracted` ≈ plans it added or changed. Use a plain
`cp -r`, never a hardlink copy (`cp -al`) — the stage rewrites the
ledger in place, and hardlinks would corrupt the previous build's
copy. `--force` ignores the seed entirely.

### 5. Enrich

```bash
./cycle.sh enrich                   # every cell, smallest first, verify at the end
./cycle.sh status                   # until: enrich ok (or failed)
```

Runs each cell as its own lane invocation under one run name (defaults
to the build-id) and finishes with the verify sweep. Everything resumes
by document: an interrupted or partially failed run is continued by
**the same command** — nothing already enriched is redone.

Targeted forms:

```bash
./cycle.sh enrich --cell <MNO>__<MMMYYYY>            # one cell (e.g. inspect the smallest first)
./cycle.sh enrich --cell <MNO>__<MMMYYYY> --retry-failed   # evict genuine failures, solo LLM calls, re-verify
```

After a hard interruption (host reboot, killed container) the runbook's
`--heal-torn` repair is a by-hand step (`docker/README.md` §"Retrying
failed enrichments") before you resume.

### 5v. Verify enrichment (gate)

```bash
./cycle.sh verify-enrich
```

Runs the read-only verify sweep and shows it: per cell, batch/round
histograms, kept-vs-failed reconciliation, coverage, resume invariant,
a verdict. The output is **paste-safe by construction** (counts and
statuses only). On any cell `FAIL` the verb stops with `CYC-E070` and
cannot be confirmed — triage with `sira_enrich_inspect --failed`
(local-only output: it prints real requirement ids), then
`enrich --cell X --retry-failed`, then verify again. On `PASS`, read
the `WARN`s, and confirm with `yes`.

What the statuses mean when you triage (from the runbook): timeouts /
connection / HTTP errors are transient — retry fixes them;
`all_filtered` is not an error and is excluded from retries;
`skipped_*_chunk` is build policy, not failure; `llm_refused` is a
permanent refusal — the fallback LLM must be configured in
`.env.sira-batch` (architect), then `--retry-failed --include-refused`
by hand.

### 6. Promote and push

```bash
./cycle.sh promote --label 2026-09-05-a --host <serving-host> --scheme <sira-prompt-scheme>
```

Snapshots the serve-set into `serve/<label>/` (hardlinks, instant),
pushes it to the serving host (rsync into a staging dir, checksum
verify, atomic rename — the remote label exists complete or not at
all), closes the cycle and frees the baton. `--scheme` names the
enrichment-prompt scheme the cycle ran (ask if unsure; it is how eval
runs attribute served data to a hypothesis). Labels are refused if
they already exist, on either host — pick a new one.

`--sira-only` when the cycle produced no NORA-native artifacts (the
NORA lanes were not run); the web app's NORA lanes then serve empty on
this label, which is the expected state today.

### 6f. Flip (serving host)

The default protocol is **staged**: the secondary stack first, an eval
against the standing baseline, then production.

```bash
# on the serving host, docker/ of the checkout
./serve-flip.sh <secondary-stack> <label>              # shows MANIFEST current vs new; type yes
# golden eval against the secondary stack (docs/golden-eval-guide.md);
# compare its GEV block with ${GOLDEN_DIR}/baselines/<production-stack>.txt
./serve-flip.sh <production-stack> <label> --eval-run <run-id>
```

Alternatives, both recorded in `serve/PROMOTE_LOG`, neither blocked:

- `--mode expedited` — direct production flip, no staging (hotfix-shaped
  changes, or a rollback).
- `--mode override --note "<why>"` — the eval regressed and you are
  promoting anyway, reason stated.

The flip verifies the query service's `/healthz` reports the new
label and prints an identity line (`label= fp= code= scheme= cells=`)
— that line is what you paste as the promote report. It also prints
the rollback one-liner; keep it in your terminal until the stack has
been observed healthy.

**Rollback** is the same command with the previous label:
`./serve-flip.sh <stack> <previous-label> --mode expedited --yes`.
Old labels stay on disk; nothing is deleted by a flip.

After production is on the new label, the eval operator refreshes the
baseline file from the next accepted golden run on that stack (the
flip prints the reminder).

## Multiple operators

The env dir belongs to the **build, not the operator**:
`builds/nora/<build-id>` (with its `builds/sira/<build-id>` twin) is
created by `cycle.sh start`, named by the build id, and shared by the
whole group. The setgid tree + `umask 002` + per-operator `JOB_UID` /
`JOB_GID` exist precisely so that any group member can re-run,
`--force`, retry, or clean up what another member's containers wrote.

**Cycles are serialized, never parallel.** The baton (`builds/ACTIVE`)
admits one open cycle; a second `start` refuses with CYC-E021 and names
the owner. What IS supported is **hand-over**: any group member can run
the next phase, confirm a gate, or retry a failed enrich on the open
cycle — `CYCLE.json` records the owner, and every phase and gate
records who ran or confirmed it (`by`, `confirmed_by`), so a cycle
started by one operator and promoted by another keeps a full trail.
Non-owner phases print a NOTE, not a refusal. Rehearse this once with
two operators (the provisioning checklist's hand-over cycle) before
relying on it.

Within the open cycle, keep it to **one phase in flight at a time**.
The phase gates enforce ordering, but they cannot see two detached
containers writing the same build concurrently — one operator running
`taxonomy` while another launches `enrich` is the failure mode the
baton cannot catch. Whoever's turn it is runs the phase; everyone else
watches `./cycle.sh status`.

**Personal runs stay out of the team tree.** A parser experiment, a
profile debug, a what-if rebuild — anything that shouldn't wait for the
baton — runs against a private tree (own `--env-dir` /
`NORA_BUILDS_ROOT`, own copy of the wiring env) and is **never
promoted**. Promotion goes only through the baton'd cycle;
`promote.sh` refusing existing labels is the backstop, not the rule.
The shared `.env.builds` wiring is repointed only by `cycle.sh start`
on the team tree.

**Cleanup follows the promoter.** Builds are transient once their
label is pushed and the flip is verified: the operator who ran
`promote` archives the build if the team keeps archives (include
`CYCLE.json` — it is the who-did-what record) and then GCs
`builds/{nora,sira}/<build-id>`. `serve/PROMOTE_LOG` on the serving
host is the durable history either way.

## Reporting

Each phase writes a `CYC` block under `builds/nora/<build>/reports/`
and prints it. The sequence of blocks for a cycle IS the cycle's
report — paste them into the team channel as they come:

```
CYC build=2026-09-a phase=verify-enrich op=<user> ts=2026-09-04T18:02:11Z status=confirmed
run_name=2026-09-a verify_rc=0 warn_lines=3 gate=confirmed_by:<user>
```

Plus the verify sweep output (paste-safe) and the flip's healthz
identity line. `./cycle.sh status` output is paste-safe too.

**Never paste**: lane/stage logs, `sira_enrich_inspect` output, anything
under `reports/tax_debug/`, or file listings of `requirements/` — these
carry document names, requirement ids, or corpus text (NFR-8). When you
need help with one of those, describe the shape ("3 docs stay failed
with the same HTTP status") and keep the ids on the machine.

## When something refuses to run

| You see | It means | Do |
|---|---|---|
| `CYC-E021 cycle '<x>' is still in flight` | someone else's cycle owns the baton | coordinate with the named owner; never `rm ACTIVE` |
| `CYC-E030 no profiles.json` | bindings not staged | `start --profiles <prev-build>` or get bindings from the architect |
| `PIP-E003` in the parse log | a cell has no profile binding | add the binding (architect-reviewed), re-run `parse` |
| `CYC-E040 parse dirs < staged cells` | a cell failed to parse | first `PIP-E` code in the parse log |
| `CYC-E041 ... mis-shaped` | a derived prompt file has the wrong placeholders | fix under `customizations/prompts/` (architect commits) |
| `CYC-E061 taxonomy ledger has N failed` | LLM/server errors during taxonomy | `./cycle.sh taxonomy` again (retry); escalate after 3 identical runs |
| `CYC-E062 TAX-W003` | overviews were not attached | `.env.nora-pipeline` lacks `NORA_TAXONOMY_OVERVIEW_DIR`, or `prompts/` is empty — re-run `prompts` |
| `CYC-E070 verify reports FAIL` | structural break in a cell | triage locally, `enrich --cell X --retry-failed`, verify again |
| `SPUSH-E012 remote label already exists` | label name reused | promote again under a new label (`abandon` is NOT needed — the cycle is still open until push succeeds) |
| `SPUSH-E020 checksum verify found differences` | transfer incomplete | re-run the same `promote` command — the push resumes |
| `SFLIP-E020 healthz does not report serve_label` | stack came up on the wrong data or not at all | the printed rollback one-liner; then `docker compose --env-file <stack env> logs sira-query` |
| `CYC-E004 wiring env does not point at build` | `.env.builds` was edited by hand mid-cycle | restore the two path lines to the active build dirs |

Every error prints a `fix:` line; that line is the first thing to try.
If a cycle must be given up (wrong corpus staged, wrong bindings),
`./cycle.sh abandon --note "<why>"` frees the baton and keeps the
build dirs on disk for inspection.

## What is deliberately manual

- Image snapshot and rebake (runbook Phase 0.1–0.2) — architect;
  rides the next cycle.
- Profile bindings for new cells — operator authors, architect commits.
- Phase 2 prompt derivation — AI-assisted, per new MNO.
- NORA-native retrieval lanes (`./ingest.sh -l nora <MNO> <MMMYYYY>`)
  — not part of the default cycle while serving is SIRA-only.
- The flip — a separate act on the serving host, with its own log.
- Garbage collection — old builds and labels are moved to the archive
  share by hand, never by the tooling.
