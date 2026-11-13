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
