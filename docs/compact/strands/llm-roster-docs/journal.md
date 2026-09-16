# Journal — llm-roster-docs

## 2026-09-15 — NORA_LLM_CONFIG was implemented but never documented for operators

**The gap.** `NORA_LLM_CONFIG` selects which file a deployment loads its LLM
roster from. It appears in exactly four places: `core/src/env/config.py`,
`core/src/env/MODULE.md`, the tests, and COMPACT journals. Zero operator-facing
documentation — not in any of the five `docker/*.example` files, not in
`docker/README.md`, not in `docs/provisioning-checklist.md`. An operator
standing up a box from the runbooks cannot discover that the Ask-page roster
exists, and a wrong path fails silently to a rosterless config. The var landed
with `llm-roster-deploy` (D-218..D-234) and the docs never followed it.

**The trap that shaped the fix.** The obvious recipe — put the roster in
`env_dir`, which is already mounted at `/data/env` — is wrong on a serving
host. `NORA_ENV_DIR` there points at a promoted label snapshot, and
`promote.sh` builds each label fresh via hardlinks containing only `nora/out/*`
and `sira/*`. A roster dropped in `env_dir` vanishes at the next promote. The
repo already hit this once: `GOLDEN_DIR` exists as a separate mount precisely so
eval samples "never live inside a promoted snapshot".

**Where it actually belongs.** `WEB_STATE_DIR` — mounted at `/data/web-state`,
described in `docker/env.example` as "per stack: jobs/metrics/config DBs", and
referenced by neither `promote.sh` nor `serve-flip.sh`. Config is already in its
charter: `NORA_CONFIG_DB` lives there today, which is also the proof that the
app reads and writes that path in-container. So the roster needs no new mount
and no compose change — see D-DRAFT-1.

**Verified, not assumed.** Three checks. (1) A roster at an arbitrary path
loads: `config_source == "env"`, both entries parsed with their
`reasoning_control`. (2) The documented failure mode is real: a missing path
logs `NORA_LLM_CONFIG=... is missing or unreadable — falling back to
config/llm.json` and yields zero providers. (3) End-to-end, the app served
`/test` with `id="ask-provider"` rendering both roster entries, the declared
default selected, and the Fast/Think toggle present.

**Not verified in-container.** The Docker daemon was down on this machine with
no images and no `.env` files, so the stack itself was not brought up. The
container claim rests on the compose file: `nora-web` mounts
`${WEB_STATE_DIR}:/data/web-state` and already sets
`NORA_CONFIG_DB: /data/web-state/nora_config.db`. Same mount, different file.
Worth one confirmation on a host that has the images.

**Scope note.** `docs/provisioning-checklist.md` was deliberately left alone.
Section A is build-machine greenfield, B is a one-time serving-host migration
runbook, C is a stub — none is a natural home for an optional serve-side knob,
and forcing one in would have been padding.
