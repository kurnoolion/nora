# self-serve-ingestion

**Status:** in-flight
**Opened:** 2026-11-03
**Landed:**
**Assignees:** kurnoolion
**Target modules:** web, pipeline, stores, eval (plus the host-side runner under docker/)
**Active phase:**

## Summary

Let the requirements-management team ingest a new release of an existing
MNO on their own, from the web, without the Linux CLI: they point the page
at a shared-folder path with the MNO and release month; the system stages
the documents, runs the existing cycle on that one cell (parse, taxonomy,
enrichment with its verify gate, convert, publish as a delta with History
rebuilt), runs the golden eval, records the gate, and flips the serve,
fully self-service, with rollback one click away. The cycle runs through a
host-side runner the web enqueues to, never from inside the web container.
The same operation is exposed as an API endpoint so an LLM-side skill can
orchestrate it later. New MNOs stay out of scope: their first release is
ingested by hand.

## Notes

- 2026-11-07 — scope widened: **Purge**, the fourth ingest verb. A dead
  ingest (abandoned, rolled back, failed, cancelled) left six things
  behind that an administrator had to find by hand; Purge removes them
  all by the ingest record and never touches the serving set.

