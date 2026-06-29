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
