# req-bubble-tables

**Status:** in-flight
**Opened:** 2026-09-18
**Landed:**
**Assignees:** Hanif
**Target modules:** web
**Active phase:**

## Summary

Render tables properly inside the Ask-page req-ID bubble. `req.text` already
carries them inline at document position in three forms — lossless `<table>`
HTML (Docling, and merged-cell DOCX per D-199), a GFM pipe table
(`render_table_markdown`), and the compact `[Table: …]` line (D-198, which may
span several lines). `_req_bubble.html` escapes all three under
`white-space: pre-wrap`, so a reader opening a table-bearing requirement sees
markup rather than a table. Render-side only: no parser, extraction, or
`req_tree.find_req` change — the content is already in `text` at the right
position, and `drop_grid` can leave `TableData.headers`/`rows` empty anyway.
Driver: manager feedback on the Ask answer page.

## Notes

<!-- appended to over the strand's lifetime -->
