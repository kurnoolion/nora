"""`/v1/systemone` HTTP decision provider.

Stdlib `urllib` only, no SDK — the same posture that kept
`OpenAICompatibleProvider` vendor-neutral behind `/v1/chat/completions`.

**The request and response shapes below are UNVERIFIED against a live
server.** No decision-model endpoint was reachable when this was written, so
the payload is built from the wire format as documented rather than as
observed. The parse therefore fails loudly and prints the keys the server
actually sent (`DecisionError`), so the first real run names exactly what to
correct instead of silently mapping a field to the wrong place. Treat a
`DecisionError` naming unexpected keys as a schema correction, not a bug in
the server.

Assumed request::

    POST {base_url}/systemone
    {"model": "<tag>",            # omitted when empty
     "state": "<shared context>",
     "questions": [{"id": "query_type", "type": "choice",
                    "question": "...", "options": ["fact", ...]}]}

Assumed response::

    {"answers": [{"id": "query_type", "value": "fact",
                  "confidence": 0.91}]}
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

        payload: dict = {
            "state": state,
            "questions": [
                {
                    "id": q.question_id,
                    "type": q.kind,
                    "question": q.question,
                    **({"options": list(q.options)} if q.options else {}),
                }
                for q in questions
            ],
        }
        if self.model:
            payload["model"] = self.model

        request = urllib.request.Request(
            f"{self.base_url}/systemone",
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
                "decision response has no 'answers' key — the wire schema "
                f"in this module is unverified; server sent keys: "
                f"{sorted(data) if isinstance(data, dict) else type(data).__name__}"
            )

        answers = []
        for raw in data["answers"]:
            missing = [k for k in ("id", "value") if k not in raw]
            if missing:
                raise DecisionError(
                    f"decision answer is missing {missing} — the wire "
                    f"schema in this module is unverified; server sent "
                    f"keys: {sorted(raw)}"
                )
            answers.append(
                DecisionAnswer(
                    question_id=raw["id"],
                    value=raw["value"],
                    confidence=float(raw.get("confidence", 0.0)),
                )
            )
        return answers
