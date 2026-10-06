## D-DRAFT-1 — Requirement bodies render through a table-only promoter, never a markdown pass

**Context.** Manager feedback on the Ask answer page: the req-ID bubble should
show tables. The tables are already present — the parser inlines each one into
`Requirement.text` at its document position (`structural_parser.py`,
`block.html or render_table_markdown(...)`) — but `_req_bubble.html` escaped the
whole body under `white-space: pre-wrap`, so a reader saw `<table>` markup or
raw `|` pipes. The web module already owns a renderer that turns markdown into
HTML (`render_markdown`, the `md` filter), so reusing it was the obvious move.

**Decision (Hanif, 2026-09-18).** A separate `render_req_body` renders the
requirement body for the bubble: it promotes tables to markup and leaves
everything else as escaped literal text. Registered as the `req_body` Jinja
filter. `render_markdown` is not reused and no markdown pass runs over corpus
text.

**Why.** `render_markdown` renders LLM prose, where markdown emphasis is the
point. Requirement bodies are corpus content, where it is a liability: req IDs
are underscore-dense (`VZ_REQ_LTEDATARETRY_7748`), and `**MUST**`-style emphasis
is part of the spec's own text, not formatting to interpret. A full markdown
pass would silently rewrite the corpus the bubble exists to quote faithfully.

**Rejected alternatives.**
- *Pipe the body through `render_markdown`.* One line, and it renders pipe
  tables for free — but it puts emphasis rules in contact with every requirement
  body, and the failure is silent corruption rather than a visible error.
- *Render only the structured `Requirement.tables` beneath the prose.* See
  D-DRAFT-3.

**Consequences.** `markdown_render.py` now hosts two renderers with deliberately
different contracts — LLM prose vs corpus text — and the distinction has to hold
at future call sites. Any surface that wants a requirement body rendered must
reach for `req_body`, not `md`. The compact `[Table: …]` form stays text, which
is consistent with D-198 making it deliberately lossy: it carries no grid to
recover, and building one would invent structure the corpus never had.

## D-DRAFT-2 — Provider table HTML is rebuilt from an allowlist, on stdlib

**Context.** D-199 has the DOCX extractor emit lossless HTML for merged-cell
tables, and Docling supplies HTML for layout-provider tables; both land verbatim
in `Requirement.text`. Rendering them means third-party markup reaching the
answer page's DOM unescaped for the first time. `render_markdown` already
handles the adjacent problem with `_DANGEROUS_TAG_RE`, a denylist of
`script`/`style`/`iframe`/`object`/`embed`/`svg`/`math`.

**Decision (Hanif, 2026-09-18).** Table HTML is parsed with stdlib
`html.parser.HTMLParser` and rebuilt from an allowlist: tags
`table/thead/tbody/tfoot/tr/th/td/caption`, attributes `colspan`/`rowspan` only.
`script` and `style` drop with their content; any other tag is unwrapped so its
text survives; all text is escaped.

**Why.** A denylist has to predict every dangerous construct and stays wrong as
the web grows. The set of tags a table legitimately needs is small and closed,
which makes an allowlist both safer and shorter here. `colspan`/`rowspan`
survive because merged structure is the entire reason D-199 renders HTML at all
— dropping them would render the table while discarding what made it worth
keeping losslessly.

**Rejected alternatives.**
- *Reuse `_DANGEROUS_TAG_RE`.* Wrong instrument for markup we are about to trust
  into the DOM, and it would carry presentation attributes through.
- *Add `bleach` or `nh3`.* A well-tested sanitizer, but the repo has no
  sanitizer dependency and is stdlib-first throughout (`requirements.txt` has no
  HTML-sanitizing package); a ~40-line closed-allowlist parser does not justify
  a new dependency on every deployment.

**Consequences.** Provider tables lose styling attributes by design — they
inherit the panel's own table CSS instead. A future need for another table
feature (e.g. `<colgroup>`, alignment) is an explicit allowlist edit, which is
the intended friction.

## D-DRAFT-3 — The bubble reads the inlined `text`, not the structured `tables`

**Context.** A requirement carries its tables twice: inlined into `text` at
document position, and structured on `Requirement.tables` as
`TableData(headers, rows, html, source)`. The bubble needed one of them, and
`req_tree.find_req` currently selects neither `tables` nor anything beyond
`{mno, release, plan, doc_id, title, text}`.

**Decision (Hanif, 2026-09-18).** The bubble renders from `text`.
`req_tree.find_req` is unchanged — no new field selected, no new query path.

**Why.** `text` is the only representation that preserves where each table sits
relative to the prose, which is what makes the quoted requirement readable.
`TableData` is also not reliably populated: the parser's `drop_grid` empties
`headers`/`rows` when a layout provider supplied HTML and table-anchored
extraction is off, so for those corpora the structured path yields nothing at
all — the failure would be invisible in the demo env and appear only on a real
provider-backed corpus.

**Rejected alternatives.**
- *Select `tables` in `find_req` and render them under the prose.* Structured
  input, no HTML parsing needed — but tables detach from their position, and
  `drop_grid` makes it empty on exactly the corpora that motivated the request.

**Consequences.** `find_req` stays the single shared lookup for the Requirement
Browser, the Eval Studio picker and the bubble, with no bubble-specific shape.
The renderer carries the cost instead: it has to recognise all three inlined
forms from text alone, which is what D-DRAFT-1 and D-DRAFT-2 specify.
