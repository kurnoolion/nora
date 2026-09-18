"""Markdown → HTML rendering for LLM-synthesized answers.

Wraps the `markdown` library with a fixed extension set tuned for the
shape of answers our pipeline produces (FACT, SUMMARIZE,
SINGLE_DOC etc): fenced code blocks, tables, sane lists, and inline
code. Returns Jinja-safe Markup so the template can interpolate
without further escaping.

Safety: we don't trust raw HTML in the LLM's answer. The renderer
strips `<script>` / `<style>` and similar dangerous tags. The
markdown library itself escapes HTML attributes; we additionally
disable raw-HTML passthrough so the LLM cannot inject arbitrary
HTML into the rendered output. Citation tokens like
`(VZ_REQ_LTEDATARETRY_7748)` and `3GPP TS 24.301, Section 5.5.1.2.6`
are pure text and pass through unchanged.
"""

from __future__ import annotations

import itertools
import re
from html.parser import HTMLParser

import markdown as _markdown
from markupsafe import Markup, escape


_MD_EXTENSIONS = (
    "fenced_code",   # ```code blocks```
    "tables",        # | a | b |
    "sane_lists",    # - bullets / 1. numbered with proper nesting
    "nl2br",         # single newline → <br> (better fidelity to LLM
                     #   formatting where the model uses bare newlines
                     #   instead of blank-line paragraph breaks)
)


# Tags we'll strip outright (their content too) — they have no
# legitimate place in an analyzer's answer text.
_DANGEROUS_TAG_RE = re.compile(
    r"<\s*(script|style|iframe|object|embed|svg|math)\b[^>]*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
# Self-closing variants and stray openers without close — strip
# the tag.
_DANGEROUS_TAG_OPEN_RE = re.compile(
    r"<\s*/?\s*(script|style|iframe|object|embed|svg|math)\b[^>]*/?>",
    re.IGNORECASE,
)


def render_markdown(text: str) -> Markup:
    """Convert markdown source to HTML, return Jinja-safe Markup.

    Empty / None input → empty Markup (templates can interpolate
    safely without an `if`).
    """
    if not text:
        return Markup("")

    # Strip dangerous HTML before letting markdown have a go. The
    # `markdown` library by default passes inline HTML through; we
    # take the conservative route and remove tags that have no role
    # in answer text.
    cleaned = _DANGEROUS_TAG_RE.sub("", text)
    cleaned = _DANGEROUS_TAG_OPEN_RE.sub("", cleaned)

    html = _markdown.markdown(cleaned, extensions=list(_MD_EXTENSIONS))
    return Markup(html)


# Req-ID bubbles (strand req-id-bubbles). The ids come from the answer's own
# retrieval payload — never a req-ID regex — so this works on any MNO's id
# format. See core/src/web/MODULE.md.
#
# Substitution runs over the RENDERED html, alternating on tags so a match
# inside a tag's attributes can never be rewritten, and skipping the contents
# of <a> (no nested links) and <pre> (fenced blocks stay verbatim). Inline
# <code> is deliberately NOT skipped: LLMs routinely backtick req IDs, and
# skipping it would drop most bubbles.
_SKIP_SPANS_RE = re.compile(r"<(a|pre)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]*>")


def _bubble_html(req_id: str, nth: int) -> str:
    """One collapsed badge + the panel its click expands. The panel body is
    loaded from `/api/req/<req_id>` on first open, so an answer with N bubbles
    costs zero requirement lookups until one is actually opened.

    `nth` makes the collapse target unique: the same req ID cited twice in one
    answer would otherwise emit duplicate DOM ids, and Bootstrap would open the
    first panel when the second badge is clicked.
    """
    safe = escape(req_id)
    target = f"reqb-{nth}-" + re.sub(r"[^A-Za-z0-9_-]", "-", req_id)
    # The fetch fires on a custom `bubbleopen` event that app.js dispatches
    # from Bootstrap's `show.bs.collapse`, so EVERY open path — hover, click,
    # keyboard focus — loads the body through one route and no path can be
    # forgotten. `once` keeps it to a single request per badge.
    #
    # Do not be tempted back to `hx-trigger="revealed"`: htmx evaluates that on
    # scroll events, and a panel hidden by .collapse fires no scroll when it
    # opens, so the request only went out when an unrelated reflow happened —
    # which reads as "stuck on Loading…".
    return (
        f'<span class="req-bubble">'
        f'<a class="req-bubble-badge badge bg-primary-subtle text-primary-emphasis '
        f'border border-primary-subtle text-decoration-none" '
        f'role="button" tabindex="0" aria-controls="{target}" '
        f'aria-expanded="false" title="Show requirement {safe}" '
        f'hx-get="REQ_ENDPOINT/{safe}" hx-target="#{target}-body" '
        f'hx-trigger="bubbleopen once" hx-swap="innerHTML">{safe}'
        f'<i class="bi bi-chevron-down req-bubble-caret ms-1"></i></a>'
        f'<span class="collapse" id="{target}">'
        f'<span class="req-bubble-body d-block border rounded p-2 my-2 small" '
        f'id="{target}-body">'
        f'<span class="text-muted">Loading {safe}…</span>'
        f"</span></span></span>"
    )


def _linkify_req_ids(html: str, req_ids, root_path: str = "") -> str:
    ids = sorted({r for r in (req_ids or []) if r and isinstance(r, str)},
                 key=len, reverse=True)
    if not ids:
        return html
    id_re = re.compile("|".join(re.escape(r) for r in ids))
    counter = itertools.count()

    def sub_text(text: str) -> str:
        return id_re.sub(
            lambda m: _bubble_html(m.group(0), next(counter)).replace(
                "REQ_ENDPOINT", f"{root_path}/api/req"),
            text,
        )

    out: list[str] = []
    pos = 0
    # Walk the html, copying protected spans and tags through untouched and
    # substituting only in the text between them.
    for m in re.finditer(f"{_SKIP_SPANS_RE.pattern}|{_TAG_RE.pattern}",
                         html, re.IGNORECASE | re.DOTALL):
        out.append(sub_text(html[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(sub_text(html[pos:]))
    return "".join(out)


def render_markdown_bubbles(text: str, req_ids=None, root_path: str = "") -> Markup:
    """`render_markdown` plus click-to-expand req-ID bubbles.

    Degrades to plain `render_markdown` output when `req_ids` is empty, so a
    lane or a stored row that carries no ids renders exactly as before.
    """
    rendered = render_markdown(text)
    if not req_ids:
        return rendered
    return Markup(_linkify_req_ids(str(rendered), req_ids, root_path))


# Requirement-body rendering (strand req-bubble-tables). Separate from
# `render_markdown` on purpose: that one renders LLM prose, this one renders
# CORPUS text, where markdown emphasis rules are a liability rather than a
# feature (req IDs and spec references are underscore- and asterisk-dense).
# Only tables are promoted to markup; everything else stays literal.
#
# The parser inlines a table into `Requirement.text` at its document position
# (structural_parser.py:2291) in one of three forms:
#   1. lossless <table> HTML — Docling, and merged-cell DOCX per D-199
#   2. a GFM pipe table from `render_table_markdown`
#   3. the compact `[Table: …]` line (D-198), which may span several lines
# Forms 1 and 2 become real tables here. Form 3 stays text: D-198 made it
# deliberately lossy, so there is no grid to recover and building one would be
# inventing structure the corpus never had.

_HTML_TABLE_RE = re.compile(r"<table\b.*?</table\s*>", re.IGNORECASE | re.DOTALL)
# A pipe-table line: leading pipe, at least two cells. The leading pipe is what
# keeps the compact `[Table: a | b]` form out of this branch.
_PIPE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")
_DELIMITER_CELL_RE = re.compile(r"^:?-{2,}:?$")

# Tags a corpus table may keep, and the only attributes worth carrying. Merged
# structure (colspan/rowspan) is the entire reason D-199 renders HTML at all,
# so it survives; presentation attributes do not.
_TABLE_TAGS = frozenset(
    {"table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption"})
_TABLE_ATTRS = frozenset({"colspan", "rowspan"})
# Tags whose CONTENT is dropped along with the tag. Anything else outside the
# allowlist is unwrapped — its text is corpus content and belongs to the reader.
_DROP_CONTENT_TAGS = frozenset({"script", "style"})


class _TableSanitizer(HTMLParser):
    """Rebuild provider table HTML from an allowlist.

    This is third-party markup (a layout provider's output) landing in the
    answer page's DOM. A denylist would be the wrong instrument: it has to
    predict every dangerous construct, and the set of tags a table legitimately
    needs is tiny and closed.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._suppress_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _DROP_CONTENT_TAGS:
            self._suppress_depth += 1
            return
        if self._suppress_depth or tag not in _TABLE_TAGS:
            return
        kept = "".join(
            f' {k}="{escape(v)}"'
            for k, v in attrs
            if k in _TABLE_ATTRS and v is not None
        )
        self._out.append(f"<{tag}{kept}>")

    def handle_endtag(self, tag):
        if tag in _DROP_CONTENT_TAGS:
            self._suppress_depth = max(0, self._suppress_depth - 1)
            return
        if self._suppress_depth or tag not in _TABLE_TAGS:
            return
        self._out.append(f"</{tag}>")

    def handle_data(self, data):
        if self._suppress_depth:
            return
        self._out.append(str(escape(data)))

    def result(self) -> str:
        return "".join(self._out)


def _sanitize_table_html(html: str) -> str:
    parser = _TableSanitizer()
    parser.feed(html)
    parser.close()
    return parser.result()


def _render_pipe_table(lines: list[str]) -> str:
    """Build a table from `render_table_markdown`'s pipe output.

    Hand-built rather than handed to the markdown library for two reasons: the
    library needs a header/delimiter pair and so drops the headerless row-only
    form the renderer also emits, and running cells through markdown would put
    emphasis rules back in contact with corpus text.
    """
    rows = [
        [c.strip() for c in ln.strip().strip("|").split("|")]
        for ln in lines
    ]
    headers: list[str] = []
    if len(rows) >= 2 and all(_DELIMITER_CELL_RE.match(c) for c in rows[1] if c):
        headers, rows = rows[0], rows[2:]
    out = ["<table>"]
    if headers:
        out.append("<thead><tr>")
        out += [f"<th>{escape(h)}</th>" for h in headers]
        out.append("</tr></thead>")
    out.append("<tbody>")
    for row in rows:
        out.append("<tr>")
        out += [f"<td>{escape(c)}</td>" for c in row]
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def _render_prose(text: str) -> str:
    """Corpus prose: escaped, newlines preserved by the class's `pre-wrap`."""
    if not text.strip():
        return ""
    return f'<span class="req-body-text">{escape(text)}</span>'


def render_req_body(text: str) -> Markup:
    """Render `Requirement.text` for the Ask-page bubble panel.

    Tables become tables; everything else stays literal corpus text.
    """
    if not text:
        return Markup("")

    out: list[str] = []
    pos = 0
    # Pass 1: lift the lossless HTML tables out, so their own pipes and
    # newlines never reach the line-based pipe-table scan.
    for m in _HTML_TABLE_RE.finditer(text):
        out.append(_render_segment(text[pos:m.start()]))
        out.append(_sanitize_table_html(m.group(0)))
        pos = m.end()
    out.append(_render_segment(text[pos:]))
    return Markup("".join(out))


def _render_segment(text: str) -> str:
    """Pass 2 over a table-HTML-free segment: group contiguous pipe-table
    lines into tables, everything else into prose, preserving order."""
    if not text:
        return ""
    out: list[str] = []
    prose: list[str] = []
    pipes: list[str] = []

    def flush_prose():
        if prose:
            out.append(_render_prose("\n".join(prose)))
            prose.clear()

    def flush_pipes():
        if pipes:
            out.append(_render_pipe_table(list(pipes)))
            pipes.clear()

    for line in text.split("\n"):
        if _PIPE_LINE_RE.match(line):
            flush_prose()
            pipes.append(line)
        else:
            flush_pipes()
            prose.append(line)
    flush_prose()
    flush_pipes()
    return "".join(out)
