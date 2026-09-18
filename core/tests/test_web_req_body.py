"""Tests for the requirement-body renderer behind the Ask-page bubble
(strand req-bubble-tables).

`render_req_body` renders `Requirement.text` — CORPUS content, not LLM
output — for the bubble panel. The parser inlines tables into that text at
their document position, in the three forms the corpus can produce:

  - lossless ``<table>`` HTML (Docling; merged-cell DOCX per D-199)
  - a GFM pipe table (`render_table_markdown`)
  - the compact ``[Table: …]`` line (D-198), which may span several lines

Everything that is not a table stays literal text. That is the whole point of
segmenting instead of running the body through `render_markdown`: requirement
prose is underscore- and asterisk-dense (req IDs, spec references), and
markdown emphasis rules have no business rewriting corpus content.
"""

from __future__ import annotations

from markupsafe import Markup

from core.src.web.markdown_render import render_req_body


# ── Robustness ─────────────────────────────────────────────────


class TestEmpty:
    def test_empty_string_returns_empty_markup(self):
        assert render_req_body("") == Markup("")

    def test_none_returns_empty_markup(self):
        assert render_req_body(None) == Markup("")


# ── Prose stays literal ────────────────────────────────────────


class TestProseIsNotMarkdown:
    def test_underscore_dense_req_id_is_not_emphasised(self):
        """The regression this renderer exists to avoid: running corpus text
        through a full markdown pass turns `VZ_REQ_LTEDATARETRY_7748` into
        emphasis soup."""
        out = str(render_req_body("See VZ_REQ_LTEDATARETRY_7748 for detail."))
        assert "VZ_REQ_LTEDATARETRY_7748" in out
        assert "<em>" not in out

    def test_asterisks_are_not_bold(self):
        out = str(render_req_body("The value *shall* be 2*3 per spec."))
        assert "<strong>" not in out
        assert "*shall*" in out

    def test_hash_is_not_a_heading(self):
        out = str(render_req_body("# 3 of the 5 bands"))
        assert "<h1" not in out

    def test_html_in_prose_is_escaped(self):
        out = str(render_req_body("Compare a<b and c>d."))
        assert "a&lt;b" in out
        assert "c&gt;d" in out

    def test_script_in_prose_is_escaped_not_executed(self):
        out = str(render_req_body("<script>evil()</script>"))
        assert "<script>" not in out
        assert "&lt;script&gt;" in out


# ── Form 2: GFM pipe table ─────────────────────────────────────


PIPE_TABLE = """| Band | Required |
| --- | --- |
| B13 | Yes |
| B66 | No |"""


class TestPipeTable:
    def test_renders_as_a_table(self):
        out = str(render_req_body(PIPE_TABLE))
        assert "<table" in out
        assert "<th>Band</th>" in out
        assert "<td>B13</td>" in out

    def test_no_raw_pipes_survive(self):
        out = str(render_req_body(PIPE_TABLE))
        assert "| B13 |" not in out

    def test_headerless_row_only_table_renders(self):
        """`render_table_markdown` emits rows with no header line when the
        source table had no headers."""
        out = str(render_req_body("| a | b |\n| c | d |"))
        assert "<table" in out
        assert "a" in out and "d" in out


# ── Form 1: lossless provider HTML ─────────────────────────────


HTML_TABLE = (
    "<table><tr><th colspan=\"2\">Bands</th></tr>"
    "<tr><td rowspan=\"2\">B13</td><td>Yes</td></tr>"
    "<tr><td>No</td></tr></table>"
)


class TestHtmlTable:
    def test_renders_as_a_real_table(self):
        out = str(render_req_body(HTML_TABLE))
        assert "<table" in out
        assert "&lt;table" not in out

    def test_merged_cell_attributes_survive(self):
        """D-199's whole purpose: merged structure reaches the reader."""
        out = str(render_req_body(HTML_TABLE))
        assert 'colspan="2"' in out
        assert 'rowspan="2"' in out

    def test_script_inside_provider_html_is_stripped(self):
        src = "<table><tr><td><script>evil()</script>B13</td></tr></table>"
        out = str(render_req_body(src))
        assert "<script" not in out
        assert "B13" in out

    def test_event_handler_attribute_is_stripped(self):
        src = "<table onclick=\"evil()\"><tr><td>B13</td></tr></table>"
        out = str(render_req_body(src))
        assert "onclick" not in out
        assert "<table" in out

    def test_non_allowlisted_tag_inside_table_is_dropped(self):
        src = "<table><tr><td><a href=\"javascript:evil()\">B13</a></td></tr></table>"
        out = str(render_req_body(src))
        assert "javascript:" not in out
        assert "B13" in out

    def test_style_attribute_is_dropped(self):
        src = "<table><tr><td style=\"position:fixed\">B13</td></tr></table>"
        out = str(render_req_body(src))
        assert "style=" not in out


# ── Form 3: the compact line stays text ────────────────────────


class TestCompactTableLine:
    def test_compact_line_is_not_promoted_to_a_table(self):
        """D-198 made this form deliberately lossy — it has no grid to
        recover, so rendering one would be inventing structure."""
        out = str(render_req_body("[Table: Band | Required | Notes]"))
        assert "<table" not in out
        assert "[Table: Band | Required | Notes]" in out

    def test_multiline_compact_line_survives(self):
        """D-198: a cell containing newlines makes the compact form
        multi-line; consumers must not assume one line."""
        src = "[Table: Band\nB13 | Required\nYes]"
        out = str(render_req_body(src))
        assert "<table" not in out
        assert "B13" in out


# ── Document order ─────────────────────────────────────────────


class TestMixedBody:
    def test_prose_table_prose_keeps_document_order(self):
        src = f"Intro sentence.\n\n{PIPE_TABLE}\n\nClosing sentence."
        out = str(render_req_body(src))
        assert out.index("Intro sentence") < out.index("<table")
        assert out.index("<table") < out.index("Closing sentence")

    def test_two_tables_both_render(self):
        src = f"{HTML_TABLE}\n\nBetween.\n\n{PIPE_TABLE}"
        out = str(render_req_body(src))
        assert out.count("<table") == 2
        assert "Between." in out
