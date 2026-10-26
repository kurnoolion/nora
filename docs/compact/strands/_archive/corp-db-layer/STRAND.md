# corp-db-layer

**Status:** landed
**Opened:** 2026-10-08
**Landed:** 2026-10-25
**Assignees:** kurnoolion
**Target modules:** stores, store_service, models, web, pipeline, env, llm (docker)
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

Landed on 2026-10-25 with 33 promoted decisions: D-255, D-256, D-257, D-258, D-259, D-260, D-261, D-262, D-263, D-264, D-265, D-266, D-267, D-268, D-269, D-270, D-271, D-272, D-273, D-274, D-275, D-276, D-277, D-278, D-279, D-280, D-281, D-282, D-283, D-284, D-285, D-286, D-287. Deferred, kept here: D-DRAFT-5, D-DRAFT-21.
