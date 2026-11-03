"""Browser check of `static/js/release_change.js` over a rendered answer.

The release-change answer is one `.rel-change` block the script re-shapes
into views (Table / Tree / Tree, expanded) with an Export menu. Python
tests cover the markdown; this one drives the script in a headless
Chromium over the rendered fixture: the toolbar, the tab panel under a
table row, the view switch, and the three downloads with a kind left out.
Skipped where Playwright or its Chromium is not installed.
"""
from __future__ import annotations

import pathlib

import pytest

pytest.importorskip("playwright.sync_api")

from core.src.web.markdown_render import render_markdown_bubbles   # noqa: E402
from core.src.web.routes import history_lane                       # noqa: E402

STATIC = pathlib.Path(__file__).resolve().parents[1] / "src" / "web" / "static"

RESULT = {
    "cancelled": False, "absent_plans": [{"plan_id": "PX", "absent_in": "Jul2031"}],
    "mno": "mno-q", "from": "Apr2031", "to": "Jul2031",
    "counts": {"requirements_from": 120, "requirements_to": 124, "added": 0, "removed": 1,
               "renumbered": 2, "moved": 0, "absent_plans": 1},
    "plans": [{"plan_id": "P1", "plan_name": "One",
               "counts": {"changed": 1, "added": 0, "removed": 1, "renumbered": 2, "moved": 0,
                          "dropped": 3, "same": 0, "bookkeeping": 3},
               "sections": [{"kind": "pair", "status": "ok", "text": "For [R1], x.", "ids": ["R1"], "lead": ""},
                            {"kind": "removed", "status": "ok", "text": "The plan loses a timer.",
                             "ids": ["R6"], "lead": "Removed: [R6]"}],
               "notes": [], "note_groups": {
                   "structural": ["2 requirement(s) renumbered (2 sibling); content unchanged."],
                   "bookkeeping": ["3 requirement(s) differ only in the revision record: [R2], [R3], [R4]."]}},
              {"plan_id": "P2", "plan_name": "Two",
               "counts": {"changed": 2, "added": 0, "removed": 0, "renumbered": 0, "moved": 0,
                          "dropped": 0, "same": 0, "bookkeeping": 0},
               "sections": [{"kind": "pair", "status": "ok", "text": "For [R7], y. For [R8], z.",
                             "ids": ["R7", "R8"], "lead": ""}],
               "notes": [], "note_groups": {"structural": [], "bookkeeping": []}}]}


def _page_html() -> str:
    md = history_lane.answer_markdown(RESULT)
    body = str(render_markdown_bubbles(md, ["R1", "R6", "R2", "R7", "R8"], "", {}))
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<link rel="stylesheet" href="file://{STATIC}/vendor/bootstrap/bootstrap.min.css">'
            f'<link rel="stylesheet" href="file://{STATIC}/css/style.css"></head>'
            f'<body><div class="answer-body">{body}</div>'
            f'<script src="file://{STATIC}/vendor/bootstrap/bootstrap.bundle.min.js"></script>'
            f'<script src="file://{STATIC}/js/app.js"></script>'
            f'<script src="file://{STATIC}/js/release_change.js"></script></body></html>')


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except Exception as exc:                      # noqa: BLE001 — no browser here
            pytest.skip(f"chromium not available: {exc}")
        yield b
        b.close()


@pytest.fixture
def page(browser, tmp_path):
    path = tmp_path / "answer.html"
    path.write_text(_page_html(), encoding="utf-8")
    ctx = browser.new_context(accept_downloads=True)
    pg = ctx.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.goto(path.as_uri())
    pg.wait_for_selector(".rc-toolbar")
    pg.errors = errors
    yield pg
    ctx.close()


def _download(page, fmt: str, untick: str = "") -> str:
    page.click(".rc-export")
    page.click(f'.rc-menu-item[data-format="{fmt}"]')
    page.wait_for_selector(".rc-sheet")
    if untick:
        page.uncheck(f'.rc-picks input[data-kind="{untick}"]')
    with page.expect_download() as dl:
        page.click(".rc-sheet-actions .btn-primary")
    page.wait_for_selector(".rc-sheet", state="detached")
    return pathlib.Path(dl.value.path()).read_text(encoding="utf-8")


def test_table_view_panel_and_view_switch(page):
    assert page.locator('.rc-view[aria-pressed="true"]').inner_text() == "Table"
    assert page.locator("table.rc-counts").is_visible() and not page.locator(".rc-tree").is_visible()
    # the plan's sections moved into its (hidden) panel; the tree holds none
    assert page.locator('tr.rc-panel[data-plan="P1"] .rc-sec').count() == 4
    assert page.locator('.rc-tree details.rc-plan[data-plan="P1"] .rc-sec').count() == 0
    # a count opens the panel on its kind; the same count closes it
    page.click('tr.rc-row[data-plan="P1"] .rc-count[data-tab="removed"]')
    panel = page.locator('tr.rc-panel[data-plan="P1"]')
    assert panel.is_visible() and panel.locator('.rc-tab[aria-selected="true"]').inner_text().startswith("Removed")
    assert "The plan loses a timer." in panel.locator(".rc-pane:not([hidden])").inner_text()
    assert "open" in page.locator('tr.rc-row[data-plan="P1"]').get_attribute("class")
    page.click('tr.rc-row[data-plan="P1"] .rc-count[data-tab="removed"]')
    assert not panel.is_visible()
    # the plan name opens the first tab; a tab click switches; Collapse closes
    page.click('tr.rc-row[data-plan="P1"] .rc-plan-toggle')
    assert panel.locator('.rc-tab[aria-selected="true"]').inner_text().startswith("Changed")
    page.click('tr.rc-panel[data-plan="P1"] .rc-tab[data-tab="bookkeeping"]')
    assert "Bookkeeping only" in panel.locator(".rc-pane:not([hidden])").inner_text()
    assert not panel.locator(".rc-pane:not([hidden]) details.rc-ids").evaluate("d => d.open")
    # Tree: sections back in the plan's block, the open plan stays open
    page.click('.rc-view[data-view="tree"]')
    assert not page.locator("table.rc-counts").is_visible() and page.locator(".rc-tree").is_visible()
    assert page.locator('.rc-tree details.rc-plan[data-plan="P1"] > .rc-sec').count() == 4
    assert page.locator('details.rc-plan[data-plan="P1"]').evaluate("d => d.open")
    assert not page.locator('details.rc-plan[data-plan="P2"]').evaluate("d => d.open")
    # expanded: every plan and id list open
    page.click('.rc-view[data-view="expanded"]')
    assert page.locator('details.rc-plan[data-plan="P2"]').evaluate("d => d.open")
    assert page.locator("details.rc-ids").evaluate("d => d.open")
    assert page.locator('.rc-tree details.rc-plan[data-plan="P1"]').inner_text().count("R2") == 1
    assert page.errors == []


def test_exports_follow_the_view_and_the_include_choice(page):
    # Raw: everything whatever the view, minus the kind left out
    raw = _download(page, "raw", untick="bookkeeping")
    assert raw.startswith("# Release changes: mno-q, Apr2031 → Jul2031\n")
    assert "## Summary" in raw and "| Plan | Changed |" in raw and "| P1 — One | 1 | 0 | 1 | 2 | 0 | 3 |" in raw
    assert "## P1 — One" in raw and "### Changed: 1 requirement(s)" in raw and "For [R1], x." in raw
    assert "### Removed: [R6]" in raw and "### Structural changes only" in raw and "- 2 requirement(s) renumbered" in raw
    assert "### Bookkeeping only" not in raw and "[R2]" not in raw      # the column header stays
    assert "## P2 — Two" in raw and "## Plans present in one release only" in raw and "- PX: not part of Jul2031" in raw
    # Text in Table view: the table, and only the open panel's tab
    page.click('tr.rc-row[data-plan="P1"] .rc-count[data-tab="bookkeeping"]')
    text = _download(page, "text")
    assert "mno-q: Apr2031 → Jul2031" in text and "Plan" in text and "P1 — One" in text
    assert "Bookkeeping only" in text and "(ids not shown)" in text and "For [R1], x." not in text
    # Text in the expanded tree: everything, ids shown
    page.click('.rc-view[data-view="expanded"]')
    text = _download(page, "text")
    assert "▾ P1 — One · 1 changed, 1 removed" in text and "For [R1], x." in text and "[R2], [R3], [R4]" in text
    # HTML: one page, bubbles as plain ids, no htmx wiring, no scripts
    html = _download(page, "html", untick="structural")
    assert html.startswith("<!doctype html>") and "<script" not in html and "hx-get" not in html
    assert '<code class="rc-id">R1</code>' in html and "Structural changes only" not in html
    assert "<strong>Bookkeeping only</strong>" in html and "<title>Release changes: mno-q, Apr2031 → Jul2031</title>" in html
    assert page.errors == []
