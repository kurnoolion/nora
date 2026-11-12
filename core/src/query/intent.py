"""Rules intent classifier (strand sira-intent, design §3).

The baseline arm for the answer-intent dimension, and the fallback when the
decision model's confidence is below the floor. Imports only the schema, so
the rules analyzer stays light.

The ``change`` rule is ``is_release_change_question``, moved here from
``release_diff`` (which re-exports it). It leaves out verdict asks and
negated changes (D-DRAFT-9), so it routes fewer questions to History than
``main`` did. ``release_diff_rule`` keeps ``main``'s rule verbatim; History
routes by it while every intent switch is off.
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
# A pasted bug report asking for a verdict ("is this bug valid?", "expected X
# but Y") is a compliance question, whatever else it mentions.
_VERDICT_ASK = re.compile(
    r"\b(is|was) (this|that|it) (a )?(real |valid )?(bug|issue|defect|problem)\b"
    r"|\b(bug|issue|defect|ticket) (is )?(valid|invalid|legit\w*)\b"
    r"|\breal (issue|bug|problem)\b"
    r"|\bcomment on (this|the|that) (bug|issue|defect|ticket|report|problem)\b"
    r"|\bis (this|that|it) (true|false|correct|right|valid|expected|ok|okay)\b"
    r"|\bare we (wrong|right|ok|okay|correct|fine|at fault)\b"
    r"|\bexpect(ed|s|ing)?\b[^.?!]*\b(but|instead)\b")
# "nothing changed", "hasn't changed", "unchanged": no change is asked about.
_NEGATED_CHANGE = re.compile(
    r"\b(nothing|no|not|never|hasn't|haven't|didn't|hadn't|wasn't|isn't)\s+(\w+\s+)?chang\w*"
    r"|\bunchang\w*")


def release_diff_rule(question: str, intent: QueryIntent) -> bool:
    """``main``'s History routing rule, unchanged: a release-diff intent,
    or a one-MNO comparison phrased as a change between releases. Two or
    more MNOs is a comparison, never a release diff."""
    if intent.query_type == QueryType.RELEASE_DIFF:
        return True
    if len(intent.mnos) >= 2:
        return False
    q = (question or "").lower()
    return bool(_CHANGE_WORDS.search(q) and (_RELEASE_WORDS.search(q) or len(intent.releases) >= 1))


def is_release_change_question(question: str, intent: QueryIntent) -> bool:
    """A release-diff intent, or a one-MNO comparison phrased as a change
    between releases ("… compared to the previous one"), which the rules
    analyzer files under cross-MNO comparison because of "compare".
    Two or more MNOs is a comparison, never a release diff. A bug report
    asking for a verdict is never one, and a negated change ("nothing
    changed") is not a change asked about."""
    q = (question or "").lower()
    if _VERDICT_ASK.search(q):
        return False
    cleaned = _NEGATED_CHANGE.sub(" ", q)
    if intent.query_type == QueryType.RELEASE_DIFF:
        # The rules analyzer files any "changed" (even "nothing changed") here.
        negated_only = (_NEGATED_CHANGE.search(q) and len(intent.releases) < 2
                        and not any(w in cleaned for w in ("delta", "diff", "changed")))
        return not negated_only
    if len(intent.mnos) >= 2:
        return False
    return bool(_CHANGE_WORDS.search(cleaned)
                and (_RELEASE_WORDS.search(cleaned) or len(intent.releases) >= 1))


def classify_intent(question: str, qi: QueryIntent) -> Intent:
    """The rules intent for ``question``, given the rest of its rules analysis.

    First match wins: change, a bug report asking for a verdict (compliance),
    compare, compliance, list, fact, explain, then ``OTHER``. ``change``
    comes first so it agrees with ``is_release_change_question`` exactly.
    """
    q = (question or "").lower()
    if is_release_change_question(question, qi):
        return Intent.CHANGE
    if _VERDICT_ASK.search(q):
        return Intent.COMPLIANCE
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
