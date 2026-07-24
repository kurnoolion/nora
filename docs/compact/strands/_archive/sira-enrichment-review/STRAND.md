# sira-enrichment-review

**Status:** landed
**Opened:** 2026-07-20
**Landed:** 2026-07-24
**Assignees:** kurnoolion
**Target modules:** web
**Active phase:** development

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

Landed on 2026-07-24 with 8 promoted decisions: D-160, D-161, D-162, D-163, D-164, D-165, D-166, D-167
