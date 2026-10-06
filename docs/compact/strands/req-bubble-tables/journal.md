## 2026-09-18 — Tables render inside the req-ID bubble

### Done this session
- `render_req_body` + the `req_body` Jinja filter: the bubble panel now renders
  the tables already inlined in `Requirement.text` instead of escaping them.
  Render-side only — no parser, extraction or `req_tree.find_req` change
  (D-DRAFT-1, D-DRAFT-3).
- All three corpus forms handled distinctly: provider `<table>` HTML (D-199)
  allowlist-sanitized on stdlib `HTMLParser` with `colspan`/`rowspan` kept
  (D-DRAFT-2); GFM pipe table rebuilt hand-rolled rather than via the markdown
  library's `tables` extension, which needs a header/delimiter pair and drops
  the headerless row-only form `render_table_markdown` also emits; compact
  `[Table: …]` (D-198) left as text.
- CSS: `pre-wrap` moved off the panel onto per-prose spans so it cannot leak
  into table cells and stop them wrapping; `.req-body` scrolls horizontally
  because the floating panel caps at 40rem.
- 21 new tests (20 renderer + 1 end-to-end route). Suite 1983 passed / 8 failed
  / 112 skipped — the 8 verified identical on `main` at 7395969 in a throwaway
  worktree, so not regressions from this branch.
- Red-green verified the end-to-end test rather than trusting it: reverting the
  template filter fails it, restoring passes 31/31.
- Team-mode gate checked ON against a live server, not a path-list unit test —
  `/api/req/` 200 with a rendered `<table>`, `/dashboard` 302.
- Browser-verified all three forms in the floating panel through stored
  shared-answer rows; merged cells (`colspan`/`rowspan`) render correctly.

### Next
- PR up; boss merges; then `/land-strand req-bubble-tables`.

### Flags
- `core/src/web/MODULE.md` Structure section for `markdown_render.py` is stale:
  it predates `render_markdown_bubbles` (strand req-id-bubbles) and now also
  omits `render_req_body`. Mechanically-derived content, so it belongs to
  `/regen-map`, not a hand edit.
- Ollama is down on this machine, so the live Ask lane was never exercised —
  bubble verification went through stored shared-answer rows only. The demo
  corpus and feedback DB were both mutated for fixtures and restored after.
- Suggested but not run: `/drift-check dev-module web` (design + implementation
  layers both touched this session).
