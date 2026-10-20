# jev-experimentation

**Status:** in-flight
**Opened:** 2026-10-13
**Landed:**
**Assignees:** Hanif
**Target modules:** decision, query, env, eval, sandbox
**Active phase:** development

## Summary

Explore replacing LLM-based decision points in NORA with a typed, calibrated
decision model (Jev-class), starting with query classification. Hosted Jev is
ruled out — closed weights, no self-hosting, no external egress from the
build/serve environment — so the target is an open-weights Jev-class model
served in-corp. Per architect directive: (1) characterize the current
query-classification implementation and its class taxonomy; (2) inventory
internal infra capability against candidate hardware and runtime requirements;
(3) design a decision-model flow replacing today's LLM decisions, including
whether it runs before or after query expansion, with a selectable roster
mirroring the LLM roster and legacy as the configurable default; (4) design
flag-gated experimentation/migration phases — decision path behind a flag,
legacy without it — and run a trial stack on separate ports to A/B against
legacy; (5) rebuild the production image with the flag default-on once
validated; (6) execute the phases in order without breaking existing behavior.
Patterned on the `corp-db-layer` strand.

## Notes

<!-- appended to over the strand's lifetime -->

- 2026-10-20: the strand's code is merged to `main`. The strand stays
  in-flight: phases 4 (trial stack) and 5 (default flip) are open, the
  phase-3 gate is not met (D-DRAFT-21), and the drafts in
  `decisions-draft.md` are not yet promoted to `DECISIONS.md`.
