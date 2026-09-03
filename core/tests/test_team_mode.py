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


class TestSharedAnswerPaths:
    def test_share_and_history_paths_allowed(self):
        """Merged-in finding (strands ask-page-ux / ask-history): the share
        + history routes were not allowlisted, so a gated team member
        opening a teammate's shared link was redirected — defeating the
        share feature in gated deployments. Shared answers carry the
        normal user view only (D-209), so they are team-safe."""
        assert tm.path_allowed_for_team("/ask/s/123") is True
        assert tm.path_allowed_for_team("/api/ask/s/123") is True
        assert tm.path_allowed_for_team("/ask/history") is True
        # Unrelated /ask paths are NOT blanket-admitted.
        assert tm.path_allowed_for_team("/ask/admin") is False

    def test_req_bubble_fragments_allowed(self):
        """Strand req-id-bubbles: the bubbles inside a shared answer fetch
        their body from /api/req/. Without the allowlist entry a gated
        expert opening a teammate's link gets bubbles that redirect to
        /test — the same miss the share paths above shipped with once."""
        assert tm.path_allowed_for_team("/api/req/VZ_REQ_A_1") is True
        assert tm.path_allowed_for_team("/api/req/REQ-TMO-5G-42") is True
        # Not a blanket /api admit.
        assert tm.path_allowed_for_team("/api/config/save") is False


# ---------------------------------------------------------------------------
# /api/health roster diagnostics (strand llm-roster-deploy, #18 item 4)
# ---------------------------------------------------------------------------

class TestHealthRosterDiagnostics:
    """`/api/health` is the operator's only window onto which llm.json a
    deployment actually loaded. It is also team-allowlisted, so it must expose
    enough to debug a wrong-file problem without handing a gated user an
    absolute container path."""

    @staticmethod
    def _roster_file(tmp_path):
        import json
        p = tmp_path / "deployed-roster.json"
        p.write_text(json.dumps({
            "providers": [
                {"id": "dgx-130b", "name": "130B — DGX",
                 "base_url": "http://dgx.invalid/v1", "model": "m",
                 "api_key_env": "NORA_TEST_KEY_PRESENT"},
                {"id": "small", "name": "Small",
                 "base_url": "http://small.invalid/v1", "model": "m",
                 "api_key_env": "NORA_TEST_KEY_ABSENT"},
            ],
        }))
        return p

    def _get_health(self, monkeypatch, tmp_path, team_mode: bool):
        from starlette.testclient import TestClient

        from core.src.env import config as env_cfg
        from core.src.web import team_mode as tm
        from core.src.web.app import app

        monkeypatch.setattr(tm, "TEAM_MODE", team_mode)
        if team_mode:
            monkeypatch.setattr(tm, "ADMIN_TOKEN", "sek")
        monkeypatch.setenv(env_cfg.LLM_CONFIG_PATH_ENV_VAR,
                           str(self._roster_file(tmp_path)))
        monkeypatch.setenv("NORA_TEST_KEY_PRESENT", "shhh")
        monkeypatch.delenv("NORA_TEST_KEY_ABSENT", raising=False)
        env_cfg._reset_llm_config_cache()
        try:
            return TestClient(app).get("/api/health").json()
        finally:
            env_cfg._reset_llm_config_cache()

    def test_reports_roster_loaded_from_env_var(self, monkeypatch, tmp_path):
        body = self._get_health(monkeypatch, tmp_path, team_mode=False)
        assert body["llm_config_source"] == "env"
        assert body["llm_config_file"] == "deployed-roster.json"
        assert body["roster_size"] == 2
        assert body["effective_provider"] == "dgx-130b"

    def test_reports_api_key_presence_not_values(self, monkeypatch, tmp_path):
        """A roster entry's key comes only from the env var it names, so a file
        deployed without its keys exported 401s at the endpoint with nothing
        local to explain it. Presence makes that visible; the value never
        leaves the process."""
        body = self._get_health(monkeypatch, tmp_path, team_mode=False)
        assert body["roster_keys"] == [
            {"id": "dgx-130b", "api_key": "set"},
            {"id": "small", "api_key": "unset"},
        ]
        assert "shhh" not in str(body)

    def test_gated_user_sees_diagnostics_but_no_absolute_path(
        self, monkeypatch, tmp_path,
    ):
        """Gate ON, no admin cookie: /api/health is allowlisted so it still
        serves (verified with the gate ON per CLAUDE.md), and the body carries
        no filesystem path."""
        body = self._get_health(monkeypatch, tmp_path, team_mode=True)
        assert body["status"] == "ok"
        assert body["roster_size"] == 2
        assert "/" not in body["llm_config_file"]
        assert str(tmp_path) not in str(body)
