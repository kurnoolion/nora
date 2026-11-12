"""Intent confidence report (strand sira-intent, design §8).

Covers:
- `collect`: one decision call per sample, the model's own intent and
  confidence beside the rules intent; a provider error stops the run (no
  silent rules fallback); an unknown value counts as no model answer.
- `sweep`, `bins`, `summarize`: accuracy, change precision/recall and Kev's
  share per floor; accuracy per confidence bin; plateau, change-bar start,
  crossover.
- `build_run` / `render_html` / `write_run`: run metadata for correlating
  runs, no query text anywhere, a timestamped folder with a slug.
- The CLI end to end on the mock decision provider.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from core.src.decision.base import DecisionAnswer, DecisionError
from core.src.eval import intent_report as ir
from core.src.eval.intent_eval import IntentSample
from core.src.query.schema import Intent

F, L, C, O = Intent.FACT, Intent.LIST, Intent.CHANGE, Intent.OTHER


def _row(sid, expected, rules, model, conf):
    return ir.Row(sid, expected, rules, model, conf)


# Four rows: Kev right and confident on r1, right but unsure on r2, wrong and
# confident on r3; the rules are right on r2 and r3 only.
ROWS = [
    _row("r1", F, O, F, 0.9),
    _row("r2", L, L, L, 0.3),
    _row("r3", C, C, O, 0.6),
    _row("r4", O, F, None, None),
]


class _Provider:
    model = "fake"

    def __init__(self, answers):
        self.answers, self.calls = answers, 0

    def decide(self, state, questions):
        self.calls += 1
        value, conf = self.answers[state]
        if value is DecisionError:
            raise DecisionError("down")
        return [DecisionAnswer("intent", value, conf)]


def test_collect_keeps_the_model_answer_beside_the_rules():
    samples = [IntentSample("a", "list all requirements for data retry", L),
               IntentSample("b", "hello", O)]
    p = _Provider({samples[0].query: ("list", 0.8), samples[1].query: ("no_such_intent", 0.7)})
    rows = ir.collect(samples, p)
    assert p.calls == 2
    assert rows[0] == ir.Row("a", L, L, L, 0.8)
    assert rows[1].model is None and rows[1].confidence == 0.7


def test_collect_stops_on_a_provider_error():
    samples = [IntentSample("a", "q", O)]
    with pytest.raises(DecisionError):
        ir.collect(samples, _Provider({"q": (DecisionError, 0)}))


def test_sweep_scores_each_floor():
    by = {s["floor"]: s for s in ir.sweep(ROWS, (0.0, 0.5, 0.95))}
    assert by[0.0]["model_decides"] == 3 and by[0.0]["accuracy"] == 0.5     # r1 r2 right
    assert by[0.5]["model_decides"] == 2 and by[0.5]["accuracy"] == 0.5     # r1 r2(rules) right
    assert by[0.95]["model_decides"] == 0 and by[0.95]["accuracy"] == 0.5   # rules: r2 r3
    assert by[0.95]["change_precision"] == 1.0 and by[0.95]["change_recall"] == 1.0
    assert by[0.5]["change_recall"] == 0.0 and by[0.5]["change_bar"] is False


def test_bins_report_both_sides_and_drop_empty_bins():
    b = ir.bins(ROWS, (0.0, 0.5, 1.0))
    assert b == [
        {"lo": 0.0, "hi": 0.5, "n": 1, "model_accuracy": 1.0, "rules_accuracy": 1.0},
        {"lo": 0.5, "hi": 1.0, "n": 2, "model_accuracy": 0.5, "rules_accuracy": 0.5},
    ]


def test_summary_finds_the_plateau_bar_start_and_crossover():
    sweep = [
        {"floor": 0.0, "accuracy": 0.75, "change_bar": False, "model_decides": 10},
        {"floor": 0.4, "accuracy": 0.87, "change_bar": False, "model_decides": 6},
        {"floor": 0.5, "accuracy": 0.87, "change_bar": True, "model_decides": 4},
        {"floor": 0.6, "accuracy": 0.85, "change_bar": True, "model_decides": 2},
        {"floor": 0.9, "accuracy": 0.75, "change_bar": True, "model_decides": 0},
    ]
    bins = [
        {"lo": 0.0, "hi": 0.5, "n": 5, "model_accuracy": 0.4, "rules_accuracy": 0.8},
        {"lo": 0.5, "hi": 0.7, "n": 3, "model_accuracy": 0.9, "rules_accuracy": 0.6},
        {"lo": 0.7, "hi": 1.0, "n": 2, "model_accuracy": 1.0, "rules_accuracy": 0.5},
    ]
    s = ir.summarize(sweep, bins, current_floor=0.5)
    assert s["best_accuracy"] == 0.87 and s["plateau"] == [0.4, 0.5]
    assert s["change_bar_from"] == 0.5
    assert s["rules_accuracy"] == 0.75 and s["model_accuracy"] == 0.75
    assert s["at_current"]["model_decides"] == 4
    assert s["crossover"] == 0.5


def test_run_carries_what_correlates_it_and_no_query_text():
    run = ir.build_run(ROWS, set_id="intent-dev-v1", dataset_sha="abc", floor=0.5,
                       decision={"provider": "fake", "model": "m"},
                       created_at=datetime(2026, 11, 8, 15, 32), git={"commit": "1234567", "dirty": False})
    assert run["meta"]["created_at"] == "2026-11-08T15:32:00"
    assert run["meta"]["git"]["commit"] == "1234567" and run["meta"]["dataset_sha256"] == "abc"
    assert run["meta"]["n"] == 4 and run["meta"]["floor"] == 0.5
    assert {"sample_id", "expected", "rules", "model", "confidence"} == set(run["rows"][0])
    html = ir.render_html(run)
    assert "<title>Kev Confidence Floor</title>" in html
    assert ir.PLACEHOLDER not in html and '"intent-dev-v1"' in html


def test_write_run_makes_a_timestamped_slugged_folder(tmp_path):
    run = ir.build_run(ROWS, set_id="intent-dev-v1", dataset_sha="abc", floor=0.5,
                       decision={"provider": "fake", "model": "m"},
                       created_at=datetime(2026, 11, 8, 15, 32), git={"commit": "x", "dirty": True})
    out = ir.write_run(run, tmp_path)
    assert out.name == "2026-11-08-1532_kev-floor_intent-dev-v1"
    assert json.loads((out / "run.json").read_text())["meta"]["git"]["dirty"] is True
    assert (out / "report.html").read_text().startswith("<meta charset=\"utf-8\">")


def test_cli_end_to_end_on_the_mock_provider(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NORA_DECISION_PROVIDER", "mock")
    assert ir.main(["--out-root", str(tmp_path)]) == 0
    (folder,) = tmp_path.iterdir()
    run = json.loads((folder / "run.json").read_text())
    assert run["meta"]["set_id"] == "intent-dev-v1" and run["meta"]["decision"]["provider"] == "mock"
    assert str(folder) in capsys.readouterr().out


def test_the_report_records_no_endpoint(tmp_path, monkeypatch):
    # Reports are committed: provider and model only, never the server address.
    monkeypatch.setenv("NORA_DECISION_PROVIDER", "mock")
    monkeypatch.setenv("NORA_DECISION_BASE_URL", "http://decision.example.test:1234/v1")
    assert ir.main(["--out-root", str(tmp_path)]) == 0
    (folder,) = tmp_path.iterdir()
    assert set(json.loads((folder / "run.json").read_text())["meta"]["decision"]) == {"provider", "model"}
    for f in ("run.json", "report.html"):
        assert "example.test" not in (folder / f).read_text()


def test_cli_refuses_without_a_decision_provider(monkeypatch, tmp_path):
    monkeypatch.delenv("NORA_DECISION_PROVIDER", raising=False)
    assert ir.main(["--out-root", str(tmp_path)]) == 2
    assert not list(tmp_path.iterdir())
