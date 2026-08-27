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
