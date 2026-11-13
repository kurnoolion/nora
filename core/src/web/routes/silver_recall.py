"""Silver recall for a SIRA select-synth answer (strand recall-metrics).

The requirements the answer cited stand in for gold labels; silver
recall@k is the share of them that BM25 ranked within the top k of the
fetch. It shows how deep the evidence sat, not what BM25 missed: a
requirement never fetched cannot be cited. Pure functions, no I/O.
"""

from __future__ import annotations

from typing import Iterable

_STEP = 10
_CAP = 40


def _k_steps(fetched: int) -> list[int]:
    """10, 20, ... up to the fetched count, capped at 40; a fetched count
    that is not a multiple of 10 is the last step."""
    top = min(fetched, _CAP)
    steps = list(range(_STEP, top + 1, _STEP))
    if top and (not steps or steps[-1] != top):
        steps.append(top)
    return steps


def compute(results: list[dict], packed: Iterable, cited_ids: Iterable[str]) -> "dict | None":
    """Silver recall for one answer, or None when nothing was cited.

    `results` is SIRA's fetched list with its `rank` field (list position
    when absent); `packed` the SIRA
    results that made it into the LLM context; `cited_ids` the requirement
    ids the answer cited. A req_id fetched twice (two cells) keeps its better
    rank; a cited id not fetched at all counts as outside."""
    cited = list(dict.fromkeys(cited_ids))
    if not cited:
        return None
    best: dict[str, int] = {}
    for pos, r in enumerate(results, 1):
        rid, rank = r.get("req_id"), r.get("rank") or pos
        if rid and (rid not in best or rank < best[rid]):
            best[rid] = rank
    ranks = [best[c] for c in cited if c in best]
    recall = {str(k): round(sum(1 for r in ranks if r <= k) / len(cited), 4)
              for k in _k_steps(len(results))}
    return {"recall": recall, "cited": len(cited), "outside": len(cited) - len(ranks),
            "fetched": len(results), "packed": len(list(packed))}


def line(lane: str, lane_config: "dict | None") -> "dict | None":
    """The stored silver recall for an answer card; None for other lanes,
    older rows, or answers that cited nothing."""
    if lane != "sira":
        return None
    return (lane_config or {}).get("silver_recall") or None
