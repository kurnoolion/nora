# sira-enrichment-review

**Status:** in-flight
**Opened:** 2026-07-20
**Landed:**
**Assignees:** kurnoolion
**Target modules:** web
**Active phase:** architecture

## Summary

A web interface for domain experts to review and correct SIRA's
per-requirement enrichments. Browse by MNO → Release → Plan; a table shows
each requirement (req_id, text) with its enrichment keywords as deletable
chips, per-requirement delete-all + undo, and a text box to add
comma-separated keywords. Purpose is evaluative as much as corrective:
experts "debug" enrichments to judge whether they're meaningful in general
and to surface systematic errors that feed back into the enrichment LLM
prompt.

## Notes

<!-- appended to over the strand's lifetime -->
