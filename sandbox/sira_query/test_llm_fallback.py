"""Permanent-refusal fallback in the service's _llm_call choke point.

Markers are invented test strings — real values are deployment-local
env config (.env.sira-query) and never appear in the repo.
"""

from __future__ import annotations

import asyncio

import sandbox.sira_query.service as svc


class _Resp:
    status_code = 200
    text = ""

    def __init__(self, content: str):
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


class _StubClient:
    """httpx.AsyncClient stand-in: canned responses, records posts."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[tuple[str, dict, dict | None]] = []

    async def post(self, url, json=None, headers=None):
        self.calls.append((url, json, headers))
        return _Resp(self._responses.pop(0))


def _configure(monkeypatch, *, url="http://fallback.test", model="fb-model",
               markers=("CANNOT_COMPLY",)):
    monkeypatch.setattr(svc, "_FALLBACK_LLM_URL", url)
    monkeypatch.setattr(svc, "_FALLBACK_LLM_MODEL", model)
    monkeypatch.setattr(svc, "_FALLBACK_LLM_API_KEY", "")
    monkeypatch.setattr(svc, "_REFUSAL_MARKERS", markers)
    monkeypatch.setattr(svc, "_FALLBACK_STATS", {"used": 0})


def test_refusal_reroutes_to_fallback(monkeypatch):
    _configure(monkeypatch)
    client = _StubClient(["CANNOT_COMPLY with this input.",
                          '{"keywords": ["alpha"]}'])
    raw = asyncio.run(svc._llm_call(client, "prompt text"))
    assert raw == '{"keywords": ["alpha"]}'
    assert len(client.calls) == 2
    fb_url, fb_payload, _ = client.calls[1]
    assert fb_url == "http://fallback.test/v1/chat/completions"
    assert fb_payload["model"] == "fb-model"
    # same prompt reaches the fallback
    assert fb_payload["messages"] == client.calls[0][1]["messages"]
    assert svc._FALLBACK_STATS["used"] == 1


def test_normal_answer_never_touches_fallback(monkeypatch):
    _configure(monkeypatch)
    client = _StubClient(['{"keywords": ["alpha"]}'])
    raw = asyncio.run(svc._llm_call(client, "prompt text"))
    assert raw == '{"keywords": ["alpha"]}'
    assert len(client.calls) == 1
    assert svc._FALLBACK_STATS["used"] == 0


def test_unconfigured_fallback_returns_refusal_verbatim(monkeypatch):
    _configure(monkeypatch, url="", model="")
    client = _StubClient(["CANNOT_COMPLY with this input."])
    raw = asyncio.run(svc._llm_call(client, "prompt text"))
    assert raw.startswith("CANNOT_COMPLY")
    assert len(client.calls) == 1


def test_no_markers_disables_detection(monkeypatch):
    _configure(monkeypatch, markers=())
    client = _StubClient(["CANNOT_COMPLY with this input."])
    raw = asyncio.run(svc._llm_call(client, "prompt text"))
    assert raw.startswith("CANNOT_COMPLY")
    assert len(client.calls) == 1
