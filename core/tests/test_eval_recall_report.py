"""Silver recall report over the feedback DB (strand recall-metrics, design §7).

The DB is built from web's own schema, so a renamed table or column fails
here instead of silently reporting nothing. Rows carry numbers and intents
only; no question text reaches the report."""
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from core.src.eval import recall_report as rr
from core.src.web.feedback_db import _SCHEMA

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


def _sr(r10, r20=None, r40=None, *, cited=4, outside=0, fetched=40, packed=40):
    recall = {"10": r10}
    if r20 is not None:
        recall["20"] = r20
    if r40 is not None:
        recall.update({"30": r40, "40": r40})
    return {"recall": recall, "cited": cited, "outside": outside, "fetched": fetched, "packed": packed}


def _db(tmp_path, rows):
    path = tmp_path / "fb.db"
    con = sqlite3.connect(path)
    con.executescript(_SCHEMA)
    for ts, lane, lc in rows:
        con.execute("INSERT INTO test_feedback (timestamp, section, question, answer, lane, lane_config)"
                    " VALUES (?, 'merged', 'secret question text', 'an answer', ?, ?)",
                    (ts, lane, json.dumps(lc) if lc is not None else None))
    con.commit()
    con.close()
    return path


@pytest.fixture()
def db(tmp_path):
    return _db(tmp_path, [
        ("2026-11-12T10:00:00+00:00", "sira", {"intent": "list", "stack": "s1", "silver_recall": _sr(0.5, 1.0, fetched=20, packed=15)}),
        ("2026-11-12T11:00:00+00:00", "sira", {"intent": "list", "stack": "s2", "silver_recall": _sr(1.0, 1.0, 1.0)}),
        ("2026-11-13T09:00:00+00:00", "sira", {"intent": "fact", "stack": "s1", "silver_recall": _sr(0.25, 0.5, 0.75, outside=1)}),
        ("2026-11-13T09:30:00+00:00", "sira", {"intent": "fact", "stack": "s1"}),               # no citations: skipped
        ("2026-11-13T09:40:00+00:00", "nora", {"silver_recall": _sr(0.0)}),                     # other lane: ignored
        ("2026-10-01T09:00:00+00:00", "sira", {"intent": "list", "silver_recall": _sr(0.0)}),   # outside the window
    ])


def test_since_parses_days_and_hours():
    assert rr.parse_since("7d") == rr.timedelta(days=7)
    assert rr.parse_since("36h") == rr.timedelta(hours=36)
    with pytest.raises(ValueError):
        rr.parse_since("week")


def test_load_keeps_sira_rows_in_the_window_and_counts_skipped(db):
    rows, skipped = rr.load(db, since=NOW - rr.timedelta(days=7))
    assert [r["intent"] for r in rows] == ["list", "list", "fact"]
    assert skipped == 1


def test_filters(db):
    since = NOW - rr.timedelta(days=7)
    assert [r["intent"] for r in rr.load(db, since=since, intent="fact")[0]] == ["fact"]
    rows, skipped = rr.load(db, since=since, stack="s1")
    assert len(rows) == 2 and skipped == 1


def test_each_k_is_averaged_over_the_rows_that_reached_it(db):
    rows, _ = rr.load(db, since=NOW - rr.timedelta(days=7))
    t = {g["intent"]: g for g in rr.summarize(rows)}
    lst = t["list"]
    assert lst["n"] == 2
    assert lst["recall"]["10"] == 0.75 and lst["n_k"]["10"] == 2
    assert lst["recall"]["40"] == 1.0 and lst["n_k"]["40"] == 1         # the 20-fetch row has no @40
    assert lst["budget_cut"] == 0.5                                       # one of two had packed < fetched
    assert t["fact"]["outside"] == 1.0
    assert t["all"]["n"] == 3 and t["all"]["recall"]["10"] == round((0.5 + 1.0 + 0.25) / 3, 4)


def test_buckets_by_day_and_week(db):
    rows, _ = rr.load(db, since=NOW - rr.timedelta(days=7))
    days = rr.series(rows, "day")
    assert sorted({s["bucket"] for s in days}) == ["2026-11-12", "2026-11-13"]
    weeks = rr.series(rows, "week")
    assert {s["bucket"] for s in weeks} == {"2026-11-09"}                 # Monday of that week


def test_run_and_files_hold_no_question_text(db, tmp_path):
    rows, skipped = rr.load(db, since=NOW - rr.timedelta(days=7))
    run = rr.build_run(rows, skipped=skipped, since="7d", bucket="day", intent=None, stack=None,
                       created_at=NOW.replace(tzinfo=None), git={"commit": "abc", "dirty": False})
    out = rr.write_run(run, tmp_path / "reports")
    assert out.name == "2026-11-13-1200_silver_7d"
    for f in ("run.json", "report.html"):
        text = (out / f).read_text()
        assert "secret question text" not in text and str(db) not in text
    assert json.loads((out / "run.json").read_text())["meta"]["skipped"] == 1


def test_cli_end_to_end(db, tmp_path, capsys):
    code = rr.main(["--db", str(db), "--since", "3650d", "--out-root", str(tmp_path / "r")])
    assert code == 0
    out = capsys.readouterr().out
    assert "list" in out and "fact" in out and "all" in out


def test_cli_missing_db(tmp_path, capsys):
    assert rr.main(["--db", str(tmp_path / "nope.db")]) == 2


def test_halves_round_up_like_the_page_and_the_card():
    assert rr._pct(0.625) == 63 and rr._pct(0.6249) == 62 and rr._pct(1.0) == 100
