## D-DRAFT-1 — Shell is the operator control surface; the web app stays visibility-only

**Context:** Delegating ingestion + promote to team members raised the
interface question: extend the web app (a jobs lane exists) into a
control plane for the dockerized cycle, or keep operators in the shell.
The existing web jobs lane drives only the in-process NORA
PipelineRunner — none of the dockerized SIRA cycle.

**Decision:** Shell is the control surface for the ingestion/promote
cycle. The web app keeps its existing surfaces (dashboards, Eval
Studio, review pages, in-process jobs lane); at most it later gains a
read-only cycle-status page fed by CYCLE.json markers.

**Why:** Operators need shell on both machines regardless (the serve
flip is inherently a shell act), so a web control plane adds a second
surface without removing the first. Web-driving the cycle means the
web container orchestrating sibling containers — docker-socket access
from a network-exposed app, a real security/complexity step for a 2–3
operator team. Failures are diagnosed in shell anyway.

**Consequences:** Operators must be Linux-capable — a hiring/onboarding
requirement, not a tooling one. Visibility for non-shell stakeholders
comes from pasted CYC blocks (D-DRAFT-8), not a live UI. Revisit only
if the operator pool outgrows shell comfort.

## D-DRAFT-2 — Stateless checkouts: all shared state re-roots at /srv/nora; env files leave the checkout

**Context:** Solo operation kept everything under one user's home
(~/work/nora-data) with live env files inside the repo checkout's
docker/. Multi-operator breaks both: no canonical home, and two
checkouts mean two divergent .env.builds — the file that points at the
current build dirs, i.e. shared operational state, not checkout config.

**Decision:** Shared root /srv/nora (requirements/, builds/{nora,sira}/,
serve/, eval/golden/, env/); live env files move to /srv/nora/env/ and
never re-enter a checkout (env.example stays the in-repo template).
The per-user checkout holds code only and is disposable. Ownership:
group nora-ops, setgid dirs, umask 002, containers run as per-operator
${JOB_UID}:${JOB_GID} (identity in shell profiles, never in shared env
files).

**Why:** One truth for "what is in flight" requires the pointer files
to live outside any personal checkout. Group + setgid + per-operator
UID gives shared writability plus per-file attribution for free; a
service account would erase attribution. Same compose project results
from any operator's checkout because project name, mounts, and image
names all come from the shared env file.

**Consequences:** Invocations gain --env-file /srv/nora/env/... paths;
all guides teach them. Build machine carries requirements/+builds/,
serving host serve/+eval/ — the promote transfer stays an explicit
act. Migration must rewrite absolute paths and sweep for /home/ leaks.

## D-DRAFT-3 — Cross-host promote: rsync push + atomic remote labels; staged promote by default; the eval gate is advisory and logged, never enforced

**Context:** Builds move to a dedicated machine while serving stays on
the existing host, and promote authority is delegated — promote.sh's
local snapshot no longer reaches the serving host, and the go/no-go
judgment no longer defaults to the architect.

**Decision:** Three steps: promote.sh (unchanged) → serve-push.sh
(rsync over ssh as the operator, .incoming staging + atomic mv — a
remote label exists complete or not at all; refuses existing labels;
rsync -c verify) → serve-flip.sh on the serving host (MANIFEST vs
current-label confirm, env rewrite, recreate, healthz verify, rollback
print). Default protocol is staged: flip the secondary stack, golden
eval, compare to the standing baseline, then flip production. Two
sanctioned deviations: expedited (skip eval) and override (eval
regressed, promote anyway). serve-flip appends label, stack, operator,
timestamp, mode, and eval run id to PROMOTE_LOG.

**Why:** Push-from-build keeps one hop and uses accounts operators
need anyway; pull adds no safety. Staged promote makes go/no-go
evidence-based using machinery A/B already built, and a bad candidate
becomes a non-event instead of a rollback. The gate stays advisory
because the operator can hold context the eval set doesn't (deliberate
trade-offs, hotfixes); enforcement would push people to work around
the tooling. The log preserves auditability without blocking.

**Consequences:** Ingestion operators need accounts on both machines.
Rollback stays "flip to a previous label" — no special path. The
staging role of the secondary stack is transient; campaigns and
promote windows must not overlap (serialization baton extends there).
One golden run (~1h) per default-path promote.

## D-DRAFT-4 — Serving host swaps snap docker for docker-ce in the migration window

**Context:** The serving host runs snap-packaged docker. Snap
confinement limits bind mounts to home paths (breaking the /srv/nora
root), and snap auto-refresh restarts dockerd on its own schedule —
killing every running container, including detached multi-hour sweeps
and always-on serving stacks. Alternatives considered: design around
it (/home/nora-ops root + snap refresh --hold) or replace the engine.

**Decision:** Replace snap docker with docker-ce (official repo) inside
the already-needed migration window: stacks down → docker save images
→ snap remove → install docker-ce → docker load → continue migration.

**Why:** The auto-refresh hazard exists independent of layout and is
worst exactly for this host's workload. The fallback root carries
owner-qualified AppArmor uncertainty for a shared home. Uniform layout
and engine across both hosts keeps every guide single-story. Cost is
~30–60 min inside an existing window, using the already-exercised
save/load pattern.

**Consequences:** One-time image save/load; local volumes/state under
/var/snap/docker must be inventoried before removal. Provisioning
checklist mandates docker-ce and forbids snap docker on all nora hosts.

## D-DRAFT-5 — Ingestion runs through cycle.sh: thin phase-gated driver, explicit verbs, CYCLE.json as baton + resume + audit

**Context:** The Phase 0–6 cycle lives in docker/README as expert field
notes; delegation needs repeatability, one-cycle-at-a-time
serialization, and crash-resume — without hiding the underlying tools
or automating away judgment. Full one-shot automation and a `next`
auto-advance verb were considered.

**Decision:** docker/cycle.sh orchestrates existing tools and never
reimplements them; explicit verb per phase (start/status/parse/
prompts/taxonomy/enrich [--cell] [--retry-failed]/verify-enrich/
promote/abandon); enrichment loops cells smallest-first by default.
CYCLE.json in the build dir records owner + phase log; `start` refuses
while any cycle is unfinished (the baton). Preconditions check real
artifacts, fail-loud with named errors. Human gates are verbs that
present evidence and record who confirmed. `promote` closes the cycle
at push; the flip lives outside it.

**Why:** Judgment points (profile bindings, prompt derivation,
enrichment verification, promote go/no-go) can't be scripted away —
phase-gating automates the mechanical majority while making gates
unskippable. Explicit verbs preserve the deliberateness the design
exists for. A marker file gives serialization + resume + audit with no
daemon. Artifact-checked preconditions keep manual work legal.

**Consequences:** cycle.sh must stay thin — logic drift into the
driver breaks "everything still works by hand." The baton is
convention-backed (a marker, not a lock): two simultaneous `start`s
race in theory; acceptable at this team size. Runbook is written
against the verbs with manual equivalents alongside.

## D-DRAFT-6 — Operators are implicitly web-admins; profile-binding commits stay with the architect

**Context:** Group-readable stack env files carry NORA_WEB_ADMIN_TOKEN
and LLM credentials, so anyone with shell can unlock the full web UI.
Separately, new-cell profile bindings are committed JSON baked into
images — the one place ingestion touches the repo.

**Decision:** Accept operators-as-web-admins deliberately (no secret
store, no root-only env files). Repo write access stays with the
architect: operators author bindings, the architect reviews, commits,
and the rebake rides the next cycle — an explicit Phase-0 checkpoint.

**Why:** A shell operator can already do strictly more than the admin
cookie allows; hardening the token buys nothing against that role, and
the team-mode gate's threat model (web-side users) is untouched. New
cells are rare, and bindings encode document-structure judgment worth
review; one committer keeps repo write surface minimal while the team
is new.

**Consequences:** The access doc states the implicit-admin fact so it
is a decision, not an accident. Architect availability is on the
critical path for new-cell onboarding (accepted; a few times a year).
Revisit commit rights if cadence grows.

## D-DRAFT-7 — NAS (dual-domain NetApp) carries file-shaped/cold data; DB-shaped/hot data stays local; transfer stays rsync

**Context:** A NetApp FAS2750 with ample space is reachable from both
Windows and Linux domains. Candidates for it: source corpus, archives,
serve-set transfer medium, live serving data, pooled eval state.

**Decision:** The split rule — file-shaped, cold, or exchanged data on
the NAS (requirements/ with Windows-side document drop; archive/ for
retired builds, old labels, image tarballs, completed runs);
database-shaped, hot, or served data on local disk (vector stores,
SIRA cell DBs, web SQLite, live serve/ and eval/golden/). Serve-set
transfer stays rsync-over-ssh; NAS-staging is the recorded fallback.
env/ never on the share. Mounts via fstab at
/srv/nora/{requirements,archive}.

**Why:** SQLite over NFS is a known locking/corruption hazard and the
hot paths are full of SQLite; hardlink semantics vary with NetApp
volume security style; entry-level NAS latency suits transfers, not
small-random-I/O serving. The Windows-side drop removes the
staging-by-operator step entirely, generalizing the project's existing
shared-folder pattern. The share's dual-domain audience is wider than
nora-ops, so secrets stay off it and its ACLs join the security
boundary (proprietary corpus).

**Consequences:** Storage-side dependencies: NFS export with UNIX or
mixed security style, team-restricted ACLs on both domains, consistent
user/group id mapping. GC becomes "move to NAS". If cross-host ssh is ever
withdrawn, the transfer fallback activates without redesign.

## D-DRAFT-8 — Tool-emitted CYC compact blocks per phase; standing GEV baseline file per stack

**Context:** Operators must report cycle progress without knowing the
redaction rules, and staged promote needs a comparison baseline that
currently lives in memory ("whatever run you remember").

**Decision:** cycle.sh emits a compact block at each phase end (header:
build-id, phase, operator, timestamp; counts/codes/digests lines; err
histogram), saved under the build dir's reports/ and chat-pasteable —
never document, requirement, or query text. Gate verbs record "gate:
confirmed by <user>". ${GOLDEN_DIR}/baselines/<stack>.txt holds the
accepted GEV block for what each stack currently serves, owned by eval
operators; serve-flip prints a refresh reminder (not automation).

**Why:** The GEV block proved tool-emitted compact blocks beat prose
status: zero effort, uniform, NFR-8-safe by construction — the
convention is enforced by tooling, not memory. A baseline as a file
makes the staged-promote comparison concrete and gives the
eval-operator role an owned artifact.

**Consequences:** Block formats become a stable contract (operators
and the architect parse them by eye; changing fields is a breaking
change worth a decision). A stale baseline file misleads staged
promotes — upkeep is an eval-operator duty the campaign-discipline
notes must teach.

## D-DRAFT-9 — serve-push transfers without owner/group; ownership comes from the pusher + setgid

**Context:** The cross-host push copies a promoted label from the build
machine to the serving host over rsync as the operator's own account.
Build-machine files carry that host's numeric user/group ids; the
serving host and the NAS mapping may not agree with them, and the
serve/ tree must stay group-writable so any operator can GC labels.

**Decision:** `serve-push.sh` uses `rsync -rlptD --chmod=ug+rwX,Dg+s`
— `-a` minus `-o`/`-g` — so files land owned by the pushing account,
group inherited from the setgid `serve/` tree, group-writable, setgid
re-applied on directories. Source hardlinks become plain files on the
remote by design.

**Why:** Preserving owner/group (`-a`) requires identical numeric ids
on both hosts (and on the NAS) and would let a build-machine mode bit
decide whether a serving-host operator can later remove the label.
Per-file attribution to the pusher is the audit property wanted; the
group is the access property; neither needs the source's ids. The
de-linking from `builds/` is what makes the remote label immune to
build wipes without a same-filesystem constraint across hosts.

**Consequences:** Remote labels are real copies (disk cost = serve-set
size per label; GC = move to archive). The pusher must be in nora-ops
on the serving host (checked by `SPUSH-E011`). Ownership on the two
hosts intentionally differs for the same label.

## D-DRAFT-10 — Detached phases signal completion via a CYC-EXIT trailer the driver appends to the phase's own log

**Context:** parse, taxonomy and enrich run for hours as detached
`compose run -d --rm` jobs. The driver needs to know when a phase
finished and how, without a daemon, and without depending on the wording
of lane logs it does not own.

**Decision:** `cycle.sh` wraps every detached phase's inner command as
`( <cmd> ) > <log> 2>&1; echo "CYC-EXIT rc=$?" >> <log>` and reads the
trailing `CYC-EXIT` line to derive the phase state (`ok` / `failed` /
`running`). `CYCLE.json` records the log path; `status` and every
precondition re-derive state from the log, never from the recorded
status alone.

**Why:** Parsing `run_cli` / `sira_lane` output couples the driver to
log formats owned by other modules. `docker wait` or `ps` leaves no
record once `--rm` removes the container and needs the driver process
to stay alive. Foreground execution ties an hours-long job to a
terminal. The trailer is written by the same shell that ran the job,
survives the container, and is independent of what the job printed.

**Consequences:** A phase whose log lacks the trailer is `running`
forever if the container was killed from outside (host reboot, OOM);
the operator's signal is a stale `running` in `status` with no live
container — a `--heal`/orphan-detection verb is a possible later
addition. Logs gain one foreign line at the end.

## D-DRAFT-11 — cycle.sh promote resumes at push when the local label already belongs to the active build

**Context:** Promote is two steps under one verb: `promote.sh` (local
hardlink snapshot, refuses an existing label) then `serve-push.sh`
(cross-host). A push can fail after the local label exists (ssh, disk,
checksum). Re-running the verb would hit `promote.sh`'s refusal, so a
failed push could only be retried by promoting under a new label.

**Decision:** `cycle.sh promote` checks `serve/<label>/MANIFEST.json`
first; if its `sira_build` names the active build, it skips
`promote.sh` and proceeds straight to the push. The cycle stays open
(baton held) until the push succeeds.

**Why:** A label is meant to name one serve-set; forcing a fresh label
per push attempt produces labels that differ only by attempt number and
breaks the immutability story ("same label = same data"). The MANIFEST
check ties the resume to the build, so a label that exists from a
different build is still refused. `promote.sh` itself stays unchanged
(immutability on the local side is its contract).

**Consequences:** `promote.sh`'s refusal is bypassed only inside the
driver and only for the active build. A partially pushed remote label
is never visible (staging + atomic mv on the push side). Operators
re-run the same `promote` command; the guide's recovery table depends
on this.

## D-DRAFT-12 — Driver gates are advisory: they present evidence and record who confirmed, they do not block

**Context:** The cycle has human decision points (prompt sets for new
MNOs, enrichment verification, promote mode). The driver could enforce
them (refuse to proceed with a missing prompt set, refuse WARN, refuse
override) or present the evidence and record the operator's call.

**Decision:** Gates are verbs that show evidence, require an explicit
typed `yes`, and record `confirmed_by` in CYCLE.json and the CYC block.
`prompts` confirms even with missing per-MNO sets (generic fallback,
counted in the block); `verify-enrich` refuses only on FAIL (structural
break) and confirms on WARN; `serve-flip` records `staged` / `expedited`
/ `override` and never enforces the staged protocol. Mis-shaped prompt
files and FAIL verdicts remain hard errors.

**Why:** Hard-blocking gates on judgment calls push operators to run
phases by hand around the driver — which every phase deliberately
allows — and the audit trail is lost exactly when a deviation happens.
Recording the deviation (who, when, the counts in the block) is the
property the team needs; the reviewer can see it in the pasted block
sequence. Structural breaks are not judgment calls and stay blocking.

**Consequences:** A new MNO can be enriched on fallback prompts if an
operator confirms it; the CYC block shows `missing=N` so the deviation
is visible, not silent. Override promotes are legitimate and logged.
If a deviation is ever confirmed by mistake, the fix is procedural
(guide + review of blocks), not a driver change — unless the pattern
repeats (journal flag).

---

## D-DRAFT-13 — UI-only changes deploy by web-only image rebuild; eval anchors never move

**Context:** req-id-bubbles merged mid-sweep. Campaign discipline pins
`code=` equal grid-wide, so the normal deploy (rebuild all four images,
recreate stacks) would advance sira-query's healthz `code_version` and
split the campaign's pooling — for a change that is provably web-only
(zero files outside `core/src/web/` + tests + docs in the merge diff).

**Decision:** a UI-only change deploys by rebuilding and recreating
ONLY the nora-web service: tag a rollback point, `compose build
nora-web`, `up -d nora-web`. The sira-query image is not rebuilt.
Precondition, checked not assumed: `git diff --name-only` against the
merge base shows nothing outside `core/src/web/`, tests, and docs.
Post-check: healthz on the query port reports unchanged label/fp/code.

**Why:** eval and the query lanes run in the sira-query container; the
web app is a separate container from the same repo. Rebuilding
everything changes the `code=` anchor with zero behavioral difference
for scoring — forcing either a campaign split or documented
anchor-equivalence bookkeeping. Rejected: hold the deploy until the
sweep completes (delays a shipped feature for no risk reduction);
rebuild-all plus a CAMPAIGN.md equivalence note (bookkeeping and a
pooling boundary bought for nothing).

**Consequences:**
- Fleet images diverge temporarily — nora-web runs a newer sha than
  sira-query, and healthz `code=` no longer names the web code's sha.
  The next cycle's Phase 0.2 rebake re-unifies; baselines refresh from
  the first accepted run after that per campaign discipline.
- The precondition is the whole safety argument: any non-web source
  change disqualifies this path, full stop.
- Journaled flag until re-unified so the divergence is visible.

---

## D-DRAFT-14 — Cross-build taxonomy seeding by plain copy; validity delegated to the stage's own cache

**Context:** cycle.sh builds are self-contained, so a fresh build has a
cold taxonomy cache and re-extracts every plan — full-corpus LLM cost
on every incremental release. The stage already carries a two-level
self-validating cache: a corpus fingerprint (stamped only on
zero-failure runs) and a per-plan ledger keyed by content hash of the
winning tree plus a prompt-files hash.

**Decision:** seed the new build with a plain `cp -r` of the previous
build's `out/taxonomy/` (after parse, before the first taxonomy run)
and let the stage decide per-plan validity itself. Never a hardlink
copy. Documented as an operator step in the ingestion guide; no driver
flag yet.

**Why:** the cache is content-hashed and self-validating — a stale or
wrong seed costs nothing (those plans re-extract), so a manual copy is
safe without driver support. `cp -al` rejected: the stage rewrites
`extraction_state.json` in place after every unit, so hardlinks would
corrupt the previous build's ledger (hardlinking is promote.sh's trick
and is safe there only because labels are immutable). A
`--seed-taxonomy` driver flag deferred until the manual step proves
tree byte-stability across builds.

**Consequences:**
- First seeded cycle must check the stage stats: `cached` ≈ plans the
  new release didn't touch. A silent full re-extract is safe but means
  trees are not byte-stable across builds — itself worth knowing.
- The guide carries the step; the driver flag is the enhancement path.
- The same seeding idea for enrichment (much larger cost) remains open
  and undesigned.
