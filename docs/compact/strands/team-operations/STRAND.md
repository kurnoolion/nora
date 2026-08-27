# team-operations

**Status:** in-flight
**Opened:** 2026-08-27
**Landed:**
**Assignees:** kurnoolion
**Target modules:** docs, docker (no core modules expected yet)
**Active phase:**

## Summary

Transition NORA operations from a single operator to a team: onboard members
to own ingestion of requirement releases and eval running (golden evals,
sweeps). Covers runbooks/guides, access and role setup, and any tooling or
workflow changes needed so multiple operators can work the pipelines safely
in parallel.

## Notes

Scoping (2026-08-27):

- Ingestion: multiple trained members, strictly one build cycle in
  flight at a time, owned end-to-end by one operator.
- Dedicated Linux build machine for operators (being provisioned);
  serving stays on the existing host — promote therefore needs a
  cross-host serve-set transfer step that does not exist yet.
- Promote authority: delegated to operators (serve flip + rollback on
  the serving host included).
- Phase 2 (per-MNO prompt derivation, AI-assisted): delegated — needs
  operator-grade documentation plus tooling access.

Ops-infra design (2026-08-27, agreed):

- Shell is the control surface; web stays visibility-only (dashboards,
  Eval Studio, review surfaces, in-process jobs lane). No web control
  plane for the dockerized cycle — it would add a docker-socket-exposed
  orchestrator without removing the shell requirement.
- Automation depth: a phase-gated cycle driver (each phase checks its
  predecessor's artifacts, fail-loud), not one-shot automation. Human
  gates at: profile bindings (new cells), Phase 2 prompt derivation,
  enrichment verification, promote go/no-go. Promote stays a deliberate
  two-command act (transfer, then flip) with a checklist between.
- Cycle state: per-build marker file (owner, phase, timestamps) in the
  build dir — the serialization baton and crash-resume in one, no lock
  daemon.
- Artifact layout: stateless checkouts; everything shared re-roots at
  /srv/nora (requirements/, builds/{nora,sira}/, serve/, eval/golden/,
  env/). Env files move OUT of the checkout into /srv/nora/env/ — they
  are shared operational state (.env.builds is the baton's pointer),
  not checkout config; env.example remains the in-repo template. Build
  machine carries requirements/+builds/; serving host carries
  serve/+eval/. Ownership: group nora-ops, setgid dirs, umask 002,
  containers run as ${JOB_UID}:${JOB_GID} for per-file attribution.
  Contingency: if the build machine's docker is snap-confined (home
  paths only), re-root at /home/nora-ops/ (shared system home) —
  layout otherwise unchanged; provisioning checklist mandates
  docker-ce from the official repo to avoid this.

Cross-host promote design (2026-08-27, agreed):

- Three steps: promote.sh (unchanged, local snapshot + MANIFEST) →
  serve-push.sh <label> <host> (build machine, push over rsync/ssh as
  the operator's own account; .incoming staging + atomic mv so a remote
  label either exists complete or not at all; refuse existing label —
  immutability holds on both ends; rsync -c verify pass) →
  serve-flip.sh <stack> <label> (serving host: print MANIFEST vs
  current label, confirm, rewrite env pointers, recreate, verify
  healthz identity vs MANIFEST, print rollback one-liner).
- Rollback is not a special path: flip to a previous label; old labels
  retained on disk, GC manual (keep last N).
- Phase-6 protocol, three modes, operator-decided (gate is advisory,
  never enforced): staged (default — flip secondary stack, golden eval
  vs production baseline, then flip production), expedited (direct
  flip, hotfix-shaped), override (eval regressed, promote anyway,
  reason known to operator).
- Staging role is transient: a promote window claims the secondary
  stack; campaigns and promotes never overlap (serialized-ops baton
  extends here). A dedicated .env.staging stack only if contention
  becomes real.
- serve-flip appends to PROMOTE_LOG on the serving host: label, stack,
  operator, timestamp, mode, eval run id if any — overrides stay
  auditable, nothing is blocked.

Migration plan — serving host (2026-08-27, agreed):

- Findings: serving host runs SNAP docker (home-confined bind mounts;
  auto-refresh restarts dockerd, killing detached campaigns — standing
  hazard); /home and /srv are on the same filesystem (mv is instant and
  hardlink-safe).
- Decision: replace snap docker with docker-ce in the migration window
  (not design around snap). Uniform /srv/nora layout on both hosts;
  removes the auto-refresh hazard; avoids the owner-qualified
  AppArmor uncertainty of a shared /home/nora-ops fallback.
- Window sequence (gated on sweep-campaign completion): capture
  before-invariants (healthz identity lines + golden set= baseline) →
  stacks down → docker save images → snap remove docker → install
  docker-ce (official repo) → docker load → mv tree to /srv/nora →
  env files to /srv/nora/env/ with path-var rewrite + /home/ leak grep
  → group nora-ops, setgid, JOB_UID/JOB_GID in per-operator shell
  profiles (identity never in shared env files) → recreate stacks from
  new env paths → verify healthz byte-identical to capture → golden
  Stage-1 smoke (set= must match baseline) → rename old dir to
  nora-data.MIGRATED-DO-NOT-USE (fail-loud, no compat symlink).
- New build machine: greenfield, docker-ce from day one, probe
  /srv bind mount during provisioning. Dev PC: adopt layout last, no
  urgency (docs will read /srv/nora).

Cycle driver design (2026-08-27, agreed):

- docker/cycle.sh — thin, phase-gated: orchestrates existing tools
  (ingest.sh, sira-batch invocations, promote.sh, serve-push.sh),
  never reimplements them; every phase remains runnable by hand.
- Explicit verbs only (no `next` auto-advance): start <build-id> /
  status / parse / prompts / taxonomy / enrich [--cell C]
  [--retry-failed] / verify-enrich / promote --label L / abandon.
  Enrichment loops all cells smallest-first by default; --cell targets
  one (retry flow: verify-enrich → fix → enrich --cell X
  --retry-failed).
- CYCLE.json in the build dir = baton + crash-resume + audit: owner,
  build-id, phase log ({phase, status, by, started, finished}).
  `start` refuses while any cycle is neither promoted nor abandoned.
- Preconditions check artifacts, not just recorded state (operators
  may act manually); fail-loud with named errors + one-line fix.
- Human gates are verbs (prompts, verify-enrich): present evidence,
  require explicit yes, record who confirmed. Promote go/no-go lives
  on the serving host in serve-flip.sh.
- Cross-host seam: `promote` closes the cycle at push time and prints
  the serve-flip command; the flip is outside the cycle (serving-host
  act, PROMOTE_LOG is its record) so the baton frees immediately.

Access matrix (2026-08-27, agreed):

- Ingestion operators: shell + nora-ops on BOTH machines (push lands
  as them, flip runs as them); eval operators: serving host only;
  domain experts: gated web only; sudo stays architect-only on both
  hosts (needing sudo = outside the designed surface).
- Operators are implicitly web-admins (group-readable stack env files
  carry NORA_WEB_ADMIN_TOKEN): accepted deliberately — shell already
  grants strictly more than the admin cookie; the team-mode gate's
  threat model is web-side users and stays intact. Same rationale for
  LLM credentials in env files.
- Profile bindings for new cells: operator authors, architect reviews
  + commits + rebake rides the next cycle (repo write stays with one
  person; bindings encode document-structure judgment worth review).
  Runbook Phase 0 records this as an explicit checkpoint.

NAS integration — NetApp FAS2750, dual-domain (2026-08-27, agreed):

- Rule: file-shaped/cold/exchanged data on the NAS; database-shaped/
  hot/served data on local disk (SQLite-over-NFS corruption hazard:
  vector stores, SIRA cell DBs, web aiosqlite; hardlink semantics
  depend on volume security style; small-random-I/O latency).
- requirements/ → NAS: Windows-side members drop release docs into the
  share themselves; build machine reads the same tree over NFS at
  /srv/nora/requirements/ — staging friction removed (generalizes the
  existing Windows↔Linux shared-folder pattern).
- archive/ → NAS: retired builds, old labels beyond keep-last-N, image
  tarballs, completed sweep runs; Linux-side GC becomes "move to NAS".
- Serve-set transfer stays rsync-over-ssh (decided; NAS-staged
  transfer kept as fallback if cross-host ssh ever becomes a problem).
- env/ NEVER on the share (admin token + LLM creds; dual-domain
  audience is wider than nora-ops) — local per host, 0640
  root:nora-ops. Share ACLs are part of the security boundary now:
  team-restricted on both domains, not org-wide (proprietary corpus).
- Storage-side asks: NFS export with UNIX or mixed security style;
  consistent user/group id mapping (domain accounts free; local →
  pin numeric IDs in provisioning). Mounts via fstab at
  /srv/nora/{requirements,archive} — uniform tree, docker indifferent.

Reporting conventions (2026-08-27, agreed):

- Per-phase compact blocks ("CYC" family, GEV pattern generalized):
  emitted by cycle.sh at each phase end, saved under the build dir's
  reports/, chat-pasteable by construction — header (build-id, phase,
  operator, timestamp) + counts/codes/digests lines + err histogram;
  never document/requirement/query text (NFR-8-clean without operators
  needing to know the rules). Gate verbs record "gate: confirmed by
  <user>" in their block — a pasted block sequence IS the cycle's
  audit narrative, matching CYCLE.json.
- Standing baseline: ${GOLDEN_DIR}/baselines/<stack>.txt holds the
  accepted GEV block for what that stack currently serves; owned by
  eval operators; staged-promote comparison reads it; serve-flip
  prints a reminder to refresh it after the next accepted golden run
  (reminder, not automation).

Backlog (recommended order):

0. Shared-layout migration plan — designed above; execute after the
   sweep campaign completes.
1. Cross-host promote design — transfer mechanism + recreate + verify
   + rollback; the one item needing tooling, not just docs.
2. `docs/ingestion-guide.md` — operator-grade Phases 0–6 runbook.
3. Build-machine provisioning checklist.
4. Serialization convention — cycle-owner baton, documented not tooled.
5. Access matrix — build machine / serving host / admin token /
   GOLDEN_DIR.
6. Reporting conventions per phase — counts/histograms/GEV blocks and
   the sharing boundary each operator must respect.
7. Eval-operator campaign-discipline notes — the guide exists; the
   discipline layer (anchors, snapshot freeze, arm flips) is not yet
   written down.

