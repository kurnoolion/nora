"""Parallel runner (core/src/llm/parallel.py, strand rel-history): N in
flight, completion-order delivery, cancel stops new starts, a failing
batch does not stop the run, results keyed by batch index."""

from __future__ import annotations

import threading
import time

import pytest

from core.src.llm.batching import Batch
from core.src.llm.parallel import BatchOutcome, RunResult, run


def _batches(n: int, group: str = "") -> list[Batch]:
    return [Batch(index=i, group=group, items=[i], bytes=1) for i in range(n)]


def test_results_keyed_by_index_and_delivered_in_completion_order():
    seen: list[int] = []

    def call(b: Batch) -> str:
        time.sleep(0.05 if b.index == 0 else 0.0)        # the first batch finishes last
        return f"out-{b.index}"

    r = run(_batches(3), call, parallel=3, on_result=lambda o: seen.append(o.index))
    assert isinstance(r, RunResult) and r.total == 3 and r.done == 3 and r.failed == 0
    assert [o.text for o in r.ordered()] == ["out-0", "out-1", "out-2"]
    assert seen[-1] == 0 and sorted(seen) == [0, 1, 2]
    assert not r.cancelled and r.skipped == 0 and r.elapsed_ms >= 0


def test_parallelism_is_bounded():
    active, peak = [0], [0]
    lock = threading.Lock()

    def call(b: Batch) -> str:
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.02)
        with lock:
            active[0] -= 1
        return "x"

    run(_batches(8), call, parallel=3)
    assert peak[0] <= 3 and peak[0] >= 2


def test_cancel_stops_new_starts_and_keeps_in_flight_results():
    cancel = threading.Event()

    def call(b: Batch) -> str:
        time.sleep(0.02)
        return f"out-{b.index}"

    def on_result(o: BatchOutcome) -> None:
        cancel.set()                                     # cancel after the first result

    r = run(_batches(10), call, parallel=2, cancel=cancel, on_result=on_result)
    assert r.cancelled and 1 <= r.done <= 2 and r.skipped == 10 - r.done
    assert all(o.ok for o in r.outcomes.values())


def test_failed_batch_is_recorded_and_the_run_continues():
    def call(b: Batch) -> str:
        if b.index == 1:
            raise RuntimeError("endpoint said no")
        return "ok"

    r = run(_batches(3), call, parallel=2)
    assert r.done == 3 and r.failed == 1
    assert r.outcomes[1].error.startswith("RuntimeError: endpoint said no")
    assert r.outcomes[1].text == "" and r.outcomes[0].ok and r.outcomes[2].ok


def test_call_stats_are_attached_when_offered():
    stats = {"model": "m", "tokens_per_second": 1.0}
    r = run(_batches(2), lambda b: "x", parallel=1, stats_of=lambda: stats)
    assert all(o.call_stats == stats for o in r.outcomes.values())
    r2 = run(_batches(1), lambda b: "x", stats_of=lambda: (_ for _ in ()).throw(RuntimeError()))
    assert r2.outcomes[0].call_stats == {}


def test_empty_and_validation():
    r = run([], lambda b: "x")
    assert r.total == 0 and r.done == 0 and not r.cancelled
    with pytest.raises(ValueError):
        run([], lambda b: "x", parallel=0)


def test_latency_summary_and_failure_log(caplog):
    import logging

    def call(b: Batch) -> str:
        time.sleep(0.01 * (b.index + 1))
        if b.index == 2:
            raise ValueError("boom")
        return "x"

    with caplog.at_level(logging.WARNING, logger="core.src.llm.parallel"):
        r = run(_batches(3), call, parallel=3)
    lat = r.latency_ms()
    assert set(lat) == {"p50", "p95", "max"} and lat["max"] >= lat["p95"] >= lat["p50"] >= 0
    assert any("batch 2" in m and "ValueError: boom" in m for m in caplog.messages)
    assert RunResult().latency_ms() == {"p50": 0, "p95": 0, "max": 0}


def test_failed_batch_error_keeps_the_first_line_only():
    from core.src.llm.parallel import Batch, run

    def call(b):
        raise RuntimeError("LLM HTTP 500: first line\n<html>second line with a page</html>")

    rr = run([Batch(index=0, group="g", items=[], bytes=1)], call, parallel=1)
    err = rr.outcomes[0].error
    assert err == "RuntimeError: LLM HTTP 500: first line" and "html" not in err

