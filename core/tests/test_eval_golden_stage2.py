"""Tests for golden Stage-2 (pin + judge), run report, and batch runner.

Pipeline and judge are duck-typed fakes — no LLM, no store, no service.
Synthetic fixtures only (NFR-8).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.src.eval import golden_runner
from core.src.eval.golden import (
    STATUS_GOLDEN_READY,
    STATUS_STAGE1_READY,
    GoldenEvalError,
    GoldenSample,
    GroundTruthEntry,
)
from core.src.eval.golden_runner import (
    GoldenRunReport,
    Stage1Result,
    Stage2Result,
    format_ab_delta,
    load_judge_prompt,
    run_all,
    run_stage2,
    write_run,
)

_NOW = "2026-08-05T12:00:00"


class _FakeResponse:
    def __init__(self, answer):
        self.answer = answer


class _FakePipeline:
    def __init__(self, answer="The widgets shall retry with backoff."):
        self.answer = answer
        self.calls = []

    def query(self, query_text, pinned_chunk_ids=None, **kw):
        self.calls.append((query_text, pinned_chunk_ids))
        return _FakeResponse(self.answer)


class _FakeJudge:
    def __init__(self, raw='{"score": 7.5, "missing": ["backoff cap"], "contradicting": []}'):
        self.raw = raw
        self.prompts = []

    def complete(self, prompt, system=None, temperature=0.0, max_tokens=None):
        self.prompts.append(prompt)
        return self.raw


def _sample(status=STATUS_GOLDEN_READY):
    return GoldenSample(
        sample_id="gs-0001",
        query="widget retry?",
        ground_truth=[GroundTruthEntry(req_id="REQ_FOO_0001")],
        golden_response="Widgets retry with exponential backoff.",
        status=status,
    )


def _stage1(retrieved=("REQ_FOO_0001", "REQ_FOO_0009")):
    return Stage1Result(
        sample_id="gs-0001",
        recall=0.5,
        hits=[{"req_id": "REQ_FOO_0001", "mno": "", "release": "", "rank": 1}],
        misses=[],
        retrieved_req_ids=list(retrieved),
    )


# ─── Judge prompt loading ───────────────────────────────────────────


def test_load_judge_prompt_default_is_highest_version():
    version, text = load_judge_prompt()
    assert version == "v1"
    assert "<<QUERY>>" in text and "<<GOLDEN>>" in text and "<<CANDIDATE>>" in text


def test_load_judge_prompt_picks_highest(tmp_path: Path):
    (tmp_path / "judge_v1.txt").write_text("one", encoding="utf-8")
    (tmp_path / "judge_v3.txt").write_text("three", encoding="utf-8")
    version, text = load_judge_prompt(prompts_dir=tmp_path)
    assert (version, text) == ("v3", "three")


def test_load_judge_prompt_explicit_pin(tmp_path: Path):
    (tmp_path / "judge_v1.txt").write_text("one", encoding="utf-8")
    (tmp_path / "judge_v3.txt").write_text("three", encoding="utf-8")
    assert load_judge_prompt("v1", prompts_dir=tmp_path) == ("v1", "one")


def test_load_judge_prompt_missing_raises(tmp_path: Path):
    with pytest.raises(GoldenEvalError) as exc:
        load_judge_prompt(prompts_dir=tmp_path)
    assert exc.value.code == "GEV-E004"


# ─── run_stage2 ─────────────────────────────────────────────────────


def test_stage2_pins_all_retrieved_and_scores():
    pipeline, judge = _FakePipeline(), _FakeJudge()
    r = run_stage2(_sample(), _stage1(), pipeline, judge, ("v1", "Q:<<QUERY>> G:<<GOLDEN>> C:<<CANDIDATE>>"))
    assert pipeline.calls[0][1] == ["req:REQ_FOO_0001", "req:REQ_FOO_0009"]
    assert r.score == 7.5
    assert r.missing == ["backoff cap"]
    assert r.judge_version == "v1"
    assert r.pinned_count == 2
    prompt = judge.prompts[0]
    assert "widget retry?" in prompt
    assert "exponential backoff" in prompt
    assert "shall retry" in prompt


def test_stage2_requires_golden_response():
    s = _sample(status=STATUS_STAGE1_READY)
    s.golden_response = None
    with pytest.raises(GoldenEvalError) as exc:
        run_stage2(s, _stage1(), _FakePipeline(), _FakeJudge(), ("v1", ""))
    assert exc.value.code == "GEV-W001"


def test_stage2_skips_on_empty_retrieval():
    r = run_stage2(_sample(), _stage1(retrieved=()), _FakePipeline(), _FakeJudge(), ("v1", ""))
    assert r.score is None
    assert "retrieved nothing" in r.skipped_reason


def test_stage2_empty_answer_is_gev_e003():
    with pytest.raises(GoldenEvalError) as exc:
        run_stage2(_sample(), _stage1(), _FakePipeline(answer="  "), _FakeJudge(), ("v1", ""))
    assert exc.value.code == "GEV-E003"


def test_stage2_unparseable_verdict_is_gev_e004():
    judge = _FakeJudge(raw="I think it looks fine.")
    with pytest.raises(GoldenEvalError) as exc:
        run_stage2(_sample(), _stage1(), _FakePipeline(), judge, ("v1", "<<CANDIDATE>>"))
    assert exc.value.code == "GEV-E004"


def test_stage2_score_clamped():
    judge = _FakeJudge(raw='{"score": 14, "missing": [], "contradicting": []}')
    r = run_stage2(_sample(), _stage1(), _FakePipeline(), judge, ("v1", "<<CANDIDATE>>"))
    assert r.score == 10.0


# ─── Report + batch ─────────────────────────────────────────────────


def _report():
    rep = GoldenRunReport(
        stack_label="v2", stack_url="http://127.0.0.1:9999", started_at=_NOW,
        judge_version="v1",
    )
    rep.stage1 = [
        Stage1Result(sample_id="gs-0001", recall=1.0,
                     hits=[{"req_id": "a", "mno": "", "release": "", "rank": 2}],
                     misses=[]),
        Stage1Result(sample_id="gs-0002", recall=0.0, hits=[],
                     misses=[{"req_id": "b", "mno": "", "release": ""}]),
    ]
    rep.stage2 = [
        Stage2Result(sample_id="gs-0001", score=8.0, judge_version="v1"),
        Stage2Result(sample_id="gs-0002", score=None, judge_version="v1",
                     skipped_reason="stage1 retrieved nothing"),
    ]
    return rep


def test_compact_report_shape():
    text = _report().compact_report("envx")
    lines = text.splitlines()
    assert lines[0] == f"GEV envx v2 {_NOW} judge=v1"
    assert lines[1] == "s1: n=2 recall_avg=0.50 r@5=0.50 r@10=0.50 full=1 zero=1"
    assert lines[2] == "s2: n=1 judge_avg=8.0 judge_med=8.0"
    assert lines[3] == "err: none"
    # NFR-8: no sample content in the compact block
    assert "gs-" not in text and "REQ_" not in text


def test_run_id_and_write_run(tmp_path: Path):
    rep = _report()
    assert rep.run_id == "20260805T120000-v2"
    rdir = write_run(tmp_path, rep, env_name="envx")
    assert (rdir / "report.json").exists()
    assert (rdir / "report.txt").read_text().startswith("GEV envx v2")


def test_format_ab_delta_flags_judge_mismatch():
    a, b = _report(), _report()
    b.stack_label = "v3"
    b.stage1[1].recall = 1.0
    b.stage2[0].score = 9.0
    assert format_ab_delta(a, b) == "delta v2->v3: recall=+0.50 judge=+1.0"
    b.judge_version = "v2"
    assert "JUDGE MISMATCH" in format_ab_delta(a, b)


def test_run_all_statuses_and_error_capture(monkeypatch):
    monkeypatch.setattr(golden_runner, "fetch_healthz", lambda url, timeout=30.0: {"ok": True})

    def fake_post(url, payload, timeout):
        if "boom" in payload["query"]:
            raise OSError("down")
        return {
            "results": [{"rank": 1, "req_id": "REQ_FOO_0001"}],
            "top_k": 10, "effective_top_k": 10, "mode": "multi-cell",
            "resolved_cells": [],
        }

    monkeypatch.setattr(golden_runner, "_post_json", fake_post)
    samples = [
        GoldenSample(sample_id="gs-0001", query="q1",
                     ground_truth=[GroundTruthEntry(req_id="REQ_FOO_0001")],
                     golden_response="golden.", status=STATUS_GOLDEN_READY),
        GoldenSample(sample_id="gs-0002", query="q2 boom",
                     ground_truth=[GroundTruthEntry(req_id="REQ_FOO_0002")],
                     status=STATUS_STAGE1_READY),
        GoldenSample(sample_id="gs-0003", query="q3"),  # draft
    ]
    rep = run_all(
        samples, "http://127.0.0.1:9999", "v2", _NOW,
        query_pipeline=_FakePipeline(), judge=_FakeJudge(),
        judge_prompt=("v1", "<<CANDIDATE>>"),
    )
    assert rep.healthz == {"ok": True}
    assert [r.sample_id for r in rep.stage1] == ["gs-0001"]
    assert [r.sample_id for r in rep.stage2] == ["gs-0001"]
    codes = {e["sample_id"]: e["code"] for e in rep.errors}
    assert codes == {"gs-0002": "GEV-E002", "gs-0003": "GEV-W001"}


# ─── SIRA-lane Stage-2 (field-found: legacy path needs graph+vectorstore
#     the SIRA-only lane never builds; skip was silent) ───────────────


class _FakeSynthLLM:
    """LLMProvider fake for the SiraRowsPipeline's synthesizer."""

    def __init__(self, answer="Synthesized from retrieved rows."):
        self.answer = answer
        self.prompts = []

    def complete(self, prompt, system=None, temperature=0.0, max_tokens=None):
        self.prompts.append((system or "") + "\n" + prompt)
        return self.answer


def _rows():
    return [
        {"req_id": "REQ_FOO_0001", "title": "Retry",
         "text": "Widgets shall retry with backoff.", "mno": "m", "release": "r"},
        {"req_id": "REQ_FOO_0009", "title": "Cap",
         "text": "Backoff shall be capped.", "mno": "m", "release": "r"},
    ]


class TestSiraRowsPipeline:
    def test_synthesizes_over_bound_rows_in_pinned_order(self):
        llm = _FakeSynthLLM()
        p = golden_runner.SiraRowsPipeline(llm)
        p.bind_rows(_rows())
        resp = p.query("widget retry?",
                       pinned_chunk_ids=["req:REQ_FOO_0009", "req:REQ_FOO_0001"])
        assert resp.answer == "Synthesized from retrieved rows."
        sent = llm.prompts[0]
        assert "Backoff shall be capped." in sent
        assert "Widgets shall retry with backoff." in sent
        # Pinned order respected: 0009's text appears before 0001's.
        assert sent.index("capped") < sent.index("retry with backoff")

    def test_no_bound_text_raises_loud(self):
        p = golden_runner.SiraRowsPipeline(_FakeSynthLLM())
        p.bind_rows([])
        with pytest.raises(GoldenEvalError) as exc:
            p.query("q", pinned_chunk_ids=["req:REQ_FOO_0001"])
        assert exc.value.code == "GEV-E003"

    def test_run_all_sira_mode_binds_rows_and_scores(self, monkeypatch):
        monkeypatch.setattr(
            golden_runner, "_get_json", lambda url, timeout: {})

        def fake_post(url, payload, timeout):
            # want_row_text must request full text from the service.
            assert payload.get("text_chars")
            return {"results": [
                dict(rank=i + 1, req_id=r["req_id"], mno="m", release="r",
                     title=r["title"], text=r["text"]) for i, r in
                enumerate(_rows())
            ]}

        monkeypatch.setattr(golden_runner, "_post_json", fake_post)
        pipeline = golden_runner.SiraRowsPipeline(_FakeSynthLLM())
        judge = _FakeJudge()
        report = run_all(
            [_sample()], "http://127.0.0.1:1", "stackA", _NOW,
            query_pipeline=pipeline, judge=judge,
            judge_prompt=("v1", "<<QUERY>> <<GOLDEN>> <<CANDIDATE>>"),
        )
        assert report.stage2_mode == "sira-rows"
        assert report.stage2_skip_reason == ""
        assert len(report.stage2) == 1
        assert report.stage2[0].score == 7.5

    def test_skip_reason_lands_in_report_and_compact(self, monkeypatch):
        monkeypatch.setattr(
            golden_runner, "_get_json", lambda url, timeout: {})
        monkeypatch.setattr(
            golden_runner, "_post_json",
            lambda url, payload, timeout: {"results": []})
        report = run_all(
            [_sample()], "http://127.0.0.1:1", "stackA", _NOW,
            stage2_skip_reason="vector store missing",
        )
        assert report.stage2_mode == ""
        assert report.stage2_skip_reason == "vector store missing"
        assert report.to_dict()["stage2_skip_reason"] == "vector store missing"
        assert "s2: SKIPPED (vector store missing)" in report.compact_report()

    def test_rows_never_serialized(self):
        s1 = _stage1()
        s1.retrieved_rows = _rows()
        assert "retrieved_rows" not in s1.to_dict()
