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
