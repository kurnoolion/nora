"""Tests for `core/src/query/rewriter.py` — pre-retrieval query expansion.

Pins:
  - `MockQueryRewriter` returns no rewrites (no-op contract used by
    offline tests + the default pipeline path)
  - `LLMQueryRewriter` parses common LLM-output shapes (numbered
    lists, bullets, quoted lines, blank lines)
  - `LLMQueryRewriter` falls back to no rewrites on LLM error or
    short query — never raises into the retrieval path
  - `expand_query` joins original + rewrites with a separator that
    keeps debug logs readable but doesn't break tokenization
"""

from __future__ import annotations

import pytest

from core.src.query.rewriter import (
    LLMQueryRewriter,
    MockQueryRewriter,
    expand_query,
)


# ---------------------------------------------------------------------------
# MockQueryRewriter — the offline / default path
# ---------------------------------------------------------------------------


def test_mock_returns_empty_list_for_any_query():
    """Default pipeline path: mock rewriter is a strict no-op so
    enabling the rewriter slot doesn't change retrieval inputs."""
    m = MockQueryRewriter()
    assert m.rewrite("anything at all") == []
    assert m.rewrite("") == []
    assert m.rewrite("VZ_REQ_LTEDATARETRY_7754") == []


# ---------------------------------------------------------------------------
# LLMQueryRewriter parsing
# ---------------------------------------------------------------------------


class _MockLLM:
    """Stand-in LLM provider that returns a fixed canned response.
    Used to test LLMQueryRewriter parsing without a real LLM."""

    def __init__(self, response: str):
        self._response = response
        self.call_count = 0
        self.last_prompt = None

    def complete(self, prompt, system="", temperature=0.0, max_tokens=4096):
        self.call_count += 1
        self.last_prompt = prompt
        return self._response


def test_llm_rewriter_parses_plain_lines():
    """The expected LLM output shape: one rewrite per line, no
    numbering. Round-trip cleanly into `list[str]`."""
    llm = _MockLLM(
        "What does the EMM cause code 22 trigger?\n"
        "Where does cause 22 fire in the NAS layer?\n"
        "Which reqs cite cause code 22?"
    )
    r = LLMQueryRewriter(llm, n_rewrites=3)
    out = r.rewrite("What requirements mention cause code 22 in VZW?")
    assert out == [
        "What does the EMM cause code 22 trigger?",
        "Where does cause 22 fire in the NAS layer?",
        "Which reqs cite cause code 22?",
    ]


def test_llm_rewriter_strips_numbering_and_bullets():
    """Real LLMs sometimes prepend list markers despite the prompt's
    instructions. Tolerate `1.`, `1)`, `1:`, `-`, `*`, `•`."""
    llm = _MockLLM(
        "1. EMM cause code 22 trigger conditions\n"
        "2) Cause 22 NAS layer firing reqs\n"
        "- Reqs citing cause code 22"
    )
    out = LLMQueryRewriter(llm, n_rewrites=3).rewrite(
        "What requirements mention cause code 22?"
    )
    assert out == [
        "EMM cause code 22 trigger conditions",
        "Cause 22 NAS layer firing reqs",
        "Reqs citing cause code 22",
    ]


def test_llm_rewriter_strips_wrapping_quotes():
    """Some LLMs wrap each rewrite in straight or smart quotes."""
    llm = _MockLLM(
        '"first rewrite"\n'
        "'second rewrite'\n"
        "“third rewrite”"
    )
    out = LLMQueryRewriter(llm, n_rewrites=3).rewrite(
        "any longer query that passes the min-length gate"
    )
    assert out == ["first rewrite", "second rewrite", "third rewrite"]


def test_llm_rewriter_skips_blank_and_marker_only_lines():
    """Blank lines and lines that are ONLY a list marker (e.g. `-`)
    are skipped — they're noise, not rewrites."""
    llm = _MockLLM(
        "first rewrite\n"
        "\n"
        "-\n"
        "second rewrite\n"
        "   \n"
        "third rewrite"
    )
    out = LLMQueryRewriter(llm, n_rewrites=3).rewrite(
        "longer query passing min length"
    )
    assert out == ["first rewrite", "second rewrite", "third rewrite"]


def test_llm_rewriter_caps_count_at_n():
    """The LLM may return more lines than asked for. Cap at `n_rewrites`."""
    llm = _MockLLM("\n".join(f"rewrite {i}" for i in range(10)))
    out = LLMQueryRewriter(llm, n_rewrites=3).rewrite(
        "longer query passing min length"
    )
    assert len(out) == 3
    assert out == ["rewrite 0", "rewrite 1", "rewrite 2"]


def test_llm_rewriter_caps_individual_rewrite_length():
    """A rewrite over the length cap is truncated — usually means the
    LLM started writing prose."""
    long = "a" * 500
    llm = _MockLLM(long)
    out = LLMQueryRewriter(llm, n_rewrites=1).rewrite(
        "longer query passing min length"
    )
    assert len(out) == 1
    assert len(out[0]) == 240  # _MAX_REWRITE_LEN


# ---------------------------------------------------------------------------
# Failure paths — must NOT raise into the retrieval path
# ---------------------------------------------------------------------------


def test_llm_rewriter_short_query_skips_llm_call():
    """Single-word lookups don't benefit from rewriting and would burn
    LLM cost. Skip when query is below `_MIN_QUERY_LEN` (12 chars)."""
    llm = _MockLLM("any output")
    out = LLMQueryRewriter(llm, n_rewrites=3).rewrite("T3402?")
    assert out == []
    assert llm.call_count == 0  # LLM never called


def test_llm_rewriter_empty_query_returns_empty():
    llm = _MockLLM("any output")
    assert LLMQueryRewriter(llm, n_rewrites=3).rewrite("") == []
    assert llm.call_count == 0


class _FailingLLM:
    """LLM that raises on every call — simulates timeouts, unavailable
    Ollama, OpenRouter rate limits, etc."""
    call_count = 0

    def complete(self, prompt, system="", temperature=0.0, max_tokens=4096):
        self.__class__.call_count += 1
        raise RuntimeError("simulated LLM failure")


def test_llm_rewriter_returns_empty_on_llm_error():
    """If the LLM call fails, the rewriter logs a warning and returns
    `[]` so the pipeline proceeds with the original query unchanged."""
    out = LLMQueryRewriter(_FailingLLM(), n_rewrites=3).rewrite(
        "longer query passing min length"
    )
    assert out == []


def test_llm_rewriter_rejects_invalid_n():
    with pytest.raises(ValueError):
        LLMQueryRewriter(_MockLLM(""), n_rewrites=0)


# ---------------------------------------------------------------------------
# expand_query
# ---------------------------------------------------------------------------


def test_expand_query_no_rewrites_returns_original_unchanged():
    """The default pipeline path (no rewrites) must produce a string
    indistinguishable from the original — preserves existing
    retrieval semantics for back-compat."""
    assert expand_query("what is X?", []) == "what is X?"


def test_expand_query_joins_original_and_rewrites():
    """Original first, then rewrites in order, separated by ` | ` so
    debug logs read cleanly while BM25/dense tokenization treat the
    whole string as a bag of tokens."""
    out = expand_query(
        "What is the T3402 timer?",
        ["T3402 NAS attempt-counter behavior", "T3402 implementation specific"],
    )
    assert out == (
        "What is the T3402 timer? | "
        "T3402 NAS attempt-counter behavior | "
        "T3402 implementation specific"
    )


def test_expand_query_drops_empty_rewrites():
    """A rewrite that's empty / whitespace doesn't get joined in —
    avoids producing `... | |  | ...` separator runs."""
    out = expand_query("Q", ["a", "", "  ", "b"])
    assert out == "Q | a | b"


def test_expand_query_strips_outer_whitespace():
    out = expand_query("  Q  ", ["  a  ", "  b  "])
    assert out == "Q | a | b"


# ---------------------------------------------------------------------------
# Tokenizer compatibility — rewrites must produce useful tokens for
# BM25 + dense retrievers
# ---------------------------------------------------------------------------


def test_expanded_query_tokens_include_original_and_rewrite_tokens():
    """The whole point: BM25 and dense embedders see tokens from BOTH
    the original query AND the rewrites. Tested against the production
    BM25 tokenizer."""
    from core.src.query.bm25_index import tokenize

    expanded = expand_query(
        "What requirements mention cause code 22?",
        ["EMM cause 22 ATTACH REJECT", "TAU REJECT cause code 22"],
    )
    tokens = set(tokenize(expanded))

    # Original-query tokens
    assert "what" in tokens
    assert "requirements" in tokens
    # Specific terms from rewrites that the original didn't contain
    assert "emm" in tokens
    assert "attach" in tokens
    assert "reject" in tokens
    assert "tau" in tokens
    # Numeric token shared across all three
    assert "22" in tokens
