"""Tests for OpenAICompatibleProvider — fully mocked, no network."""

from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest

from core.src.llm.openai_provider import (
    ENV_API_KEY,
    ENV_BASE_URL,
    ENV_MODEL,
    OpenAICompatibleProvider,
)


# ---------------------------------------------------------------------------
# urlopen mock helpers
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._buf = io.BytesIO(body)
        self.status = status

    def read(self, size: int = -1) -> bytes:
        return self._buf.read(size) if size != -1 else self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _ok_response(content: str, prompt_tokens: int = 10, completion_tokens: int = 5) -> _FakeResponse:
    return _FakeResponse(
        json.dumps({
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }).encode("utf-8")
    )


# ---------------------------------------------------------------------------
# Construction & config validation
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_constructor_args_happy(self, monkeypatch):
        # Wipe env to confirm constructor args are sufficient on their own.
        for v in (ENV_BASE_URL, ENV_API_KEY, ENV_MODEL):
            monkeypatch.delenv(v, raising=False)
        p = OpenAICompatibleProvider(
            model="qwen/qwen3-235b-a22b",
            base_url="https://example.test/v1",
            api_key="sk-test",
        )
        assert p.model == "qwen/qwen3-235b-a22b"
        assert p.call_count == 0
        assert p.last_call_stats == {}

    def test_env_vars_used_when_args_missing(self, monkeypatch):
        monkeypatch.setenv(ENV_BASE_URL, "https://example.test/v1")
        monkeypatch.setenv(ENV_API_KEY, "sk-from-env")
        monkeypatch.setenv(ENV_MODEL, "openai/gpt-oss-120b")
        p = OpenAICompatibleProvider()
        assert p.model == "openai/gpt-oss-120b"

    def test_constructor_args_win_over_env(self, monkeypatch):
        monkeypatch.setenv(ENV_BASE_URL, "https://env.test/v1")
        monkeypatch.setenv(ENV_API_KEY, "sk-env")
        monkeypatch.setenv(ENV_MODEL, "env-model")
        p = OpenAICompatibleProvider(
            model="explicit-model",
            base_url="https://explicit.test/v1",
            api_key="sk-explicit",
        )
        assert p.model == "explicit-model"

    def test_missing_model_raises(self, monkeypatch):
        for v in (ENV_BASE_URL, ENV_API_KEY, ENV_MODEL):
            monkeypatch.delenv(v, raising=False)
        with pytest.raises(ValueError, match="model"):
            OpenAICompatibleProvider(
                base_url="https://example.test/v1", api_key="sk"
            )

    def test_missing_base_url_raises(self, monkeypatch):
        for v in (ENV_BASE_URL, ENV_API_KEY, ENV_MODEL):
            monkeypatch.delenv(v, raising=False)
        with pytest.raises(ValueError, match="base_url"):
            OpenAICompatibleProvider(model="m", api_key="sk")

    def test_missing_api_key_raises(self, monkeypatch):
        for v in (ENV_BASE_URL, ENV_API_KEY, ENV_MODEL):
            monkeypatch.delenv(v, raising=False)
        with pytest.raises(ValueError, match="api_key"):
            OpenAICompatibleProvider(
                model="m", base_url="https://example.test/v1"
            )


# ---------------------------------------------------------------------------
# complete() — payload, response, headers, errors
# ---------------------------------------------------------------------------


def _make_provider() -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        model="qwen/qwen3-235b-a22b",
        base_url="https://example.test/v1",
        api_key="sk-test",
        timeout=10,
    )


def _capture_request(captured: dict):
    """Return a side_effect that records the urllib.Request and yields a response."""
    def _side(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.headers)
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _ok_response("hello")
    return _side


class TestComplete:
    def test_payload_shape_and_endpoint(self):
        captured: dict = {}
        with patch("urllib.request.urlopen", side_effect=_capture_request(captured)):
            p = _make_provider()
            out = p.complete("ping", system="be terse", temperature=0.0, max_tokens=64)
        assert out == "hello"
        assert captured["url"] == "https://example.test/v1/chat/completions"
        body = captured["body"]
        assert body["model"] == "qwen/qwen3-235b-a22b"
        assert body["temperature"] == 0.0
        assert body["max_tokens"] == 64
        assert body["messages"] == [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "ping"},
        ]

    def test_no_system_message_when_empty(self):
        captured: dict = {}
        with patch("urllib.request.urlopen", side_effect=_capture_request(captured)):
            p = _make_provider()
            p.complete("ping")
        assert captured["body"]["messages"] == [{"role": "user", "content": "ping"}]

    def test_authorization_header(self):
        captured: dict = {}
        with patch("urllib.request.urlopen", side_effect=_capture_request(captured)):
            p = _make_provider()
            p.complete("ping")
        # urllib lowercases header keys when stored on Request.
        auth = captured["headers"].get("Authorization") or captured["headers"].get("authorization")
        assert auth == "Bearer sk-test"

    def test_extra_headers_merged(self):
        captured: dict = {}
        with patch("urllib.request.urlopen", side_effect=_capture_request(captured)):
            p = OpenAICompatibleProvider(
                model="m", base_url="https://example.test/v1", api_key="sk",
                extra_headers={"X-Title": "NORA", "HTTP-Referer": "https://nora.test"},
            )
            p.complete("ping")
        # urllib normalizes — check case-insensitively.
        norm = {k.lower(): v for k, v in captured["headers"].items()}
        assert norm["x-title"] == "NORA"
        assert norm["http-referer"] == "https://nora.test"

    def test_last_call_stats_populated(self):
        with patch("urllib.request.urlopen", return_value=_ok_response("hi", 100, 25)):
            p = _make_provider()
            p.complete("ping")
        stats = p.last_call_stats
        assert stats["model"] == "qwen/qwen3-235b-a22b"
        assert stats["eval_count"] == 25
        assert stats["prompt_eval_count"] == 100
        assert stats["tokens_per_second"] >= 0
        assert stats["total_duration_s"] >= 0

    def test_call_count_increments(self):
        # Each _ok_response wraps a single-use BytesIO, so build a fresh one
        # per call via side_effect rather than reusing return_value.
        with patch(
            "urllib.request.urlopen",
            side_effect=lambda *a, **kw: _ok_response("hi"),
        ):
            p = _make_provider()
            assert p.call_count == 0
            p.complete("a")
            p.complete("b")
            assert p.call_count == 2

    def test_http_error_surfaces_body(self):
        import urllib.error
        err = urllib.error.HTTPError(
            url="https://example.test/v1/chat/completions",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"error": {"message": "Invalid API key"}}'),
        )
        with patch("urllib.request.urlopen", side_effect=err):
            p = _make_provider()
            with pytest.raises(RuntimeError, match="401.*Invalid API key"):
                p.complete("ping")

    def test_network_error_raises_runtime(self):
        import urllib.error
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            p = _make_provider()
            with pytest.raises(RuntimeError, match="network error"):
                p.complete("ping")

    def test_empty_choices_raises(self):
        bad = _FakeResponse(json.dumps({"choices": []}).encode("utf-8"))
        with patch("urllib.request.urlopen", return_value=bad):
            p = _make_provider()
            with pytest.raises(RuntimeError, match="no choices"):
                p.complete("ping")

    def test_missing_usage_does_not_crash(self):
        # Some providers omit `usage` entirely (or partial fields). Stats should
        # default cleanly to zeros rather than KeyError.
        body = json.dumps({
            "choices": [{"message": {"content": "ok"}}],
        }).encode("utf-8")
        with patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
            p = _make_provider()
            out = p.complete("ping")
        assert out == "ok"
        assert p.last_call_stats["eval_count"] == 0
        assert p.last_call_stats["prompt_eval_count"] == 0
