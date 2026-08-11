"""Tests for team-mode access gating (team-eval-pilot)."""

from __future__ import annotations

from types import SimpleNamespace

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from core.src.web import team_mode as tm
from core.src.web.middleware import TeamModeMiddleware


def _req(cookies=None):
    return SimpleNamespace(cookies=cookies or {})


class TestGateLogic:
    def test_gate_off_everyone_is_admin(self, monkeypatch):
        monkeypatch.setattr(tm, "TEAM_MODE", False)
        assert tm.is_admin(_req()) is True
        assert tm.team_restricted(_req()) is False

    def test_gate_on_no_cookie_is_restricted(self, monkeypatch):
        monkeypatch.setattr(tm, "TEAM_MODE", True)
        monkeypatch.setattr(tm, "ADMIN_TOKEN", "sek")
        assert tm.team_restricted(_req()) is True
        assert tm.is_admin(_req()) is False

    def test_gate_on_valid_cookie_is_admin(self, monkeypatch):
        monkeypatch.setattr(tm, "TEAM_MODE", True)
        monkeypatch.setattr(tm, "ADMIN_TOKEN", "sek")
        r = _req({tm.ADMIN_COOKIE: "sek"})
        assert tm.is_admin(r) is True
        assert tm.team_restricted(r) is False

    def test_gate_on_wrong_cookie_is_restricted(self, monkeypatch):
        monkeypatch.setattr(tm, "TEAM_MODE", True)
        monkeypatch.setattr(tm, "ADMIN_TOKEN", "sek")
        assert tm.team_restricted(_req({tm.ADMIN_COOKIE: "nope"})) is True

    def test_empty_admin_token_never_unlocks(self, monkeypatch):
        # A misconfigured deploy (gate on, no token) must NOT let an empty cookie in.
        monkeypatch.setattr(tm, "TEAM_MODE", True)
        monkeypatch.setattr(tm, "ADMIN_TOKEN", "")
        assert tm.is_admin(_req({tm.ADMIN_COOKIE: ""})) is False

    def test_path_whitelist(self):
        for ok in ("/test", "/api/test/ask-stream", "/static/x.css",
                   "/admin-unlock", "/api/health", "/favicon.ico"):
            assert tm.path_allowed_for_team(ok), ok
        for blocked in ("/", "/dashboard", "/api/config", "/parse"):
            assert not tm.path_allowed_for_team(blocked), blocked


def _mini_app():
    async def home(request):
        return PlainTextResponse("home")

    async def test(request):
        return PlainTextResponse("test")

    app = Starlette(routes=[
        Route("/", home), Route("/test", test), Route("/dashboard", home),
    ])
    app.add_middleware(TeamModeMiddleware)
    return app


class TestMiddleware:
    def test_gate_off_passes_through(self, monkeypatch):
        monkeypatch.setattr(tm, "TEAM_MODE", False)
        assert TestClient(_mini_app()).get("/dashboard").text == "home"

    def test_restricted_redirects_nonwhitelisted(self, monkeypatch):
        monkeypatch.setattr(tm, "TEAM_MODE", True)
        monkeypatch.setattr(tm, "ADMIN_TOKEN", "sek")
        r = TestClient(_mini_app()).get("/dashboard", follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/test"

    def test_restricted_allows_test(self, monkeypatch):
        monkeypatch.setattr(tm, "TEAM_MODE", True)
        monkeypatch.setattr(tm, "ADMIN_TOKEN", "sek")
        assert TestClient(_mini_app()).get("/test").text == "test"

    def test_admin_cookie_passes(self, monkeypatch):
        monkeypatch.setattr(tm, "TEAM_MODE", True)
        monkeypatch.setattr(tm, "ADMIN_TOKEN", "sek")
        c = TestClient(_mini_app())
        c.cookies.set(tm.ADMIN_COOKIE, "sek")
        assert c.get("/dashboard").text == "home"

    def test_restricted_redirect_carries_proxy_prefix(self, monkeypatch):
        """Behind a path-prefixed reverse proxy (scope root_path), the gate
        redirect must stay inside the prefix — a bare /test would bounce
        the client to the proxy root, off the app entirely."""
        monkeypatch.setattr(tm, "TEAM_MODE", True)
        monkeypatch.setattr(tm, "ADMIN_TOKEN", "sek")
        c = TestClient(_mini_app(), root_path="/nora-v2")
        r = c.get("/dashboard", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/nora-v2/test"

    def test_restricted_allows_prefixed_test(self, monkeypatch):
        """The proxy passes the prefix THROUGH (Starlette root_path semantics:
        the ASGI path includes root_path), so the gate's allowlist check must
        compare the stripped path — a raw compare sees `<prefix>/test`, fails
        the allowlist, and redirects to `<prefix>/test` in an infinite loop."""
        monkeypatch.setattr(tm, "TEAM_MODE", True)
        monkeypatch.setattr(tm, "ADMIN_TOKEN", "sek")
        c = TestClient(_mini_app(), root_path="/nora-v2")
        r = c.get("/nora-v2/test")
        assert r.status_code == 200 and r.text == "test"

    def test_restricted_prefixed_nonwhitelisted_redirects(self, monkeypatch):
        monkeypatch.setattr(tm, "TEAM_MODE", True)
        monkeypatch.setattr(tm, "ADMIN_TOKEN", "sek")
        c = TestClient(_mini_app(), root_path="/nora-v2")
        r = c.get("/nora-v2/dashboard", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/nora-v2/test"


class TestAdminUnlockProxyPrefix:
    """/admin-unlock redirects (real app) must carry the reverse-proxy
    prefix from scope root_path."""

    def _client(self):
        from core.src.web.app import app
        return TestClient(app, root_path="/nora-v2")

    def test_wrong_token_redirects_to_prefixed_test(self, monkeypatch):
        monkeypatch.setattr(tm, "TEAM_MODE", True)
        monkeypatch.setattr(tm, "ADMIN_TOKEN", "sek")
        r = self._client().get("/admin-unlock?token=bad", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/nora-v2/test"

    def test_good_token_redirects_to_prefixed_home(self, monkeypatch):
        monkeypatch.setattr(tm, "TEAM_MODE", True)
        monkeypatch.setattr(tm, "ADMIN_TOKEN", "sek")
        r = self._client().get("/admin-unlock?token=sek", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/nora-v2/"

    def test_gate_off_redirects_to_prefixed_home(self, monkeypatch):
        monkeypatch.setattr(tm, "TEAM_MODE", False)
        r = self._client().get("/admin-unlock", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/nora-v2/"


class TestStaticUnderProxyPrefix:
    """Static assets must serve on PREFIXED paths (real app). The mounted
    StaticFiles strips scope root_path + mount prefix from the request path
    (Starlette root_path semantics) — so the proxy must pass the prefix
    through, and `<prefix>/static/...` is the shape that must work. A
    prefix-stripping proxy sends `/static/...` instead, which the mount
    resolves to `static/...` INSIDE the static dir → every asset 404s."""

    def test_prefixed_static_serves(self):
        from core.src.web.app import app
        c = TestClient(app, root_path="/nora-v2")
        r = c.get("/nora-v2/static/css/style.css")
        assert r.status_code == 200
        assert "text/css" in r.headers["content-type"]
