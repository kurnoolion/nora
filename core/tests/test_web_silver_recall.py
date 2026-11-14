"""Silver recall: how deep in the BM25 fetch the cited requirements sat
(strand recall-metrics, design §2-§3)."""
from core.src.web.routes import silver_recall


def _fetched(n, prefix="R"):
    return [{"req_id": f"{prefix}{i}", "rank": i} for i in range(1, n + 1)]


def test_worked_example():
    results, in_context = _fetched(40), 31
    out = silver_recall.compute(results, results[:in_context], ["R2", "R7", "R18", "R35", "GONE"])
    assert out == {"recall": {"10": 0.4, "20": 0.6, "30": 0.6, "40": 0.8},
                   "cited": 5, "outside": 1, "fetched": 40, "packed": 31}


def test_all_cited_in_top_ten():
    results = _fetched(40)
    out = silver_recall.compute(results, results, ["R1", "R10"])
    assert out["recall"] == {"10": 1.0, "20": 1.0, "30": 1.0, "40": 1.0}
    assert out["outside"] == 0


def test_all_outside():
    out = silver_recall.compute(_fetched(20), [], ["X1", "X2"])
    assert out["recall"] == {"10": 0.0, "20": 0.0}
    assert out["outside"] == 2 and out["cited"] == 2


def test_duplicate_req_id_across_cells_keeps_best_rank():
    results = [{"req_id": "A", "rank": 1}] + _fetched(30)[1:]
    results[24] = {"req_id": "DUP", "rank": 25}
    results[4] = {"req_id": "DUP", "rank": 5}
    out = silver_recall.compute(results, results, ["DUP"])
    assert out["recall"]["10"] == 1.0


def test_repeated_citation_counts_once():
    results = _fetched(10)
    out = silver_recall.compute(results, results, ["R1", "R1", "GONE"])
    assert out["cited"] == 2 and out["recall"] == {"10": 0.5}


def test_k_steps():
    def steps(n):
        r = _fetched(n)
        return list(silver_recall.compute(r, r, ["R1"])["recall"])
    assert steps(40) == ["10", "20", "30", "40"]
    assert steps(25) == ["10", "20", "25"]
    assert steps(7) == ["7"]
    assert steps(120) == ["10", "20", "30", "40"]


def test_rounded_to_four_places():
    results = _fetched(10)
    out = silver_recall.compute(results, results, ["R1", "X", "Y"])
    assert out["recall"]["10"] == 0.3333


def test_no_citations_is_none():
    assert silver_recall.compute(_fetched(10), [], []) is None


def test_line_reads_back_sira_rows_only():
    sr = {"recall": {"10": 1.0}, "cited": 1, "outside": 0, "fetched": 10, "packed": 10}
    assert silver_recall.line("sira", {"silver_recall": sr}) == sr
    assert silver_recall.line("sira", {"intent": "list"}) is None
    assert silver_recall.line("sira", None) is None
    assert silver_recall.line("history", {"silver_recall": sr}) is None
    assert silver_recall.line("nora", {"silver_recall": sr}) is None


def test_rank_falls_back_to_list_position():
    out = silver_recall.compute([{"req_id": "A"}, {"req_id": "B"}], [], ["B"])
    assert out["recall"] == {"2": 1.0}


# The select-synth lane stores the numbers on lane_config (design §4).
import asyncio  # noqa: E402

import pytest  # noqa: E402

from core.src.web.routes import playground  # noqa: E402

_FETCHED = [{"req_id": f"REQ_{i:04d}", "rank": i, "mno": "mno-w", "release": "Sep2031",
             "text": f"placeholder body {i}"} for i in range(1, 16)]


@pytest.fixture()
def lane(monkeypatch):
    from core.src.web.routes import query

    reply = {"text": "answer citing REQ_0003 and REQ_0012"}

    class LLM:
        model = "m"

        def complete(self, prompt, system="", temperature=0.0, max_tokens=0):
            if isinstance(reply["text"], Exception):
                raise reply["text"]
            return reply["text"]

    async def store(*a, **k):
        return {"results": [dict(r) for r in _FETCHED]}

    async def snap():
        return {}

    async def say(msg):
        pass

    monkeypatch.delenv("NORA_PIN_NAMED_IDS", raising=False)
    monkeypatch.setattr(query, "_build_llm_from_env_or_default", lambda **k: LLM())
    monkeypatch.setattr(playground, "_call_store_query", store)
    monkeypatch.setattr(playground, "_snapshot_sira_lane_config", snap)

    def run():
        return asyncio.run(playground._run_select_synth_lane("a question", say, 0.0))

    return run, reply


def test_lane_config_carries_silver_recall_after_an_answer(lane):
    run, _ = lane
    out = run()
    assert out["cited_ids"] == ["REQ_0003", "REQ_0012"]
    sr = out["lane_config"]["silver_recall"]
    assert sr["recall"] == {"10": 0.5, "15": 1.0}
    assert sr["cited"] == 2 and sr["outside"] == 0 and sr["fetched"] == 15
    assert sr["packed"] == 15


def test_no_silver_recall_on_synth_error(lane):
    run, reply = lane
    reply["text"] = RuntimeError("model down")
    out = run()
    assert out["synth_error"]
    assert "silver_recall" not in out["lane_config"]
