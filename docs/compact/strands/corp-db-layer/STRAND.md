# corp-db-layer

**Status:** in-flight
**Opened:** 2026-10-08
**Landed:**
**Assignees:** kurnoolion
**Target modules:** env, vectorstore, web, pipeline, query
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
