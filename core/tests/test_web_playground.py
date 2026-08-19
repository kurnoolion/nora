"""Tests for the Test-page corpus-label helper.

The page blurb is dynamic — it pulls MNO + release info from the
active ``EnvironmentConfig`` so users see what's actually ingested
on-prem instead of a hardcoded corpus name.
"""

from __future__ import annotations

from unittest.mock import patch

from core.src.web.routes import playground


class _StubEnv:
    """Minimal stand-in for ``EnvironmentConfig`` — only the two fields
    ``_corpus_label`` reads."""

    def __init__(self, mnos: list[str], releases: list[str]) -> None:
        self.mnos = mnos
        self.releases = releases


def test_corpus_label_single_mno_single_release():
    with patch("core.src.web.routes.query._find_env_config_for_web") as m:
        m.return_value = _StubEnv(["VZW"], ["Feb2026"])
        assert playground._corpus_label() == "VZW Feb2026"


def test_corpus_label_other_mno_release():
    """Regression guard: label adapts to whatever's in the env config —
    not hardcoded VZW / Feb2026."""
    with patch("core.src.web.routes.query._find_env_config_for_web") as m:
        m.return_value = _StubEnv(["TMO"], ["Q3-2026"])
        assert playground._corpus_label() == "TMO Q3-2026"


def test_corpus_label_multi_mno():
    with patch("core.src.web.routes.query._find_env_config_for_web") as m:
        m.return_value = _StubEnv(["VZW", "TMO"], ["Feb2026"])
        assert playground._corpus_label() == "2 MNOs × 1 releases"


def test_corpus_label_multi_release():
    with patch("core.src.web.routes.query._find_env_config_for_web") as m:
        m.return_value = _StubEnv(["VZW"], ["Feb2026", "Jun2026"])
        assert playground._corpus_label() == "1 MNOs × 2 releases"


def test_corpus_label_falls_back_when_no_env_config():
    with patch("core.src.web.routes.query._find_env_config_for_web") as m:
        m.return_value = None
        assert playground._corpus_label() == "the indexed"


def test_corpus_label_falls_back_when_env_lookup_raises():
    """Defensive: if env-config lookup throws (e.g. JSON parse error),
    return the safe fallback rather than crashing the Test page."""
    with patch(
        "core.src.web.routes.query._find_env_config_for_web",
        side_effect=RuntimeError("boom"),
    ):
        assert playground._corpus_label() == "the indexed"


def test_corpus_label_falls_back_when_empty_lists():
    with patch("core.src.web.routes.query._find_env_config_for_web") as m:
        m.return_value = _StubEnv([], [])
        assert playground._corpus_label() == "the indexed"


def test_build_sections_blurb_includes_label():
    """Smoke: ``_build_sections`` substitutes the label into the
    requirement_bot blurb."""
    with patch("core.src.web.routes.query._find_env_config_for_web") as m:
        m.return_value = _StubEnv(["VZW"], ["Feb2026"])
        sections = playground._build_sections()
    bot = next(s for s in sections if s["id"] == "requirement_bot")
    assert "VZW Feb2026 requirements" in bot["blurb"]
    # Hardcoded "VZW OA" must not slip back in.
    assert "OA" not in bot["blurb"]


def test_test_page_sets_no_cache_header(monkeypatch):
    """The /test page must send Cache-Control: no-cache so the team gets the
    latest inline progress JS on a normal refresh (no manual hard-refresh)."""
    import asyncio
    from types import SimpleNamespace
    from starlette.responses import HTMLResponse
    from core.src.web.routes import playground as pg

    monkeypatch.setattr(
        "core.src.web.app._template_response",
        lambda request, name, ctx: HTMLResponse("<html></html>"),
    )
    req = SimpleNamespace(cookies={})            # team_restricted() reads .cookies
    resp = asyncio.run(pg.playground_page(req))
    assert resp.headers["Cache-Control"] == "no-cache"


# -- Shared-answer snapshot (/ask/s/{row_id}) --------------------------------


def _shared(row, row_id=7):
    """Call the share route with a stubbed feedback store, capturing the
    template context instead of rendering."""
    import asyncio
    from types import SimpleNamespace
    from starlette.responses import HTMLResponse
    from core.src.web.routes import playground as pg

    captured = {}

    class _Store:
        async def get_row(self, rid):
            return row if rid == row_id else None

    def _fake_template(request, name, ctx):
        captured["name"] = name
        captured["ctx"] = ctx
        return HTMLResponse("<html></html>")

    req = SimpleNamespace(
        cookies={},
        app=SimpleNamespace(state=SimpleNamespace(feedback_store=_Store())),
    )
    with patch("core.src.web.app._template_response", _fake_template):
        resp = asyncio.run(pg.shared_answer(req, row_id))
    return resp, captured


def test_shared_answer_renders_stored_row():
    row = {
        "id": 7, "question": "retry rules?", "answer": "Because backoff.",
        "lane": "nora", "user_name": "hanif", "timestamp": "2026-08-18T11:44:01+00:00",
        "llm_model": "deepseek-r1:1.5b", "query_elapsed_ms": 1200,
        "citations_json": '[{"req_id": "REQ_A_1", "plan_id": "PLAN_X"}]',
        "cited_ids": '["REQ_A_1"]',
    }
    resp, cap = _shared(row)
    assert resp.status_code == 200
    assert cap["name"] == "test/shared.html"
    assert cap["ctx"]["row"]["question"] == "retry rules?"
    # citations_json / cited_ids are decoded for the template
    assert cap["ctx"]["citations"] == [{"req_id": "REQ_A_1", "plan_id": "PLAN_X"}]
    assert cap["ctx"]["cited_ids"] == ["REQ_A_1"]


def test_shared_answer_unknown_id_is_404():
    resp, cap = _shared({"id": 7}, row_id=7)
    # same store, but ask for an id it doesn't have
    import asyncio
    from types import SimpleNamespace
    from core.src.web.routes import playground as pg

    class _Empty:
        async def get_row(self, rid):
            return None

    req = SimpleNamespace(
        cookies={},
        app=SimpleNamespace(state=SimpleNamespace(feedback_store=_Empty())),
    )
    r = asyncio.run(pg.shared_answer(req, 999999))
    assert r.status_code == 404


def test_shared_answer_survives_malformed_json():
    """A row with corrupt citations JSON must still render the answer, not 500."""
    row = {
        "id": 7, "question": "q", "answer": "a", "lane": "sira",
        "citations_json": "{not json", "cited_ids": None,
    }
    resp, cap = _shared(row)
    assert resp.status_code == 200
    assert cap["ctx"]["citations"] == [] and cap["ctx"]["cited_ids"] == []


# -- History page + snapshot fragment ---------------------------------------


def test_shared_fragment_is_body_only():
    """The History pane fetches the same snapshot markup as the shared page,
    but without page chrome — one template, two surfaces, no drift."""
    row = {
        "id": 7, "question": "q", "answer": "a", "lane": "nora",
        "citations_json": "[]", "cited_ids": "[]",
    }
    resp, cap = _shared(row)          # full page
    assert cap["name"] == "test/shared.html"

    import asyncio
    from types import SimpleNamespace
    from starlette.responses import HTMLResponse
    from core.src.web.routes import playground as pg

    captured = {}

    class _Store:
        async def get_row(self, rid):
            return row if rid == 7 else None

    def _fake_template(request, name, ctx):
        captured["name"] = name
        return HTMLResponse("<div></div>")

    req = SimpleNamespace(
        cookies={},
        app=SimpleNamespace(state=SimpleNamespace(feedback_store=_Store())),
    )
    with patch("core.src.web.app._template_response", _fake_template):
        r = asyncio.run(pg.shared_answer_fragment(req, 7))
    assert r.status_code == 200
    assert captured["name"] == "test/_shared_body.html"


def test_shared_fragment_unknown_id_is_404():
    """A stale history entry must 404 so the pane can say so, not blank out."""
    import asyncio
    from types import SimpleNamespace
    from core.src.web.routes import playground as pg

    class _Empty:
        async def get_row(self, rid):
            return None

    req = SimpleNamespace(
        cookies={},
        app=SimpleNamespace(state=SimpleNamespace(feedback_store=_Empty())),
    )
    r = asyncio.run(pg.shared_answer_fragment(req, 424242))
    assert r.status_code == 404


def test_history_page_renders():
    import asyncio
    from types import SimpleNamespace
    from starlette.responses import HTMLResponse
    from core.src.web.routes import playground as pg

    captured = {}

    def _fake_template(request, name, ctx):
        captured["name"] = name
        return HTMLResponse("<html></html>")

    req = SimpleNamespace(cookies={})
    with patch("core.src.web.app._template_response", _fake_template):
        r = asyncio.run(pg.ask_history_page(req))
    assert r.status_code == 200
    assert captured["name"] == "test/history.html"
