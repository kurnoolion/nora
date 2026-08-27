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
