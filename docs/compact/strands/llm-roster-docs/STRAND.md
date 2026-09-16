# llm-roster-docs

**Status:** in-flight
**Opened:** 2026-09-15
**Landed:**
**Assignees:** Hanif
**Target modules:** docs, docker (no core modules)
**Active phase:** development

## Summary

`NORA_LLM_CONFIG` selects which file a deployment loads its LLM roster from,
but it appears in no operator-facing documentation — not in any
`docker/*.example`, not in `docker/README.md`, not in the provisioning
checklist. An operator standing up a box from the runbooks cannot discover
that the Ask-page provider roster exists or how to point at one, and a wrong
path fails silently to a rosterless config. This strand documents the variable
for the `nora-web` deployment, pointing at `/data/web-state/` — the per-stack
mount that already holds the Config DB and is untouched by `promote.sh`, so a
roster there survives promote labels. Documentation only: no code, no compose
change, no new mount.

## Notes

<!-- appended to over the strand's lifetime -->
