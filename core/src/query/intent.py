"""Rules intent classifier (strand sira-intent, design §3).

The baseline arm for the answer-intent dimension, and the fallback when the
decision model's confidence is below the floor. Imports only the schema, so
the rules analyzer stays light.

The ``change`` rule is ``is_release_change_question``, moved here from
``release_diff`` (which re-exports it) so routing on the rules arm stays
identical to today.
"""

from __future__ import annotations

import re

from core.src.query.schema import Intent, QueryIntent, QueryType

_CHANGE_WORDS = re.compile(r"\b(chang\w*|differ\w*|diff|delta|updated|new in|added|removed|dropped)\b")
_RELEASE_WORDS = re.compile(r"\b(release\w*|latest|previous|prior|newest|current version|version\w*)\b")

_COMPARE_WORDS = re.compile(r"\b(compar\w*|versus|vs\.?|differ\w*|difference between)\b")
_COMPLIANCE_WORDS = re.compile(
    r"\b(compl(y|ies|iant|iance)|conform\w*|violat\w*|satisf(y|ies)|"
    r"meets? the requirements?)\b")
_LIST_WORDS = re.compile(
    r"^\s*(list|enumerate)\b|\b(what are all|which requirements|all (the )?requirements|all reqs)\b"
    r"|\bwhat are the\b[^?.]*\brequirements?\b|\bwhat requirements\b")
_EXPLAIN_WORDS = re.compile(r"\b(how does|how do|why|purpose of)\b")


def is_release_change_question(question: str, intent: QueryIntent) -> bool:
    """A release-diff intent, or a one-MNO comparison phrased as a change
    between releases ("… compared to the previous one"), which the rules
    analyzer files under cross-MNO comparison because of "compare".
    Two or more MNOs is a comparison, never a release diff."""
    if intent.query_type == QueryType.RELEASE_DIFF:
        return True
    if len(intent.mnos) >= 2:
        return False
    q = (question or "").lower()
    return bool(_CHANGE_WORDS.search(q) and (_RELEASE_WORDS.search(q) or len(intent.releases) >= 1))


def classify_intent(question: str, qi: QueryIntent) -> Intent:
    """The rules intent for ``question``, given the rest of its rules analysis.

    First match wins: change, compare, compliance, list, fact, explain,
    then ``OTHER``. ``change`` comes first so it agrees with
    ``is_release_change_question`` exactly.
    """
    q = (question or "").lower()
    if is_release_change_question(question, qi):
        return Intent.CHANGE
    if qi.query_type in (QueryType.CROSS_MNO_COMPARISON, QueryType.STANDARDS_COMPARISON) \
            or _COMPARE_WORDS.search(q):
        return Intent.COMPARE
    if _COMPLIANCE_WORDS.search(q):
        return Intent.COMPLIANCE
    if _LIST_WORDS.search(q):
        return Intent.LIST
    if qi.query_type == QueryType.FACT:
        return Intent.FACT
    if qi.query_type == QueryType.SUMMARIZE or _EXPLAIN_WORDS.search(q):
        return Intent.EXPLAIN
    return Intent.OTHER
