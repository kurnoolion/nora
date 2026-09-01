"""Tests for req-ID bubbles in the web markdown renderer (strand req-id-bubbles).

`render_markdown_bubbles` wraps `render_markdown` and turns req IDs that the
answer's own retrieval payload knows about into click-to-expand badges. The
anchor set is a literal id set — never a req-ID pattern — so these tests use
non-VZW id shapes deliberately: a regex-based implementation would pass the
VZW cases and fail the rest.
"""

from __future__ import annotations

import re

from markupsafe import Markup

from core.src.web.markdown_render import render_markdown, render_markdown_bubbles


ANSWER = "Device shall follow VZ_REQ_LTEAT_45 before detach."


# ── Degradation ────────────────────────────────────────────────


class TestNoIds:
    def test_empty_ids_is_byte_identical_to_plain_render(self):
        assert render_markdown_bubbles(ANSWER, []) == render_markdown(ANSWER)

    def test_none_ids_is_byte_identical_to_plain_render(self):
        assert render_markdown_bubbles(ANSWER, None) == render_markdown(ANSWER)

    def test_empty_text_returns_empty_markup(self):
        assert render_markdown_bubbles("", ["VZ_REQ_LTEAT_45"]) == Markup("")

    def test_id_not_present_in_text_bubbles_nothing(self):
        out = render_markdown_bubbles(ANSWER, ["VZ_REQ_OTHER_1"])
        assert "req-bubble" not in out


# ── Bubbling ───────────────────────────────────────────────────


class TestBubbles:
    def test_req_id_in_prose_becomes_a_bubble(self):
        out = render_markdown_bubbles(ANSWER, ["VZ_REQ_LTEAT_45"])
        assert "req-bubble" in out
        assert 'hx-get="/api/req/VZ_REQ_LTEAT_45"' in out

    def test_bubble_body_loads_lazily_not_eagerly(self):
        # find_req scans parse trees; N bubbles must cost 0 lookups until clicked
        out = render_markdown_bubbles(ANSWER, ["VZ_REQ_LTEAT_45"])
        assert 'hx-trigger="revealed once"' in out

    def test_root_path_prefixes_the_endpoint(self):
        out = render_markdown_bubbles(ANSWER, ["VZ_REQ_LTEAT_45"], root_path="/nora2")
        assert 'hx-get="/nora2/api/req/VZ_REQ_LTEAT_45"' in out

    def test_inline_code_is_bubbled(self):
        # LLMs routinely backtick req IDs; skipping <code> would drop most bubbles
        out = render_markdown_bubbles("See `VZ_REQ_LTEAT_45` here.", ["VZ_REQ_LTEAT_45"])
        assert "req-bubble" in out

    def test_every_occurrence_is_bubbled(self):
        text = "VZ_REQ_A_1 and again VZ_REQ_A_1."
        out = render_markdown_bubbles(text, ["VZ_REQ_A_1"])
        assert out.count('class="req-bubble"') == 2

    def test_repeated_id_gets_unique_collapse_targets(self):
        # duplicate DOM ids would make the 2nd badge open the 1st panel
        text = "VZ_REQ_A_1 and again VZ_REQ_A_1."
        out = render_markdown_bubbles(text, ["VZ_REQ_A_1"])
        ids = re.findall(r'id="(reqb-[^"]+)"', str(out))
        assert len(ids) == 2 and len(set(ids)) == 2


# ── Corpus-agnostic: non-VZW id shapes ─────────────────────────


class TestNonVzwIdShapes:
    def test_foreign_mno_id_bubbles(self):
        text = "Per REQ-TMO-5G-0042 the device shall comply."
        out = render_markdown_bubbles(text, ["REQ-TMO-5G-0042"])
        assert "req-bubble" in out

    def test_lowercase_and_dotted_id_bubbles(self):
        text = "See req.band.7 for details."
        out = render_markdown_bubbles(text, ["req.band.7"])
        assert "req-bubble" in out

    def test_longest_id_wins_when_one_is_a_prefix_of_another(self):
        text = "VZ_REQ_A_1 and VZ_REQ_A_12 differ."
        out = render_markdown_bubbles(text, ["VZ_REQ_A_1", "VZ_REQ_A_12"])
        # the longer id must match whole, not as "VZ_REQ_A_1" + stray "2"
        assert 'hx-get="/api/req/VZ_REQ_A_12"' in out
        assert ">VZ_REQ_A_12</a>" in out


# ── Markup safety ──────────────────────────────────────────────


class TestMarkupNotCorrupted:
    def test_id_inside_a_link_href_is_not_rewritten(self):
        out = render_markdown_bubbles(
            "[link](http://x/VZ_REQ_A_1)", ["VZ_REQ_A_1"])
        assert "http://x/VZ_REQ_A_1" in out
        assert "req-bubble" not in out

    def test_id_inside_link_text_is_not_bubbled(self):
        # no nested <a> inside <a>
        out = render_markdown_bubbles("[VZ_REQ_A_1](http://x)", ["VZ_REQ_A_1"])
        assert "req-bubble" not in out

    def test_id_inside_fenced_block_stays_verbatim(self):
        text = "```\nVZ_REQ_A_1\n```"
        out = render_markdown_bubbles(text, ["VZ_REQ_A_1"])
        assert "req-bubble" not in out
        assert "VZ_REQ_A_1" in out

    def test_dangerous_tags_still_stripped(self):
        out = render_markdown_bubbles(
            "<script>alert(1)</script> VZ_REQ_A_1", ["VZ_REQ_A_1"])
        assert "<script>" not in out
        assert "req-bubble" in out

    def test_id_with_html_metacharacters_is_escaped(self):
        text = "See A<B_1 now."
        out = render_markdown_bubbles(text, ["A<B_1"])
        assert "A&lt;B_1" in out
