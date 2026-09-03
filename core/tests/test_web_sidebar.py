"""Sidebar nav-label markup (strand collapsible-sidebar).

The collapsed rail hides labels with `.nav-label { display: none }`, which only
works if every label is wrapped in that span — a bare text node after the icon
cannot be targeted by CSS. Wrapping them was 15 near-identical hand edits across
base.html, and a missed one is invisible until someone collapses the rail and
sees a stray word floating next to the icons. These tests are that check.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

from core.src.web.app import templates

# The sidebar <nav>, up to its closing tag — scopes the assertions so the
# navbar's own links are not swept in.
_SIDEBAR = re.compile(r'<nav class="sidebar"[^>]*>(.*?)</nav>', re.S)
_NAV_LINK = re.compile(r'<a class="nav-link[^>]*>.*?</a>', re.S)
_WRAPPED = re.compile(r'<span class="nav-label">.*?</span>', re.S)


def _sidebar_links(team: bool) -> list[str]:
    html = templates.get_template("base.html").render(
        root_path="",
        request=SimpleNamespace(cookies={}),
        is_team_restricted=lambda _req: team,
    )
    body = _SIDEBAR.search(html)
    assert body, "sidebar <nav> not found in rendered base.html"
    links = _NAV_LINK.findall(body.group(1))
    assert links, "no .nav-link anchors rendered in the sidebar"
    return links


def _assert_labels_wrapped(team: bool) -> int:
    links = _sidebar_links(team)
    for link in links:
        assert 'class="nav-label"' in link, f"label not wrapped in .nav-label: {link}"
        assert "title=" in link, f"nav-link has no title for the rail tooltip: {link}"
        # Drop the wrapped label, then the remaining tags. Anything left is a
        # bare text node that display:none cannot reach.
        tail = link.split("</i>", 1)[1]
        tail = _WRAPPED.sub("", tail)
        stray = re.sub(r"<[^>]+>", "", tail).strip()
        assert not stray, f"bare label text outside .nav-label: {stray!r} in {link}"
    return len(links)


def test_admin_sidebar_labels_are_wrapped():
    """Full nav: all 15 links, both <ul> groups."""
    assert _assert_labels_wrapped(team=False) == 15


def test_team_sidebar_labels_are_wrapped():
    """Gated nav renders a different subset via {% if not team %}, so the
    wrapping has to hold on that branch too — a gated user is the one most
    likely to be on a narrow screen reaching for the collapse."""
    count = _assert_labels_wrapped(team=True)
    assert count == 3, f"expected the 3-item gated nav, got {count}"
