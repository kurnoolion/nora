"""Tests for the golden eval sample schema + persistence (FR-38).

Fixtures are synthetic — generic placeholder ids only, no proprietary
content (NFR-8).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from core.src.eval.golden import (
    STATUS_DRAFT,
    STATUS_GOLDEN_READY,
    STATUS_STAGE1_READY,
    GoldenEvalError,
    GoldenSample,
    GroundTruthEntry,
    load_sample,
    load_samples,
    next_sample_id,
    sample_path,
    save_sample,
    validate_sample,
)

_NOW = datetime(2026, 8, 5, 12, 0, 0)


def _sample(sample_id="gs-0001", status=STATUS_DRAFT, **kw) -> GoldenSample:
    defaults = dict(
        sample_id=sample_id,
        query="What are the widget retry requirements?",
        area="retry",
        created_by="expert-a",
        ground_truth=[
            GroundTruthEntry(
                req_id="REQ_FOO_0001", mno="mno-a", release="Jan2026",
                plan="PLAN_X", source="picker",
            ),
            GroundTruthEntry(req_id="REQ_FOO_0002"),
        ],
        status=status,
    )
    defaults.update(kw)
    return GoldenSample(**defaults)


# ─── Validation ─────────────────────────────────────────────────────


def test_valid_draft_sample_passes():
    assert validate_sample(_sample()) == []


def test_mno_tag_round_trips():
    s = _sample(mno="mno-a")
    assert s.to_dict()["mno"] == "mno-a"
    assert GoldenSample.from_dict(s.to_dict()).mno == "mno-a"
    # Back-compat: a sample dict without the key loads as "".
    d = s.to_dict()
    del d["mno"]
    assert GoldenSample.from_dict(d).mno == ""


def test_bad_sample_id_flagged():
    problems = validate_sample(_sample(sample_id="sample-1"))
    assert any("sample_id" in p for p in problems)


def test_empty_query_flagged():
    problems = validate_sample(_sample(query="   "))
    assert any("query is empty" in p for p in problems)


def test_unknown_status_flagged():
    problems = validate_sample(_sample(status="done"))
    assert any("status" in p for p in problems)


def test_empty_req_id_flagged():
    s = _sample(ground_truth=[GroundTruthEntry(req_id="  ")])
    assert any("empty req_id" in p for p in validate_sample(s))


def test_duplicate_entry_flagged():
    e = GroundTruthEntry(req_id="REQ_FOO_0001", mno="mno-a", release="Jan2026")
    s = _sample(ground_truth=[e, GroundTruthEntry.from_dict(e.to_dict())])
    assert any("duplicate" in p for p in validate_sample(s))


def test_same_req_id_different_cell_is_not_duplicate():
    s = _sample(ground_truth=[
        GroundTruthEntry(req_id="REQ_FOO_0001", mno="mno-a", release="Jan2026"),
        GroundTruthEntry(req_id="REQ_FOO_0001", mno="mno-a", release="Apr2026"),
    ])
    assert validate_sample(s) == []


def test_stage1_ready_requires_ground_truth():
    s = _sample(status=STATUS_STAGE1_READY, ground_truth=[])
    assert any("requires ground_truth" in p for p in validate_sample(s))


def test_golden_ready_requires_golden_response():
    s = _sample(status=STATUS_GOLDEN_READY)
    assert any("golden_response" in p for p in validate_sample(s))
    s.golden_response = "The widgets shall retry with backoff."
    assert validate_sample(s) == []


# ─── Persistence ────────────────────────────────────────────────────


def test_save_load_roundtrip(tmp_path: Path):
    s = _sample()
    path = save_sample(tmp_path, s, now=_NOW)
    assert path == sample_path(tmp_path, "gs-0001")
    loaded = load_sample(path)
    assert loaded.to_dict() == s.to_dict()
    assert loaded.created_at == "2026-08-05T12:00:00"
    assert loaded.ground_truth[0].plan == "PLAN_X"
    assert loaded.ground_truth[0].source == "picker"


def test_save_stamps_updated_but_preserves_created(tmp_path: Path):
    s = _sample()
    save_sample(tmp_path, s, now=_NOW)
    later = datetime(2026, 8, 6, 9, 30, 0)
    save_sample(tmp_path, s, now=later)
    loaded = load_sample(sample_path(tmp_path, "gs-0001"))
    assert loaded.created_at == "2026-08-05T12:00:00"
    assert loaded.updated_at == "2026-08-06T09:30:00"


def test_save_rejects_invalid(tmp_path: Path):
    with pytest.raises(GoldenEvalError) as exc:
        save_sample(tmp_path, _sample(query=""), now=_NOW)
    assert exc.value.code == "GEV-E001"
    assert not sample_path(tmp_path, "gs-0001").exists()


def test_load_samples_sorted(tmp_path: Path):
    save_sample(tmp_path, _sample(sample_id="gs-0002"), now=_NOW)
    save_sample(tmp_path, _sample(sample_id="gs-0001"), now=_NOW)
    ids = [s.sample_id for s in load_samples(tmp_path)]
    assert ids == ["gs-0001", "gs-0002"]


def test_load_samples_empty_when_missing_dir(tmp_path: Path):
    assert load_samples(tmp_path) == []


def test_load_samples_fails_loud_on_malformed(tmp_path: Path):
    save_sample(tmp_path, _sample(), now=_NOW)
    bad = sample_path(tmp_path, "gs-0002")
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(GoldenEvalError) as exc:
        load_samples(tmp_path)
    assert exc.value.code == "GEV-E001"
    assert "gs-0002" in str(exc.value)


def test_next_sample_id(tmp_path: Path):
    assert next_sample_id(tmp_path) == "gs-0001"
    save_sample(tmp_path, _sample(sample_id="gs-0001"), now=_NOW)
    save_sample(tmp_path, _sample(sample_id="gs-0007"), now=_NOW)
    assert next_sample_id(tmp_path) == "gs-0008"


def test_build_llm_honors_openai_compatible_provider(monkeypatch):
    """Field-found dead branch: _build_llm compared provider against
    "openai" — a value resolve_llm_provider can never return (canonical
    set: ollama / openai-compatible / mock) — so every eval LLM dialed
    Ollama regardless of configuration. The dispatch must match the
    CANONICAL value, mirroring pipeline/runner.py."""
    from core.src.eval.golden_cli import _build_llm
    from core.src.llm.openai_provider import OpenAICompatibleProvider

    monkeypatch.setenv("NORA_LLM_PROVIDER", "openai-compatible")
    monkeypatch.setenv("NORA_LLM_MODEL", "test-model")
    monkeypatch.setenv("NORA_LLM_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.delenv("NORA_SIRA_ENRICH_FALLBACK_LLM_URL", raising=False)
    llm = _build_llm()
    # The refusal-fallback wrap may or may not apply depending on env;
    # unwrap defensively before the type check.
    inner = getattr(llm, "_primary", None) or getattr(llm, "_llm", None) or llm
    assert isinstance(inner, OpenAICompatibleProvider) or isinstance(
        llm, OpenAICompatibleProvider)


# ─── Reasoning effort (Phase 2 — eval lane) ──────────────────────────


class TestEvalReasoningEffort:
    """`--reasoning` reaches the Stage-2 synthesis provider, and only it."""

    def _provider(self, monkeypatch, reasoning):
        from unittest.mock import patch
        from core.src.eval import golden_cli

        monkeypatch.setenv("NORA_LLM_PROVIDER", "openai-compatible")
        monkeypatch.setenv("NORA_LLM_BASE_URL", "https://example.test/v1")
        monkeypatch.setenv("NORA_LLM_API_KEY", "k")
        monkeypatch.setenv("NORA_LLM_MODEL", "m")
        # No refusal fallback configured — keep the provider unwrapped.
        monkeypatch.delenv("NORA_LLM_REFUSAL_MARKERS", raising=False)
        monkeypatch.delenv("NORA_LLM_FALLBACK_BASE_URL", raising=False)
        with patch.object(golden_cli, "__name__", golden_cli.__name__):
            return golden_cli._build_llm(model="m", reasoning=reasoning)

    def test_level_reaches_the_provider(self, monkeypatch):
        llm = self._provider(monkeypatch, "none")
        assert llm._reasoning == "none"

    def test_absent_level_sends_nothing(self, monkeypatch):
        llm = self._provider(monkeypatch, None)
        assert llm._reasoning is None

    def test_empty_string_is_treated_as_unset(self, monkeypatch):
        # argparse default is "" — it must mean "endpoint default",
        # not a literal empty reasoning_effort field on the wire.
        llm = self._provider(monkeypatch, "")
        assert llm._reasoning is None


# ─── Sample kind + relative releases (release-handling design) ────────


from core.src.eval.golden import (  # noqa: E402
    KIND_STANDARD,
    is_relative_release,
    relative_offset,
)


def _gt(release, mno="mno-b"):
    return GroundTruthEntry(req_id="R-1", mno=mno, release=release)


def test_kind_defaults_to_standard_and_round_trips():
    s = GoldenSample.from_dict({"sample_id": "gs-0001", "query": "q"})
    assert s.kind == KIND_STANDARD
    s.kind = "version"
    assert GoldenSample.from_dict(s.to_dict()).kind == "version"


def test_relative_release_grammar():
    assert is_relative_release("N") and is_relative_release("N-2")
    assert not is_relative_release("N-0")
    assert not is_relative_release("n") and not is_relative_release("Jan2026")
    assert relative_offset("N") == 0 and relative_offset("N-3") == 3


def test_validate_kind_and_relative_rules():
    s = GoldenSample(sample_id="gs-0001", query="q", kind="weird")
    assert any("kind" in p for p in validate_sample(s))
    std = GoldenSample(sample_id="gs-0001", query="q", ground_truth=[_gt("N")])
    assert any("only valid in a version" in p for p in validate_sample(std))
    ver = GoldenSample(sample_id="gs-0001", query="q", kind="version",
                       ground_truth=[_gt("N", mno="")])
    assert any("needs the entry's mno" in p for p in validate_sample(ver))
    mixed = GoldenSample(sample_id="gs-0001", query="q", kind="version",
                         ground_truth=[_gt("N"), _gt("Jan2026")])
    assert any("not both" in p for p in validate_sample(mixed))
    bare = GoldenSample(sample_id="gs-0001", query="q", kind="version",
                        ground_truth=[_gt("")])
    assert any("needs a release" in p for p in validate_sample(bare))
    ok = GoldenSample(sample_id="gs-0001", query="q", kind="version",
                      ground_truth=[_gt("N"), GroundTruthEntry(
                          req_id="R-2", mno="mno-b", release="N-1")])
    assert validate_sample(ok) == []


# ─── CLI: release map wiring ─────────────────────────────────────────


def _cli_env(tmp_path, monkeypatch, healthz_cells):
    from core.src.eval import golden_runner
    from core.src.eval.golden import save_sample
    from core.src.eval.release_map import ReleaseMap, save_release_map

    save_sample(tmp_path, GoldenSample(
        sample_id="gs-0001", query="q", status="stage1-ready",
        ground_truth=[GroundTruthEntry(req_id="R-1", mno="mno-b",
                                       release="Jan2026")]))
    save_release_map(tmp_path / "eval" / "golden", ReleaseMap(
        version=1, mnos={"mno-b": ["Jan2026", "Apr2026"]}))
    monkeypatch.setattr(golden_runner, "fetch_healthz",
                        lambda url: {"cells": healthz_cells})
    monkeypatch.setattr(golden_runner, "_post_json", lambda u, p, t: {
        "results": [{"rank": 1, "req_id": "R-1", "mno": "mno-b",
                     "release": "Apr2026"}],
        "resolved_cells": ["mno-b__Apr2026"]})


def test_cli_refuses_map_stack_disagreement(tmp_path, monkeypatch, capsys):
    from core.src.eval.golden_cli import main

    _cli_env(tmp_path, monkeypatch, ["mno-b__Jan2026"])
    argv = ["--env-dir", str(tmp_path), "--stack-url",
            "http://127.0.0.1:9999", "--stage", "1"]
    assert main(argv) == 2
    assert "GEV-E006" in capsys.readouterr().out
    assert not (tmp_path / "eval" / "golden" / "runs").exists()
    assert main(argv + ["--allow-map-mismatch"]) == 0
    assert "rmap=v1!" in capsys.readouterr().out


def test_cli_scores_with_map(tmp_path, monkeypatch, capsys):
    from core.src.eval.golden_cli import main

    _cli_env(tmp_path, monkeypatch, ["mno-b__Jan2026", "mno-b__Apr2026"])
    assert main(["--env-dir", str(tmp_path), "--stack-url",
                 "http://127.0.0.1:9999", "--stage", "1"]) == 0
    out = capsys.readouterr().out
    assert "match=m3 rmap=v1" in out
    assert "recall_avg=1.00" in out
    assert "scope: ok=1" in out


def test_cli_bad_pinned_map_version(tmp_path, monkeypatch, capsys):
    from core.src.eval.golden_cli import main

    _cli_env(tmp_path, monkeypatch, ["mno-b__Apr2026"])
    assert main(["--env-dir", str(tmp_path), "--stack-url",
                 "http://127.0.0.1:9999", "--stage", "1",
                 "--release-map-version", "9"]) == 2
