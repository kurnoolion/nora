# Draft decisions — llm-roster-docs

Drafts for `/close-session` triage; not yet promoted to DECISIONS.md.

## D-DRAFT-1 — The deployed roster file lives under `WEB_STATE_DIR`, not `env_dir`

**Context.** `NORA_LLM_CONFIG` names a path the container must be able to read,
so the roster file has to sit inside a mounted volume. `nora-web` mounts five:
`${NORA_ENV_DIR}:/data/env`, `${WEB_STATE_DIR}:/data/web-state`,
`${FEEDBACK_DIR}`, `${CORRECTIONS_DIR}`, and `${GOLDEN_DIR}`. Documenting the
variable without naming a specific one would leave operators to pick, and the
most obvious pick is wrong.

**Decision.** Document `/data/web-state/llm.json`. `WEB_STATE_DIR` is per-stack,
`docker/env.example` already describes it as holding "jobs/metrics/config DBs",
`NORA_CONFIG_DB` already lives there, and neither `promote.sh` nor
`serve-flip.sh` references it — so a roster there survives promote labels. No
compose change, no new mount, no new variable.

**Alternative considered and rejected — a roster under `/data/env`.** It is the
mount an operator reaches for first, since `env_dir` is where deployment data
lives and it needs no explanation. Rejected because on a serving host
`NORA_ENV_DIR` points at a promoted label snapshot: `promote.sh` builds each
label fresh via `cp -al` hardlinks containing only `nora/out/*` and `sira/*`, so
a roster placed there is silently absent from the next label. The failure mode
is the bad one — the picker just stops rendering, and
`_resolve_llm_config_path()` warns to a log nobody is reading. `GOLDEN_DIR`
exists in the compose file for exactly this reason ("never live inside a
promoted snapshot"), so this is the second instance of a known hazard, not a new
one.

**Alternative considered and rejected — add a dedicated `LLM_CONFIG_FILE`
mount.** This was the first proposal, mirroring the `GOLDEN_DIR` precedent
directly: a new compose volume with a default, giving the roster a home that
owes nothing to the other mounts' semantics. Rejected on YAGNI once
`WEB_STATE_DIR` was checked and found to already satisfy every requirement —
mounted, per-stack, promote-independent, and already the declared home for
config. The mount would also have needed a safe default for the unset case, and
the obvious `${LLM_CONFIG_FILE:-/dev/null}` is a trap: `/dev/null` mounts as a
character device, `Path.is_file()` returns False, and every rosterless
deployment would log the "missing or unreadable" warning on every boot. Adding a
mount to avoid a problem that a documented path already avoids is machinery for
its own sake.

**Consequences.** Operators get one recipe that works on both a dev PC and a
serving host. `docs/provisioning-checklist.md` stays untouched. If a future
deployment needs the roster pooled ACROSS stacks rather than per-stack, this
decision is the thing to revisit — `WEB_STATE_DIR` is deliberately per-stack, so
two stacks sharing one roster would need either a symlink or the rejected
dedicated mount.
