## 2026-08-27 — strand opened; full ops-infra design pass

### Done this session
- Strand opened and scoped: operator-task enumeration (ingestion
  Phases 0–6, eval ops, serving ops, expert loops), per-member needs,
  gap list. Scoping answers: multiple trained ingestion operators but
  one cycle in flight at a time; dedicated Linux build machine;
  serving stays on the existing host; promote authority and Phase 2
  prompt derivation both delegated.
- Design pass completed across the whole backlog (all recorded in
  STRAND.md Notes): shell as control surface with web visibility-only;
  phase-gated cycle driver (explicit verbs, CYCLE.json baton +
  crash-resume + audit, artifact-checked preconditions, human gates as
  verbs); stateless checkouts with shared root /srv/nora and env files
  relocated out of the checkout as shared operational state;
  cross-host promote (promote.sh → serve-push.sh rsync push with
  atomic completeness → serve-flip.sh with manifest confirm/recreate/
  healthz verify), staged-promote default with expedited + override
  modes and PROMOTE_LOG; migration plan for the serving host incl.
  snap-docker → docker-ce swap (snap confinement + auto-refresh
  hazard confirmed on the host; /home and /srv same filesystem);
  access matrix (operators shell on both machines, implicit web-admin
  accepted, architect keeps profile-binding commits); NAS (FAS2750)
  split — file-shaped/cold on the share (requirements/ with
  Windows-side drop, archive/), hot/DB local, rsync stays the
  transfer medium; reporting via tool-emitted CYC compact blocks +
  standing GEV baseline file per stack.

### In progress
- Nothing mid-flight; design phase complete, no code or guide written
  yet.

### Next
- Write the tooling: serve-push.sh, serve-flip.sh, cycle.sh (committable,
  generic placeholders).
- Write the docs: docs/ingestion-guide.md, build-machine provisioning
  checklist, eval campaign-discipline notes (incl. baseline upkeep).
- Execute the serving-host migration (gated on sweep-campaign
  completion) and greenfield-provision the build machine.

### Flags
- Serving-host migration is BLOCKED until the golden-eval sweep
  campaign (ON + OFF blocks) completes — the detached sweep references
  pre-migration paths.
- Storage-side asks outstanding for the NAS: NFS export with UNIX or
  mixed security style; team-restricted ACLs on both domains;
  user/group id mapping confirmation.

## 2026-08-28 — tooling + operator docs shipped

### Done this session
- Cross-host promote tooling (c63416d): docker/serve-push.sh (build
  machine → serving host: label preconditions, rsync into
  serve/.incoming/ with owner/group NOT preserved so ownership comes
  from the pusher + setgid, checksum verify, atomic rename, refuse
  existing remote labels, resumable) and docker/serve-flip.sh (serving
  host: MANIFEST current-vs-new, explicit yes, env pointer rewrite with
  .prev, recreate, healthz serve_label poll + identity line, PROMOTE_LOG
  TSV, staged/expedited/override modes recorded not enforced, rollback
  one-liner, baseline reminder). README Phase 6 cross-host subsection.
- docker/cycle.sh (30c3347): phase-gated driver over the runbook's own
  commands — builds/ACTIVE + CYCLE.json baton (one cycle in flight),
  artifact-checked preconditions with CYC-E codes + fix lines, detached
  long phases completing via a CYC-EXIT trailer in their own log,
  prompts + verify-enrich as explicit-yes gates recording who confirmed,
  paste-safe CYC blocks per phase, promote → serve-push hand-off that
  frees the baton at push time. Smoke-tested end-to-end (docker shimmed).
- docs/ingestion-guide.md (f193465): operator runbook — roles,
  prerequisites, /srv/nora layout, phase-by-phase with gates, staged
  flip + rollback, CYC reporting + NFR-8 boundary, error-code table,
  what stays manual. Writing the recovery table exposed that a failed
  push could not be retried (promote.sh refuses the existing local
  label) — cycle.sh promote now resumes at push when the label's
  MANIFEST names the active build.
- docs/provisioning-checklist.md (080f7b1): build machine A1–A8 with a
  probe per step (docker-ce not snap, nora-ops with pinned group id +
  setgid + umask 002 + profile-exported JOB ids, NAS mounts with the
  storage-side asks phrased for the NAS admin, env files under
  /srv/nora/env with absolute paths only, per-operator BatchMode ssh,
  hand-over cycle); serving-host 12-step in-place migration with
  before-invariants and fail-loud old-root rename; dev PC note.
- docs/golden-eval-guide.md §Campaign discipline (5c84b81): anchors,
  snapshot freeze, serving-env rule + image import probe, detached
  labeled launch + CAMPAIGN.md, per-cell verification + deliberate arm
  flip, pooling rules + baselines/<stack>.txt refresh.

### In progress
- Nothing mid-edit. All backlog writing items (1–7) delivered; items
  0 and 3 are now execution.

### Next
- NAS admin: get answers to the A4 asks (security style, machine-
  restricted export, team-only Windows share, id mapping).
- Provision the build machine per the checklist; run the A8 hand-over
  cycle on a small cell with an operator at the keyboard.
- Serving-host migration (section B) after the sweep campaign
  completes; seed PROMOTE_LOG and baselines/<stack>.txt.
- First field use of the three scripts: watch the two unverified
  assumptions — sira-query healthz reports serve_label from the
  sira/MANIFEST.json copy (else SFLIP-E020 fires on a good flip), and
  compose env_file paths must be absolute once stack envs leave docker/.

### Flags
- Migration still BLOCKED on sweep completion (unchanged).
- Host python3 is a hard dependency of cycle.sh (CYCLE.json writer) —
  in the checklist; not in any image.
- cycle.sh gates are advisory by design: `prompts` lets an operator
  confirm with missing per-MNO sets (generic fallback) — revisit if a
  new MNO ever ships on fallback prompts by accident.

## 2026-09-01 — bubbles merge integrated; ops docs hardened by operator questions

### Done this session
- Merged req-id-bubbles (a0d5cd3): branch-flow review (team-gate
  allowlist + tests, MODULE.md in-branch, non-empty decisions-draft,
  redaction gates clean), full suite green in a worktree (1833/0),
  --no-ff per house style. Landed the strand (27d4ef3): D-210..D-213
  promoted, folder archived.
- Deployed via web-only image rebuild (D-DRAFT-13): sira-query image
  untouched, sweep anchors intact. Field-verified bubbles on both
  lanes AND a gated shared link — which also verified that strand's
  open assumption (SIRA-lane req_id byte-identity with parse trees).
- team-operations end-to-end verification plan written (chat): fully
  sandboxed drill on the work PC (env-var overrides, STACK_NAME test
  stack, localhost push, failure drills incl. push-resume and
  promote-resume), then recomposed for the real topology — CIFS
  requirements share (ro mount), PC-1 ingest / PC-2 artifacts +
  services, dedicated service user owning ~/nora with JOB ids pinned
  in stack env files, pushers on their own PC-2 accounts.
- ingestion-guide: Multiple operators section (11de3b6 — per-build env
  dir, baton hand-over, one phase in flight, personal runs never
  promoted, promoter owns GC); taxonomy seeding step (7e3cf27,
  D-DRAFT-14 — plain cp -r, never hardlinks).
- Incremental-ingest dependency inventory verified in code:
  profiles.json is the one prev-build hand-off (cycle start);
  prompts + profiles come from the repo (+ baked image); the
  enrichment-review overlay and golden dir are pooled outside builds
  and labels; taxonomy has a two-level content-hashed cache
  (stage corpus fingerprint + per-plan tree_sha ledger + prompt hash,
  zero-failure stamping) — the user corrected an initial wrong claim
  that taxonomy rebuilds blind. Stale --profiles comment in cycle.sh
  fixed (03e43d4): no last-promoted fallback exists, by design.

### In progress
- team-operations tooling still not adopted operationally — the README
  manual flow stays canonical until the verification drill runs.

### Next
- Run the sandboxed drill (stages 0–6) on the work PC; then the
  real-topology provisioning (service user, CIFS fstab, PC-2 operator
  accounts) and the A8 hand-over cycle.
- NAS §A4 asks; serving-host migration §B after the sweep (unchanged).
- First seeded cycle: read taxonomy `cached` vs `extracted` — tree
  byte-stability across builds is unverified.
- Full-corpus re-enrichment cost per cycle: design a seed-from-prev
  path before the second real cycle if the cost bites.

### Flags
- Fleet images temporarily diverge after the web-only deploy
  (nora-web ahead of sira-query; healthz code= names the query code
  only). Re-unify at the next Phase 0.2 rebake, then refresh
  baselines.
- PC-2 service-user topology is planned, not executed — capture it as
  a draft decision when actually provisioned (deferred this session).
