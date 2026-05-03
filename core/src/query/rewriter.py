"""Query rewriting / expansion (TDD §7 — pre-retrieval enrichment).

Concept queries that lack specific terms ("What requirements exist for
network detach handling?") often miss the right chunks because the
embedding signal is spread thin across many topically-related candidates
and BM25 has no rare-token anchors. An LLM-generated paraphrase / synonym
expansion provides additional retrieval signal — adding telecom-specific
terminology the user query didn't contain ("UE-initiated detach",
"DETACH REQUEST", "EMM-IDLE", etc.).

Two implementations behind a `QueryRewriter` Protocol:
- `MockQueryRewriter`: returns no rewrites (lets pipelines run offline
  and the deterministic eval path stay deterministic).
- `LLMQueryRewriter`: calls the project's LLM to produce N short
  paraphrases. Logs + falls back to no rewrites on any error.

The rewriter's output is a `list[str]` of additional queries the
retriever should also see. The pipeline concatenates them with the
original query before embedding / BM25 (Option A — simplest path).
A future Option B would run separate retrievals per rewrite and fuse
via RRF, at the cost of N× retrieval roundtrips.
"""

from __future__ import annotations

import logging
import re
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# Telecom rewrite prompt. Empirical tuning notes:
#   - Earlier version listed the corpus topics (LTE / IMS / SMS / data
#     retry / OTA DM / AT commands / 3GPP) as context to ground the
#     rewriter in domain. Result: LLM parroted ALL those topics into
#     EVERY rewrite, turning a focused 10-token query into a 100-token
#     bag of off-topic tokens that diluted retrieval. This version
#     omits the topic list entirely — the prompt only tells the model
#     WHAT to do, not what the corpus covers.
#   - Hard length cap (≤15 words) keeps rewrites from growing into
#     prose paragraphs. Caller still truncates at 240 chars as a
#     safety net.
#   - "different terms, same scope" is the core rule — over-broadening
#     hurts retrieval more than the rewrites help.
_REWRITE_PROMPT = """\
Generate exactly {n} alternative phrasings of this query. Each alternative MUST:
- Be 5-15 words long. No prose, no extra context.
- Use different terminology for the same concepts (acronyms vs expansions,
  synonymous procedure names, equivalent spec terms).
- Cover EXACTLY the same scope as the original — do NOT add topics, broaden,
  narrow, or list extra subjects the original didn't mention.

Output ONLY the {n} alternatives, one per line, no numbering, no preamble.

Query: {query}
"""


@runtime_checkable
class QueryRewriter(Protocol):
    """Protocol for query rewriters.

    Any class with a matching `rewrite(query: str) -> list[str]` method
    satisfies this protocol. The returned list is empty when no rewrites
    are produced (mock mode, LLM error, or short query).
    """

    def rewrite(self, query: str) -> list[str]:
        ...


class MockQueryRewriter:
    """Deterministic no-op rewriter — keeps offline eval reproducible.

    Returns an empty list so the pipeline behaves exactly as it did
    before query rewriting was introduced. Use in tests and offline
    profiling where LLM cost / variance isn't acceptable.
    """

    def rewrite(self, query: str) -> list[str]:
        return []


class LLMQueryRewriter:
    """LLM-driven rewriter — produces N short paraphrases per call.

    Uses the project's `LLMProvider` Protocol. On any failure (timeout,
    parsing error, LLM unavailable) returns an empty list and logs a
    warning — the pipeline then proceeds with the original query
    unchanged. Never raises into the retrieval path.
    """

    # Cap rewrites at this length — anything longer is probably the LLM
    # ignoring the prompt and writing prose.
    _MAX_REWRITE_LEN = 240

    # Skip the rewrite call entirely when the original query is shorter
    # than this — single-word lookups don't benefit and add LLM cost.
    _MIN_QUERY_LEN = 12

    def __init__(self, llm_provider, n_rewrites: int = 3) -> None:
        if n_rewrites < 1:
            raise ValueError("n_rewrites must be >= 1")
        self._llm = llm_provider
        self._n = n_rewrites

    def rewrite(self, query: str) -> list[str]:
        if not query or len(query.strip()) < self._MIN_QUERY_LEN:
            return []
        prompt = _REWRITE_PROMPT.format(query=query.strip(), n=self._n)
        try:
            response = self._llm.complete(
                prompt=prompt,
                system="You rewrite telecom requirement queries concisely.",
                temperature=0.2,
            )
        except Exception as e:
            logger.warning(
                f"Query rewrite LLM call failed ({e!r}); using original query only"
            )
            return []
        return self._parse_rewrites(response)

    def _parse_rewrites(self, response: str) -> list[str]:
        """Pull rewrites out of the LLM response.

        Tolerates: numbered lists ("1.", "1)", "- "), blank lines,
        leading/trailing whitespace, optional quote characters. Caps
        each rewrite at `_MAX_REWRITE_LEN` and the total count at
        `self._n`.
        """
        if not response:
            return []
        rewrites: list[str] = []
        for line in response.splitlines():
            cleaned = line.strip()
            if not cleaned:
                continue
            # Strip common list-marker prefixes
            cleaned = re.sub(r"^\s*(?:\d+[.):]|[-*•])\s*", "", cleaned)
            # Strip wrapping quotes
            cleaned = cleaned.strip('"“”').strip("'‘’")
            if not cleaned:
                continue
            # Cap length — anything beyond is likely the LLM going off-rails
            if len(cleaned) > self._MAX_REWRITE_LEN:
                cleaned = cleaned[:self._MAX_REWRITE_LEN]
            rewrites.append(cleaned)
            if len(rewrites) >= self._n:
                break
        return rewrites


def expand_query(query: str, rewrites: list[str]) -> str:
    """Combine the original query with its rewrites into a single
    enriched query string, joined by ` | ` so it reads cleanly in
    debug logs but is treated as a single bag-of-tokens by both BM25
    and dense embedders.

    No-op when `rewrites` is empty — original query is returned
    unchanged so the rest of the pipeline sees the same input it
    always has.
    """
    if not rewrites:
        return query
    return " | ".join([query.strip()] + [r.strip() for r in rewrites if r.strip()])
