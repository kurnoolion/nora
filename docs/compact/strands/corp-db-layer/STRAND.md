# corp-db-layer

**Status:** in-flight
**Opened:** 2026-10-08
**Landed:**
**Assignees:** kurnoolion
**Target modules:** stores, models, web, pipeline, env (nora_store service, docker)
**Active phase:** architecture

## Summary

Prepare NORA for a move to global corporate infrastructure. The current
per-environment directory layout (`<env_dir>` with file-based builds,
labels, and state) will not work there. Design and build a proper
database layer that can run in the corporate network — holding the BM25
index and the other artifacts nora-web needs (jobs, metrics, config,
feedback, corrections, golden sets) — and migrate the pipeline and
serving paths onto it.

## Notes

**Scope as landed (2026-10-24).** The strand delivered the corpus data
layer and its publish path, and put every nora stack on it:

- Four stores behind protocols with local and HTTP backends
  (`core/src/stores/`): Cells (schema-2 cell DBs, vanilla index + raw
  SIRA phrases, Overlay composed at load), Trees, History, Overlay
  (SQLite, one authority). nora-store hosts them under
  `/stores/<store>/v1`; nora-web is a pure client.
- Registry publish (`core.src.stores.publish`, `registry.db` per
  serving root): versioned write-once store files, release sets by
  build label, gated flips and one-command rollback, GC by retention;
  nora-store serves the registry's active set
  (`NORA_STORE_REGISTRY`), `POST /registry/reload` swaps changed units.
- Roll-out 2026-10-23: trial and both A/B stacks on their registries,
  the Overlay authority and trial on the SQLite Overlay, retrieval
  unchanged on each (0 of 39 differ). Design: `store-split-design.md`,
  `store-schema.md`, `publish-design.md`; status: `phase-4-plan.md`.

**Out of this strand**, by the architect's scoping of 2026-10-23: the
corp transfer and corp backend (a later 4c strand); the web runtime DBs
(jobs, metrics, config, feedback) and golden samples behind nora-store
(a separate strand) — the Summary above named them, they stay
nora-web's own files for now. Host retirement of the flat path (env
backups, pre-4a image sets, label directories) follows a soak.
