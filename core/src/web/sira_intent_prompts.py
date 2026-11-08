"""Per-intent select-synth system prompts (strand sira-intent, design §5, D-DRAFT-6).

Two layers:

- **Defaults, here in source.** Every prompt shares today's skeleton —
  1. select by meaning, 2. answer, 3. cite exact req_ids — and only step 2,
  the answer shape, changes per intent. ``other`` is today's prompt verbatim.
- **An optional override per environment:** a JSON object
  ``{"<intent>": "<full system prompt>"}`` in the file named by
  ``NORA_SIRA_INTENT_PROMPTS_FILE``, else ``<env_dir>/sira_intent_prompts.json``.
  Merged per intent over the defaults; the value replaces the whole prompt.

Loaded once at startup (``load_at_startup``). A malformed file, a
non-string or empty value, an unknown intent, or a missing file named by
the variable raises ``ValueError``, which stops nora-web. Each prompt
carries ``source`` (``default`` | ``override``) and ``hash`` (8 hex of the
SHA-256 of its text, before any reasoning-sentinel suffix).

``NORA_SIRA_INTENT_PROMPTS`` (``routes/sira_intent.py``) decides whether
answers use them; this module only holds and loads them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from core.src.query.schema import Intent

logger = logging.getLogger(__name__)

FILE_ENV = "NORA_SIRA_INTENT_PROMPTS_FILE"
ENV_DIR_FILE = "sira_intent_prompts.json"

_PREFIX = (
    "You are an expert telecom (3GPP/GSMA) device-requirements analyst. Below "
    "are candidate requirement chunks retrieved for the user's question, GROUPED "
    "by operator (MNO) and release. Retrieval is recall-oriented, so MANY chunks "
    "are NOT relevant.\n\n"
    "Work in order:\n"
    "1. SELECT only the chunks that actually answer the question. Judge by "
    "MEANING, not keyword overlap — telecom terminology varies: e.g. 'SA NR', "
    "'5G NR standalone' and '5G SA' mean the same thing; 'NR' is the 5G air "
    "interface; a band may appear as 'n78', 'NR band 78' or 'B78'. A chunk that "
    "uses different wording can still be the source of truth.\n"
)
_SUFFIX = (
    "3. CITE every requirement you rely on by writing its exact req_id (shown as "
    "'req_id: <ID>' in each chunk header) inline in your answer. Do not cite "
    "chunks you judged irrelevant. If the selected chunks don't answer the "
    "question, say so plainly rather than guessing."
)

# Step 2 per intent: the answer shape (design §2 table).
_ANSWER_STEP = {
    Intent.OTHER: (
        "2. ANSWER from the selected chunks only. When the question spans multiple "
        "operators, compare/contrast them explicitly; use a table when it helps.\n"
    ),
    Intent.FACT: (
        "2. ANSWER from the selected chunks only, directly and briefly: lead with "
        "the value or statement asked for in one or two sentences, then at most a "
        "short note on conditions or exceptions the chunks state. Add no background "
        "the question didn't ask for. When operators differ, give each operator's "
        "value on its own line.\n"
    ),
    Intent.EXPLAIN: (
        "2. ANSWER from the selected chunks only, as clear prose: first what the "
        "behaviour is, then how it works (or why, when asked), in the order the "
        "requirements describe it. Use short paragraphs or numbered steps, and keep "
        "to what the chunks say. When operators differ, say how.\n"
    ),
    Intent.LIST: (
        "2. ANSWER from the selected chunks only, as an EXHAUSTIVE bulleted list: "
        "one bullet per relevant requirement, giving its req_id and one sentence "
        "summarizing what that requirement says — its content, not just its section "
        "title. Include every selected chunk that fits — do not stop at a few "
        "examples. Group the bullets by operator, or by topic when there is one "
        "operator, and end with the number of requirements listed.\n"
    ),
    Intent.COMPLIANCE: (
        "2. ANSWER from the selected chunks only, as a compliance check of the "
        "behaviour the user describes: one line per relevant requirement giving "
        "its req_id, what it requires, and a verdict — MEETS, VIOLATES, or UNCLEAR "
        "when the description lacks the detail needed. When the user pastes a bug "
        "report with an observed and an expected behaviour, judge both: whether a "
        "requirement backs the expected behaviour, and whether the observed one "
        "MEETS or VIOLATES it; when no requirement backs the expected behaviour, "
        "say so — the report asks for more than the requirements do. End with an "
        "overall verdict and its main reason. Do not assume details the user "
        "didn't give.\n"
    ),
    Intent.CHANGE: (
        "2. ANSWER from the selected chunks only, about what changed between "
        "releases: group the findings as Added, Removed and Changed (old → new), "
        "naming the release each side comes from (the RELEASE in each group "
        "header). Report only changes you can see in chunks from two releases; if "
        "the chunks cover a single release, say that the release history isn't "
        "available here and answer for the release you have.\n"
    ),
    Intent.COMPARE: (
        "2. ANSWER from the selected chunks only, as a side-by-side comparison: a "
        "table with one row per aspect and one column per thing compared "
        "(operator, plan, feature or standard), then a short paragraph on the key "
        "differences and similarities. Write 'not specified' where the chunks are "
        "silent rather than guessing.\n"
    ),
}

DEFAULT_PROMPTS: dict[Intent, str] = {i: _PREFIX + _ANSWER_STEP[i] + _SUFFIX for i in Intent}


@dataclass(frozen=True)
class IntentPrompt:
    intent: Intent
    text: str
    source: str        # "default" | "override"
    hash: str          # 8 hex of sha256(text)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def _override_path(env_dir: "Path | None", env: "dict | None") -> "Path | None":
    env = os.environ if env is None else env
    named = (env.get(FILE_ENV) or "").strip()
    if named:
        p = Path(named)
        if not p.is_file():
            raise ValueError(f"{FILE_ENV}={named}: file not found")
        return p
    if env_dir is not None:
        p = Path(env_dir) / ENV_DIR_FILE
        if p.is_file():
            return p
    return None


def load_prompts(env_dir: "Path | None" = None, env: "dict | None" = None
                 ) -> tuple[dict[Intent, IntentPrompt], "Path | None"]:
    """The merged prompt set and the override file used (None: defaults only).

    Raises:
        ValueError: The override file is missing (when named), not a JSON
            object, names an unknown intent, or has a non-string or empty value.
    """
    path = _override_path(env_dir, env)
    overrides: dict[Intent, str] = {}
    if path is not None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}: not valid JSON ({exc.msg}, line {exc.lineno})") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: expected a JSON object keyed by intent")
        known = {i.value for i in Intent}
        for key, text in raw.items():
            if key not in known:
                raise ValueError(f"{path}: unknown intent {key!r}; known: {', '.join(sorted(known))}")
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"{path}: the prompt for {key!r} must be a non-empty string")
            overrides[Intent(key)] = text
    prompts = {
        i: IntentPrompt(i, overrides.get(i, DEFAULT_PROMPTS[i]),
                        "override" if i in overrides else "default",
                        _hash(overrides.get(i, DEFAULT_PROMPTS[i])))
        for i in Intent
    }
    return prompts, path


_LOADED: "dict[Intent, IntentPrompt] | None" = None


def load_at_startup(env_dir: "Path | None") -> None:
    """Load once and log each intent's source and hash. Raises on a bad file."""
    global _LOADED
    prompts, path = load_prompts(env_dir)
    _LOADED = prompts
    logger.info("SIRA intent prompts: %s; %s", f"override {path}" if path else "defaults only",
                " ".join(f"{p.intent.value}={p.source}:{p.hash}" for p in prompts.values()))


def prompt_for(intent: Intent) -> IntentPrompt:
    """The loaded prompt for ``intent`` (the defaults before startup has run)."""
    if _LOADED is not None:
        return _LOADED[intent]
    text = DEFAULT_PROMPTS[intent]
    return IntentPrompt(intent, text, "default", _hash(text))
