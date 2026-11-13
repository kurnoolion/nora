"""The answer card's prefilled "Report issue" link (opt-in, NORA_ISSUE_URL)."""
from urllib.parse import parse_qs, urlparse

from core.src.web import issue_link

BASE = "https://git.example.test/org/repo/issues/new"


def _q(url):
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def _url(**kw):
    args = dict(row_id=7, lane="sira", share_path="/ask/s/7",
                intent="compare", question="secret question text",
                cells=["CELL_A/R1"], model="m-answer")
    args.update(kw)
    return issue_link.issue_url(**args)


def test_off_without_a_configured_url(monkeypatch):
    monkeypatch.delenv("NORA_ISSUE_URL", raising=False)
    assert _url() == ""


def test_no_link_for_an_unstored_answer(monkeypatch):
    monkeypatch.setenv("NORA_ISSUE_URL", BASE)
    assert _url(row_id=None) == ""


def test_only_http_urls_are_used(monkeypatch):
    monkeypatch.setenv("NORA_ISSUE_URL", "javascript:alert(1)")
    assert _url() == ""


def test_default_prefill_carries_metadata_and_no_content(monkeypatch):
    monkeypatch.setenv("NORA_ISSUE_URL", BASE)
    monkeypatch.delenv("NORA_ISSUE_INCLUDE_CONTEXT", raising=False)
    url = _url()
    assert url.startswith(BASE + "?")
    q = _q(url)
    assert q["title"] == "Answer issue: row 7" and q["labels"] == "bug"
    body = q["body"]
    for part in ("Link: /ask/s/7\n", "Row: 7", "Lane: sira", "Intent: compare"):
        assert part in body
    for secret in ("secret question text", "CELL_A/R1", "m-answer"):
        assert secret not in body


def test_context_switch_adds_question_cells_and_model(monkeypatch):
    monkeypatch.setenv("NORA_ISSUE_URL", BASE)
    monkeypatch.setenv("NORA_ISSUE_INCLUDE_CONTEXT", "on")
    body = _q(_url())["body"]
    for part in ("secret question text", "CELL_A/R1", "m-answer"):
        assert part in body


def test_a_url_with_a_query_keeps_it(monkeypatch):
    monkeypatch.setenv("NORA_ISSUE_URL", BASE + "?template=bug.md")
    url = _url()
    assert url.startswith(BASE + "?template=bug.md&") and _q(url)["template"] == "bug.md"


def test_page_link_off_without_a_configured_url(monkeypatch):
    monkeypatch.delenv("NORA_ISSUE_URL", raising=False)
    assert issue_link.page_issue_url("/test") == ""


def test_page_link_carries_the_path(monkeypatch):
    monkeypatch.setenv("NORA_ISSUE_URL", BASE)
    q = _q(issue_link.page_issue_url("/jobs"))
    assert q["title"] == "Issue: /jobs" and q["labels"] == "bug"
    assert "Path: /jobs" in q["body"]


def test_top_bar_shows_the_link_only_when_configured(monkeypatch):
    from fastapi.testclient import TestClient
    from core.src.web.app import app

    client = TestClient(app)
    monkeypatch.delenv("NORA_ISSUE_URL", raising=False)
    assert "bi-bug" not in client.get("/test?q=secret+question+text").text
    monkeypatch.setenv("NORA_ISSUE_URL", BASE)
    html = client.get("/test?q=secret+question+text").text
    assert "bi-bug" in html and "Issue%3A+%2Ftest" in html
    assert "secret" not in html.split("bi-bug")[0].rsplit("<a", 1)[1]
