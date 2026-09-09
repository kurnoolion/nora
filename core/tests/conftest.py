"""Shared pytest fixtures.

Currently one job: keep `NORA_LLM_CONFIG` out of the test environment.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_llm_config_env(monkeypatch):
    """Clear `NORA_LLM_CONFIG` for every test.

    The var was added by strand llm-roster-deploy (#18) so a deployment can
    point NORA at an llm.json outside the repo. It takes precedence over
    `DEFAULT_LLM_CONFIG_PATH` — which is the whole point — but that silently
    defeats the many tests that isolate themselves by monkeypatching
    `DEFAULT_LLM_CONFIG_PATH` to a temp file: with the var set, `load()` reads
    the developer's real roster instead of the fixture. Nine tests in
    `test_env_config.py` fail that way, and only on machines where someone has
    configured a roster, which is the worst shape for a test failure — it
    passes in CI and for everyone who has not adopted the feature yet.

    The other `NORA_LLM_*` vars need no clearing here; the resolver tests that
    care already delete them individually, because those vars predate this
    problem and the tests were written against them.

    Tests that WANT the var set it themselves with `monkeypatch.setenv`, which
    runs after this fixture and wins.
    """
    monkeypatch.delenv("NORA_LLM_CONFIG", raising=False)
