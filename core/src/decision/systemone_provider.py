"""`/v1/systemone` HTTP decision provider.

Stdlib `urllib` only, no SDK — the same posture that kept
`OpenAICompatibleProvider` vendor-neutral behind `/v1/chat/completions`.

Shapes **verified** against the reference server's own request and response
models — `kev/api.py` at jaredpalmer/kev `1f04cfa` — which implements
TypeSafe's public System One API. The first draft of this module guessed four
things wrong, each of which a server rejects or mis-parses:

===========================  ==========================  ====================
Guessed                      Actual                      Consequence
===========================  ==========================  ====================
``questions`` is a list      an object keyed by id       422
``question``                 ``instructions``            422
``options`` (list)           ``criteria`` (dict/list)    422
``answers`` is a list        an object keyed by id       mis-parse
answer holds ``value``       holds ``choice`` /          mis-parse
                             ``noul`` / ``score``
===========================  ==========================  ====================

Request::

    POST {base_url}/v1/systemone
    {"model": "kev-latest",       # omitted when empty
     "state": "<shared context>",
     "questions": {"query_type": {"type": "choice",
                                  "instructions": "...",
                                  "criteria": {"fact": null, ...}}}}

A ``criteria`` value is an optional per-option description; ``null`` means the
option is presented by name alone. A ``score`` question's ``criteria`` is an
ordered list of level descriptions instead, and a ``noul`` question's two
options are implicit.

Response::

    {"model": "kev-latest",
     "answers": {"query_type": {"type": "choice", "choice": "fact",
                                "confidence": 0.91,
                                "probabilities": {"fact": 0.91, ...}}},
     "usage": {"input_tokens": 118, "output_tokens": 31},
     "latency_ms": 94.2}

A ``noul`` answer carries no ``confidence`` — its ``noul`` field is already
the probability of yes — so `confidence_is_calibrated` is uninformative for a
noul-only response.

The parse still fails loudly and names the keys the server actually sent, so a
future schema change is a correction with a diagnosis rather than a silent
mis-mapping.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Sequence

from core.src.decision.base import (
    DecisionAnswer,
    DecisionError,
    DecisionQuestion,
)

logger = logging.getLogger(__name__)

VALUE_KEY_BY_KIND: dict[str, str] = {
    "choice": "choice",
    "noul": "noul",
    "score": "score",
}
"""Where each answer type keeps its value. The wire format names the field
after the type rather than using a single `value` key, so the type has to be
read before the value can be."""


class SystemOneDecisionProvider:
    """Decision provider over an in-corp `/v1/systemone` endpoint.

    No egress: `base_url` must name a host inside the corporate network
    (design §7). This class does not enforce that — it cannot know the
    network topology — but it is the invariant the operator is holding.
    """

    def __init__(
        self,
        base_url: str,
        model: str = "",
        api_key: str = "",
        timeout: int = 30,
    ) -> None:
        if not base_url:
            raise DecisionError(
                "SystemOneDecisionProvider needs a base_url "
                "(NORA_DECISION_BASE_URL)"
            )
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self._timeout = timeout
        self.call_count = 0
        self.last_latency_ms = 0.0
        self.last_server_latency_ms = 0.0
        """Latency the server reports for itself, against `last_latency_ms`
        measured at this client. The difference is network, which is what
        tells a slow model from a slow hop."""

    @property
    def _endpoint(self) -> str:
        """The `/v1/systemone` URL, whether or not `base_url` already ends
        in `/v1`. Both spellings are in circulation — an OpenAI-compatible
        `base_url` conventionally carries `/v1`, while the reference
        server's own example passes the bare origin — and posting to the
        wrong one is a 404 that looks like an outage."""
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/systemone"
        return f"{self.base_url}/v1/systemone"

    def decide(
        self, state: str, questions: Sequence[DecisionQuestion]
    ) -> list[DecisionAnswer]:
        """Answer every question against one shared state in one request.

        Raises:
            DecisionError: Transport failure, non-JSON response, or a
                response whose shape does not match the assumed schema.
                Callers degrade rather than fail — `HybridQueryAnalyzer`
                falls back to rule-based classification.
        """
        import time

        asked: dict = {}
        for q in questions:
            entry: dict = {"type": q.kind, "instructions": q.question}
            if q.kind == "choice":
                # dict, not list: the value is an optional per-option
                # description and None presents the option by name alone.
                glosses = q.descriptions or (None,) * len(q.options)
                entry["criteria"] = dict(zip(q.options, glosses))
            elif q.kind == "score":
                entry["criteria"] = list(q.options)
            asked[q.question_id] = entry

        payload: dict = {"state": state, "questions": asked}
        if self.model:
            payload["model"] = self.model

        request = urllib.request.Request(
            self._endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                **(
                    {"Authorization": f"Bearer {self._api_key}"}
                    if self._api_key
                    else {}
                ),
            },
            method="POST",
        )

        self.call_count += 1
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(
                request, timeout=self._timeout
            ) as response:
                body = response.read().decode("utf-8")
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise DecisionError(
                f"decision request to {self.base_url} failed: {e}"
            ) from e
        finally:
            self.last_latency_ms = (time.perf_counter() - started) * 1000.0

        return self._parse(body)

    def _parse(self, body: str) -> list[DecisionAnswer]:
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            raise DecisionError(
                f"decision response from {self.base_url} is not JSON: {e}"
            ) from e

        if not isinstance(data, dict) or "answers" not in data:
            raise DecisionError(
                "decision response has no 'answers' key; server sent keys: "
                f"{sorted(data) if isinstance(data, dict) else type(data).__name__}"
            )

        asked = data["answers"]
        if not isinstance(asked, dict):
            raise DecisionError(
                "decision response 'answers' should be an object keyed by "
                f"question id, got {type(asked).__name__}"
            )

        self.last_server_latency_ms = float(data.get("latency_ms") or 0.0)

        answers = []
        for question_id, raw in asked.items():
            kind = raw.get("type")
            value_key = VALUE_KEY_BY_KIND.get(kind)
            if value_key is None:
                raise DecisionError(
                    f"decision answer {question_id!r} has unknown type "
                    f"{kind!r}; expected one of "
                    f"{sorted(VALUE_KEY_BY_KIND)}. Server sent keys: "
                    f"{sorted(raw)}"
                )
            if value_key not in raw:
                raise DecisionError(
                    f"decision answer {question_id!r} is a {kind} but has "
                    f"no {value_key!r} field; server sent keys: "
                    f"{sorted(raw)}"
                )
            answers.append(
                DecisionAnswer(
                    question_id=question_id,
                    value=raw[value_key],
                    confidence=float(raw.get("confidence", 0.0)),
                )
            )
        return answers
