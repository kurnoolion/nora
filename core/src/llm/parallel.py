"""Parallel execution of batched language-model calls (strand rel-history).

`run` takes the batches `batching.pack` produced and a caller-supplied
`call(batch) -> str` — the caller's closure around its provider's
`complete()`, prompt building included — and runs up to `parallel`
calls at a time on a thread pool, since every `LLMProvider` is a
blocking HTTP client. Each batch's outcome is delivered as it completes
through `on_result`, which a web route turns into streamed progress;
`RunResult` collects everything keyed by batch index so the caller
reassembles in plan order.

A cancel event stops new batches from starting; batches already in
flight run to completion and are delivered, because `complete()` is
one-shot and cannot be interrupted without changing the Protocol. A
failing batch is recorded as an error for that index and does not stop
the run. There are no retries beyond what `call` does itself (the
refusal fallback lives in the provider wrapper), and no rate limiting:
`parallel` is the throttle.

The Protocol stays one-shot text in, text out; this module adds no
method to it. Stdlib only.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any, Callable

from core.src.llm.batching import Batch

logger = logging.getLogger(__name__)


@dataclass
class BatchOutcome:
    index: int
    group: str
    text: str = ""
    error: str = ""              # exception type name and message; never prompt text
    elapsed_ms: int = 0
    call_stats: dict = field(default_factory=dict)   # provider last_call_stats when available

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass
class RunResult:
    outcomes: dict[int, BatchOutcome] = field(default_factory=dict)
    total: int = 0
    cancelled: bool = False
    elapsed_ms: int = 0

    @property
    def done(self) -> int:
        return len(self.outcomes)

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes.values() if not o.ok)

    @property
    def skipped(self) -> int:
        """Batches never started because of a cancel."""
        return self.total - self.done

    def ordered(self) -> list[BatchOutcome]:
        return [self.outcomes[i] for i in sorted(self.outcomes)]

    def latency_ms(self) -> dict[str, int]:
        """Per-batch wall-clock summary of the finished batches: p50, p95,
        max. Separates a slow model from a slow runner at a glance."""
        xs = sorted(o.elapsed_ms for o in self.outcomes.values())
        if not xs:
            return {"p50": 0, "p95": 0, "max": 0}
        def pct(q: float) -> int:
            return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]
        return {"p50": pct(0.5), "p95": pct(0.95), "max": xs[-1]}


def run(batches: list[Batch], call: Callable[[Batch], str], *, parallel: int = 4,
        cancel: "threading.Event | None" = None,
        on_result: "Callable[[BatchOutcome], None] | None" = None,
        stats_of: "Callable[[], dict] | None" = None) -> RunResult:
    """Run `call` over `batches`, `parallel` at a time.

    `on_result` fires in completion order, from worker threads' completion
    being observed on the calling thread (so a web route may touch its own
    state without locks). `stats_of()` is read after each call to attach
    the provider's `last_call_stats`; callers with one provider per run
    pass `lambda: getattr(provider, "last_call_stats", {})`.
    """
    if parallel < 1:
        raise ValueError("parallel must be at least 1")
    result = RunResult(total=len(batches))
    started = time.time()
    cancel = cancel or threading.Event()
    pending = list(batches)
    in_flight: dict[Future, tuple[Batch, float]] = {}

    def one(batch: Batch) -> str:
        return call(batch)

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        while pending or in_flight:
            while pending and len(in_flight) < parallel and not cancel.is_set():
                batch = pending.pop(0)
                in_flight[pool.submit(one, batch)] = (batch, time.time())
            if not in_flight:
                break                                   # cancelled with nothing running
            done, _ = wait(list(in_flight), return_when=FIRST_COMPLETED)
            for fut in done:
                batch, t0 = in_flight.pop(fut)
                outcome = BatchOutcome(index=batch.index, group=batch.group,
                                       elapsed_ms=int((time.time() - t0) * 1000))
                try:
                    outcome.text = fut.result()
                except Exception as exc:            # noqa: BLE001 — recorded per batch
                    # first line only: a provider error may carry a page
                    outcome.error = f"{type(exc).__name__}: {exc}".splitlines()[0][:300]
                    logger.warning("parallel batch %d (group %r) failed after %d ms: %s",
                                   batch.index, batch.group, outcome.elapsed_ms,
                                   outcome.error)
                if stats_of is not None:
                    try:
                        outcome.call_stats = dict(stats_of() or {})
                    except Exception:               # noqa: BLE001
                        outcome.call_stats = {}
                result.outcomes[batch.index] = outcome
                if on_result is not None:
                    on_result(outcome)
    result.cancelled = cancel.is_set() and result.done < result.total
    result.elapsed_ms = int((time.time() - started) * 1000)
    return result
