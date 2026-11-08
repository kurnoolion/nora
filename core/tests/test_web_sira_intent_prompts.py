"""Per-intent select-synth prompts (strand sira-intent, design §5, D-DRAFT-6).

Covers:
- The defaults: `other` is today's prompt (hash-locked), every prompt keeps
  the select/answer/cite skeleton and only step 2 differs.
- The loader: no override, the env-dir file, the variable beating it, a
  partial override merged per intent, every loud failure, the hashes.
- Startup load and `prompt_for`.
- The select-synth lane: the switch on uses the intent's prompt (plus the
  sentinel suffix) and stamps lane_config; off keeps today's prompt.
"""

from __future__ import annotations

import asyncio
import hashlib
import json

import pytest

from core.src.query.schema import Intent
from core.src.web import sira_intent_prompts as sp

TODAY_HASH = "78cc7705"   # sha256[:8] of the select-synth prompt before this strand


def _h(text):
    return hashlib.sha256(text.encode()).hexdigest()[:8]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(sp.FILE_ENV, raising=False)
    monkeypatch.setattr(sp, "_LOADED", None)


# ── defaults ──────────────────────────────────────────────────────────

def test_other_is_todays_prompt_verbatim():
    assert _h(sp.DEFAULT_PROMPTS[Intent.OTHER]) == TODAY_HASH


def test_every_prompt_keeps_the_skeleton_and_varies_only_step_two():
    texts = sp.DEFAULT_PROMPTS
    assert set(texts) == set(Intent)
    for t in texts.values():
        assert t.startswith(sp._PREFIX) and t.endswith(sp._SUFFIX)
        assert t[len(sp._PREFIX):].startswith("2. ANSWER from the selected chunks only")
    assert len({t for t in texts.values()}) == len(Intent)


# ── loader ────────────────────────────────────────────────────────────

def _write(path, payload):
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return path


def test_no_override_means_defaults(tmp_path):
    prompts, path = sp.load_prompts(tmp_path, env={})
    assert path is None
    assert all(p.source == "default" and p.text == sp.DEFAULT_PROMPTS[i] and p.hash == _h(p.text)
               for i, p in prompts.items())


def test_env_dir_file_is_merged_per_intent(tmp_path):
    f = _write(tmp_path / sp.ENV_DIR_FILE, {"list": "LIST PROMPT", "compare": "CMP"})
    prompts, path = sp.load_prompts(tmp_path, env={})
    assert path == f
    assert prompts[Intent.LIST].text == "LIST PROMPT" and prompts[Intent.LIST].source == "override"
    assert prompts[Intent.LIST].hash == _h("LIST PROMPT")
    assert prompts[Intent.FACT].source == "default"


def test_the_variable_beats_the_env_dir_file(tmp_path):
    _write(tmp_path / sp.ENV_DIR_FILE, {"fact": "FROM ENV DIR"})
    named = _write(tmp_path / "elsewhere.json", {"fact": "FROM VARIABLE"})
    prompts, path = sp.load_prompts(tmp_path, env={sp.FILE_ENV: str(named)})
    assert path == named and prompts[Intent.FACT].text == "FROM VARIABLE"


@pytest.mark.parametrize("payload, match", [
    ("{not json", "not valid JSON"),
    ('["fact"]', "JSON object"),
    ({"lookup": "x"}, "unknown intent 'lookup'"),
    ({"fact": ""}, "non-empty string"),
    ({"fact": 3}, "non-empty string"),
])
def test_bad_files_fail_loudly(tmp_path, payload, match):
    _write(tmp_path / sp.ENV_DIR_FILE, payload)
    with pytest.raises(ValueError, match=match):
        sp.load_prompts(tmp_path, env={})


def test_a_named_file_that_does_not_exist_fails(tmp_path):
    with pytest.raises(ValueError, match="file not found"):
        sp.load_prompts(tmp_path, env={sp.FILE_ENV: str(tmp_path / "typo.json")})


def test_startup_load_feeds_prompt_for(tmp_path, monkeypatch, caplog):
    assert sp.prompt_for(Intent.LIST).source == "default"          # before startup
    _write(tmp_path / sp.ENV_DIR_FILE, {"list": "LIST PROMPT"})
    with caplog.at_level("INFO"):
        sp.load_at_startup(tmp_path)
    assert sp.prompt_for(Intent.LIST).text == "LIST PROMPT"
    assert f"list=override:{_h('LIST PROMPT')}" in caplog.text and f"other=default:{TODAY_HASH}" in caplog.text


# ── the select-synth lane ─────────────────────────────────────────────

@pytest.fixture()
def lane(monkeypatch):
    from core.src.web.routes import playground, query

    seen = {}

    class LLM:
        model = "m"

        def complete(self, prompt, system="", temperature=0.0, max_tokens=0):
            seen["system"] = system
            return "answer citing VZ_REQ_PLANA_0001"

    async def store(*a, **k):
        return {"results": [{"req_id": "VZ_REQ_PLANA_0001", "mno": "mno-w", "release": "Sep2031",
                             "text": "placeholder"}]}

    async def snap():
        return {}

    monkeypatch.setattr(query, "_build_llm_from_env_or_default", lambda **k: LLM())
    monkeypatch.setattr(playground, "_call_store_query", store)
    monkeypatch.setattr(playground, "_snapshot_sira_lane_config", snap)

    async def say(msg):
        pass

    def run(intent):
        return asyncio.run(playground._run_select_synth_lane("q", say, 0.0, intent=intent))

    return run, seen, playground


def test_switch_on_uses_the_intent_prompt_and_stamps_lane_config(lane, monkeypatch):
    run, seen, playground = lane
    monkeypatch.setenv("NORA_SIRA_INTENT_PROMPTS", "on")
    out = run(Intent.LIST)
    assert seen["system"] == sp.DEFAULT_PROMPTS[Intent.LIST] + playground._SENTINEL_SUFFIX
    lc = out["lane_config"]
    assert lc["prompt_intent"] == "list" and lc["prompt_source"] == "default"
    assert lc["prompt_hash"] == _h(sp.DEFAULT_PROMPTS[Intent.LIST])
    assert out["result"]["llm_system_prompt"] == seen["system"]


def test_switch_off_keeps_todays_prompt(lane, monkeypatch):
    run, seen, playground = lane
    monkeypatch.delenv("NORA_SIRA_INTENT_PROMPTS", raising=False)
    out = run(Intent.LIST)
    assert seen["system"] == playground._SELECT_SYNTH_SYSTEM_PROMPT
    assert "prompt_hash" not in out["lane_config"]


def test_no_intent_keeps_todays_prompt_even_when_on(lane, monkeypatch):
    run, seen, playground = lane
    monkeypatch.setenv("NORA_SIRA_INTENT_PROMPTS", "on")
    run(None)
    assert seen["system"] == playground._SELECT_SYNTH_SYSTEM_PROMPT
