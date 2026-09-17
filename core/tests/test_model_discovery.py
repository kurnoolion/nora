"""Model discovery cache for the Ask page (strand llm-model-discovery).

No network: `list_models` is replaced with a scripted double, and time is
controlled through `model_discovery._now`.
"""

from __future__ import annotations

import threading
import time

import pytest

from core.src.env.config import LLMProviderEntry
from core.src.web import model_discovery as md


def _entry(model="default-model", pid="internal"):
    return LLMProviderEntry(id=pid, name="Internal", base_url="http://llm.invalid/v1",
                            model=model)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    md._reset_cache()
    monkeypatch.setattr(md, "resolve_model_discovery_ttl_s", lambda: 100)
    clock = {"t": 1000.0}
    monkeypatch.setattr(md, "_now", lambda: clock["t"])
    yield clock
    md._reset_cache()


def _script(monkeypatch, *results):
    """Each call pops the next result; an Exception instance is raised."""
    calls = []
    queue = list(results)

    def fake(base_url, api_key="", timeout=10):
        calls.append(base_url)
        r = queue.pop(0)
        if isinstance(r, Exception):
            raise r
        return list(r)

    monkeypatch.setattr(md, "list_models", fake)
    return calls


def test_first_call_fetches_and_includes_default(monkeypatch):
    calls = _script(monkeypatch, ["model-x", "default-model"])
    assert md.models_for(_entry()) == (["model-x", "default-model"], True)
    assert len(calls) == 1


def test_default_is_prepended_when_server_does_not_list_it(monkeypatch):
    _script(monkeypatch, ["model-x"])
    assert md.models_for(_entry()) == (["default-model", "model-x"], True)


def test_fresh_cache_is_not_refetched(monkeypatch, _clean):
    calls = _script(monkeypatch, ["model-x"])
    md.models_for(_entry())
    _clean["t"] += 99
    md.models_for(_entry())
    assert len(calls) == 1


def test_stale_cache_is_refetched(monkeypatch, _clean):
    calls = _script(monkeypatch, ["model-x"], ["model-y"])
    md.models_for(_entry())
    _clean["t"] += 101
    assert md.models_for(_entry()) == (["default-model", "model-y"], True)
    assert len(calls) == 2


def test_failed_refresh_keeps_last_good_list(monkeypatch, _clean, caplog):
    _script(monkeypatch, ["model-x"], RuntimeError("down"))
    md.models_for(_entry())
    _clean["t"] += 101
    assert md.models_for(_entry()) == (["default-model", "model-x"], True)
    assert "internal" in caplog.text


def test_failure_with_no_previous_list_is_default_only(monkeypatch):
    _script(monkeypatch, RuntimeError("down"))
    assert md.models_for(_entry()) == (["default-model"], False)


def test_failed_refresh_is_not_retried_until_stale_again(monkeypatch, _clean):
    calls = _script(monkeypatch, RuntimeError("down"))
    md.models_for(_entry())
    md.models_for(_entry())
    assert len(calls) == 1


def test_allowed_models_never_fetches(monkeypatch):
    calls = _script(monkeypatch)
    assert md.allowed_models(_entry()) == ["default-model"]
    assert calls == []


def test_allowed_models_reads_the_cache(monkeypatch):
    _script(monkeypatch, ["model-x"])
    md.models_for(_entry())
    assert md.allowed_models(_entry()) == ["default-model", "model-x"]


def test_concurrent_askers_trigger_one_fetch(monkeypatch):
    calls = []

    def slow(base_url, api_key="", timeout=10):
        calls.append(1)
        time.sleep(0.05)
        return ["model-x"]

    monkeypatch.setattr(md, "list_models", slow)
    threads = [threading.Thread(target=md.models_for, args=(_entry(),)) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1


class TestModelsRoute:
    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        import json
        from fastapi.testclient import TestClient
        from core.src.env import config as cfg
        from core.src.web.app import app

        path = tmp_path / "llm.json"
        path.write_text(json.dumps({"providers": [
            {"id": "internal", "name": "Internal", "base_url": "http://llm.invalid/v1",
             "model": "default-model"},
        ]}))
        cfg._LLM_CONFIG_CACHE = cfg.LLMConfigFile.load(path)
        yield TestClient(app)
        cfg._reset_llm_config_cache()

    def test_lists_discovered_models(self, client, monkeypatch):
        _script(monkeypatch, ["model-x"])
        r = client.get("/api/test/providers/internal/models")
        assert r.status_code == 200
        assert r.json() == {"models": ["default-model", "model-x"],
                            "default": "default-model", "discovered": True}

    def test_unknown_provider_is_404(self, client):
        r = client.get("/api/test/providers/ghost/models")
        assert r.status_code == 404
        assert r.json() == {"error": "unknown provider"}

    def test_reachable_with_team_mode_on_and_no_admin_cookie(self, client, monkeypatch):
        """The route lives under the /api/test prefix, which the team gate
        allowlists (see team_mode._TEAM_ALLOWED) — a gated team member with
        no admin cookie must reach it directly, not get redirected to /test."""
        from core.src.web import team_mode as tm

        monkeypatch.setattr(tm, "TEAM_MODE", True)
        monkeypatch.setattr(tm, "ADMIN_TOKEN", "sek")
        _script(monkeypatch, ["model-x"])
        r = client.get("/api/test/providers/internal/models", follow_redirects=False)
        assert r.status_code == 200
        assert r.json() == {"models": ["default-model", "model-x"],
                            "default": "default-model", "discovered": True}


class TestAskPageModelSelect:
    """The `/test` page's `ask-model` select (strand llm-model-discovery,
    Task 6) — rendered only when a roster is configured, server-rendering
    the first provider's default so a pre-fetch submit still posts a model
    the server accepts."""

    @pytest.fixture()
    def client_with_roster(self, tmp_path, monkeypatch):
        import json
        from fastapi.testclient import TestClient
        from core.src.env import config as cfg
        from core.src.web.app import app

        path = tmp_path / "llm.json"
        path.write_text(json.dumps({"providers": [
            {"id": "vega", "name": "Vega", "base_url": "http://vega.invalid/v1",
             "model": "vega-alpha-9"},
            {"id": "nyx", "name": "Nyx", "base_url": "http://nyx.invalid/v1",
             "model": "nyx-beta-2"},
        ]}))
        cfg._LLM_CONFIG_CACHE = cfg.LLMConfigFile.load(path)
        yield TestClient(app)
        cfg._reset_llm_config_cache()

    @pytest.fixture()
    def client_no_roster(self, tmp_path, monkeypatch):
        import json
        from fastapi.testclient import TestClient
        from core.src.env import config as cfg
        from core.src.web.app import app

        path = tmp_path / "llm.json"
        path.write_text(json.dumps({}))
        cfg._LLM_CONFIG_CACHE = cfg.LLMConfigFile.load(path)
        yield TestClient(app)
        cfg._reset_llm_config_cache()

    def test_roster_configured_renders_model_select(self, client_with_roster):
        r = client_with_roster.get("/test")
        assert r.status_code == 200
        body = r.text
        assert 'id="ask-model"' in body
        assert 'name="model"' in body
        assert '<option value="vega-alpha-9" selected>vega-alpha-9</option>' in body
        assert 'data-url-template="/api/test/providers/__ID__/models"' in body

    def test_no_roster_renders_no_model_select(self, client_no_roster):
        r = client_no_roster.get("/test")
        assert r.status_code == 200
        assert 'id="ask-model"' not in r.text
