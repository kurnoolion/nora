"""A configured-but-missing prompt fails health, and prompt presence is part
of the retrieval knobs the golden stamp keys on (field findings, trial
checkpoint 2026-10-21)."""

from __future__ import annotations

import importlib

import pytest

pytest.importorskip("fastapi")


@pytest.fixture()
def svc(monkeypatch):
    for v in ("NORA_SIRA_QUERY_ENRICH_ENABLED", "NORA_SIRA_RERANK_ENABLED",
              "NORA_SIRA_RERANK_BACKEND"):
        monkeypatch.delenv(v, raising=False)
    import core.src.store_service.service as service
    return importlib.reload(service)


def test_missing_prompts_are_reported(svc, monkeypatch):
    monkeypatch.setattr(svc, "_query_prompt_template", "")
    monkeypatch.setattr(svc, "_rerank_prompt_template", "")
    assert svc._missing_prompts() == ["query", "rerank"]


def test_loaded_prompts_are_not_missing(svc, monkeypatch):
    monkeypatch.setattr(svc, "_query_prompt_template", "q {query}")
    monkeypatch.setattr(svc, "_rerank_prompt_template", "r")
    assert svc._missing_prompts() == []


def test_dedicated_rerank_backend_needs_no_prompt(svc, monkeypatch):
    monkeypatch.setattr(svc, "_RERANK_BACKEND", "tei")
    monkeypatch.setattr(svc, "_query_prompt_template", "q")
    monkeypatch.setattr(svc, "_rerank_prompt_template", "")
    assert svc._missing_prompts() == []


def test_missing_prompt_fails_health_and_shows_in_knobs(svc, monkeypatch):
    monkeypatch.setattr(svc, "_query_prompt_template", "")
    monkeypatch.setattr(svc, "_rerank_prompt_template", "r")
    h = svc.healthz()
    assert h["ok"] is False and h["missing_prompts"] == ["query"]
    assert h["retrieval_knobs"]["query_prompt_loaded"] is False
    assert h["retrieval_knobs"]["rerank_prompt_loaded"] is True


def test_prompt_content_hash_is_in_the_knobs(svc, monkeypatch):
    monkeypatch.setattr(svc, "_query_prompt_template", "version one")
    monkeypatch.setattr(svc, "_rerank_prompt_template", "")
    a = svc.healthz()["retrieval_knobs"]
    monkeypatch.setattr(svc, "_query_prompt_template", "version two")
    b = svc.healthz()["retrieval_knobs"]
    assert a["query_prompt_sha"] and a["query_prompt_sha"] != b["query_prompt_sha"]
    assert len(a["query_prompt_sha"]) == 12
    assert a["rerank_prompt_sha"] == ""
