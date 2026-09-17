# Ask-page model discovery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the Ask page choose a model within a roster provider, from a list discovered via the provider's `GET /v1/models`.

**Architecture:** `env` gains one TTL resolver; `llm` gains a stateless `list_models()` HTTP call; a new focused `web/model_discovery.py` owns the in-process cache (fetch-on-stale, last-good-on-failure, default always present); a new `/api/test/providers/{id}/models` route serves it to the page; the Ask form posts a `model` field that is threaded beside `mode` to `_build_llm_from_env_or_default`, which honours it only if the cache lists it.

**Tech Stack:** Python 3.11, FastAPI, Jinja2 + vanilla JS (no build), stdlib `urllib`, pytest. Run tests with `.venv/bin/python -m pytest`.

**Spec:** `docs/compact/strands/llm-model-discovery/plan.md`

## Global Constraints

- Branch `llm-model-discovery`. Never commit on `main`. Do not push; do not open a PR.
- Karpathy guidelines: minimum code; surgical diffs; every changed line traces to the spec. Do not tidy adjacent code.
- TDD: failing test first for every code task (Task 6 is template/JS — verified in the browser instead).
- Default TTL: `21600` seconds. Top-level `llm.json` key `model_discovery_ttl_s`. No env-var or CLI tier.
- Entry `model` is the default model; existing rosters load unchanged; no `LLMProviderEntry` field is added.
- Discovery calls `GET {base_url}/models` (roster `base_url` already ends in `/v1`), Bearer header only when a key is set.
- Fast/Think stays per provider. The fallback provider always uses its own entry `model`.
- The answer path never triggers a discovery fetch — it only reads the cache.
- No proprietary content (real endpoint URLs, model names from the internal roster, keys) in code, tests, logs or commits. Use `*.invalid` hosts and fictional model names.
- Out of scope: eval/golden_cli, per-model reasoning overrides, refresh button, Config page, Ollama `/api/tags`, `LLMProvider` Protocol.
- Known pre-existing gap, NOT fixed here: the disambiguation `synthesize-group` form (`templates/test/_answer.html:52`) posts no `provider`/`mode`, so it uses the default entry; it will not carry `model` either.

## File map

| File | Change | Responsibility |
|---|---|---|
| `core/src/env/config.py` | modify | `LLMConfigFile.model_discovery_ttl_s` + `resolve_model_discovery_ttl_s()` |
| `config/llm.json` | modify | `_comment` names the new key |
| `core/src/llm/openai_provider.py` | modify | `list_models(base_url, api_key, timeout)` |
| `core/src/web/model_discovery.py` | create | cache: `models_for(entry)`, `allowed_models(entry)`, `_reset_cache()` |
| `core/src/web/routes/playground.py` | modify | route; `_form_model`; thread `model` through every Ask path |
| `core/src/web/routes/query.py` | modify | `_build_llm_from_env_or_default(model=)`; `_run_query_sync(model=)` |
| `core/src/web/templates/test/index.html` | modify | Model select + JS |
| `core/tests/test_env_config.py` | modify | TTL tests |
| `core/tests/test_openai_provider.py` | modify | `list_models` tests |
| `core/tests/test_model_discovery.py` | create | cache + route tests |
| `core/tests/test_ask_reasoning.py` | modify | `_form_model` + model selection on the build path |
| `core/tests/test_team_mode.py` | modify | route allowed under the gate |
| `core/src/{env,llm,web}/MODULE.md` | modify | contracts |
| `docker/README.md` | modify | operator note beside `NORA_LLM_CONFIG` |

---

### Task 1: TTL setting in `env`

**Files:**
- Modify: `core/src/env/config.py` (`LLMConfigFile` dataclass ~line 250, `LLMConfigFile.load` ~line 305, resolvers after `resolve_fallback_provider` ~line 915)
- Modify: `config/llm.json` (`_comment`)
- Test: `core/tests/test_env_config.py` (append)

**Interfaces:**
- Produces: `DEFAULT_MODEL_DISCOVERY_TTL_S: int = 21600`; `LLMConfigFile.model_discovery_ttl_s: int`; `resolve_model_discovery_ttl_s() -> int`

- [ ] **Step 1: Write the failing tests** — append to `core/tests/test_env_config.py`:

```python
def _load_llm_json(tmp_path, data):
    import json
    from core.src.env import config as env_cfg
    path = tmp_path / "llm.json"
    path.write_text(json.dumps(data))
    env_cfg._LLM_CONFIG_CACHE = env_cfg.LLMConfigFile.load(path)
    return env_cfg


def test_model_discovery_ttl_defaults_to_six_hours(tmp_path):
    env_cfg = _load_llm_json(tmp_path, {})
    try:
        assert env_cfg.resolve_model_discovery_ttl_s() == 21600
        assert env_cfg.DEFAULT_MODEL_DISCOVERY_TTL_S == 21600
    finally:
        env_cfg._reset_llm_config_cache()


def test_model_discovery_ttl_explicit_value(tmp_path):
    env_cfg = _load_llm_json(tmp_path, {"model_discovery_ttl_s": 600})
    try:
        assert env_cfg.resolve_model_discovery_ttl_s() == 600
    finally:
        env_cfg._reset_llm_config_cache()


def test_model_discovery_ttl_invalid_degrades_to_default(tmp_path, caplog):
    for bad in (-5, "soon", 0):
        env_cfg = _load_llm_json(tmp_path, {"model_discovery_ttl_s": bad})
        try:
            assert env_cfg.resolve_model_discovery_ttl_s() == 21600
        finally:
            env_cfg._reset_llm_config_cache()
    assert "model_discovery_ttl_s" in caplog.text   # -5 and "soon" warn


def test_model_discovery_ttl_leaves_roster_entries_unchanged(tmp_path):
    env_cfg = _load_llm_json(tmp_path, {
        "model_discovery_ttl_s": 60,
        "providers": [{"id": "a", "base_url": "http://a.invalid/v1", "model": "m1"}],
    })
    try:
        (entry,) = env_cfg.resolve_providers()
        assert entry.model == "m1"
    finally:
        env_cfg._reset_llm_config_cache()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest core/tests/test_env_config.py -k model_discovery_ttl -v`
Expected: FAIL — `AttributeError: ... has no attribute 'resolve_model_discovery_ttl_s'`

- [ ] **Step 3: Implement.** In `core/src/env/config.py`:

Next to the other module constants near `DEFAULT_LLM_CONFIG_PATH`:

```python
# How long a provider's discovered `/v1/models` list is trusted before the
# Ask page refetches it. Endpoints rarely change what they serve, so hours.
DEFAULT_MODEL_DISCOVERY_TTL_S: int = 21600
```

Add a parser beside `_validated_provider_id`:

```python
def _parse_ttl(raw, config_path) -> int:
    """`model_discovery_ttl_s`, or the default. Missing/0 is silent (unset);
    a negative or non-integer value warns — degrade, don't fail the load."""
    if raw in (None, "", 0):
        return DEFAULT_MODEL_DISCOVERY_TTL_S
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = -1
    if value <= 0:
        logger.warning("%s: model_discovery_ttl_s=%r is not a positive "
                       "integer — using %d", config_path, raw,
                       DEFAULT_MODEL_DISCOVERY_TTL_S)
        return DEFAULT_MODEL_DISCOVERY_TTL_S
    return value
```

In `LLMConfigFile`, after `fallback_provider: str = ""`:

```python
    # Seconds a discovered model list is cached (web/model_discovery.py).
    model_discovery_ttl_s: int = DEFAULT_MODEL_DISCOVERY_TTL_S
```

In `LLMConfigFile.load`, after the `fallback_provider=...` argument:

```python
            model_discovery_ttl_s=_parse_ttl(
                data.get("model_discovery_ttl_s"), config_path),
```

After `resolve_fallback_provider`:

```python
def resolve_model_discovery_ttl_s() -> int:
    """Seconds a provider's discovered model list stays fresh. File-only,
    like the roster it belongs to."""
    return _llm_config().model_discovery_ttl_s
```

In `config/llm.json`, append to the end of the `_comment` string (before the closing quote), keeping it one JSON string:

```
 Optional `model_discovery_ttl_s` (default 21600): how long the Ask page trusts a provider's discovered `/v1/models` list; each entry's `model` is that provider's default model.
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest core/tests/test_env_config.py core/tests/test_ask_reasoning.py -v`
Expected: all PASS (the second file proves existing roster parsing is unchanged).

- [ ] **Step 5: Commit**

```bash
git add core/src/env/config.py config/llm.json core/tests/test_env_config.py
git commit -m "env: model_discovery_ttl_s roster setting (default 6h)"
```

---

### Task 2: `list_models()` in `llm`

**Files:**
- Modify: `core/src/llm/openai_provider.py` (module-level function after the `OpenAICompatibleProvider` class)
- Test: `core/tests/test_openai_provider.py` (append; reuses its `_FakeResponse`)

**Interfaces:**
- Produces: `list_models(base_url: str, api_key: str = "", timeout: float = 10) -> list[str]` — raises `RuntimeError` on HTTP, network, or shape failure.

- [ ] **Step 1: Write the failing tests** — append:

```python
class TestListModels:
    def _capture(self, captured, body):
        def _fake(req, timeout=None):
            captured["url"] = req.full_url
            captured["auth"] = req.get_header("Authorization")
            captured["timeout"] = timeout
            return _FakeResponse(json.dumps(body).encode("utf-8"))
        return _fake

    def test_parses_openai_shape_in_server_order(self):
        from core.src.llm.openai_provider import list_models
        captured = {}
        body = {"data": [{"id": "model-b"}, {"id": "model-a"}]}
        with patch("urllib.request.urlopen", side_effect=self._capture(captured, body)):
            assert list_models("http://llm.invalid/v1/", "k") == ["model-b", "model-a"]
        assert captured["url"] == "http://llm.invalid/v1/models"
        assert captured["auth"] == "Bearer k"

    def test_no_key_sends_no_authorization(self):
        from core.src.llm.openai_provider import list_models
        captured = {}
        with patch("urllib.request.urlopen",
                   side_effect=self._capture(captured, {"data": [{"id": "m"}]})):
            list_models("http://llm.invalid/v1")
        assert captured["auth"] is None

    def test_http_error_raises(self):
        import urllib.error
        from core.src.llm.openai_provider import list_models
        err = urllib.error.HTTPError("http://llm.invalid/v1/models", 401,
                                     "Unauthorized", None, io.BytesIO(b"no"))
        with patch("urllib.request.urlopen", side_effect=err):
            with pytest.raises(RuntimeError, match="401"):
                list_models("http://llm.invalid/v1")

    def test_unexpected_shape_raises(self):
        from core.src.llm.openai_provider import list_models
        with patch("urllib.request.urlopen",
                   side_effect=self._capture({}, {"models": []})):
            with pytest.raises(RuntimeError, match="shape"):
                list_models("http://llm.invalid/v1")
```

(If `pytest` is not already imported at the top of the file, add `import pytest`.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest core/tests/test_openai_provider.py -k ListModels -v`
Expected: FAIL — `ImportError: cannot import name 'list_models'`

- [ ] **Step 3: Implement** — append to `core/src/llm/openai_provider.py`:

```python
def list_models(base_url: str, api_key: str = "", timeout: float = 10) -> list[str]:
    """Model ids an OpenAI-compatible endpoint serves (`GET {base_url}/models`).

    Returned in server order. Raises RuntimeError on any failure — whether a
    failure keeps a previous list is the caller's policy, not this function's.
    """
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/models", headers=headers, method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"models HTTP {e.code} {e.reason}") from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise RuntimeError(f"models request failed: {e}") from e
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise RuntimeError("models response has unexpected shape (no `data` list)")
    return [str(it["id"]) for it in items if isinstance(it, dict) and it.get("id")]
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest core/tests/test_openai_provider.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add core/src/llm/openai_provider.py core/tests/test_openai_provider.py
git commit -m "llm: list_models() for OpenAI-compatible /v1/models"
```

---

### Task 3: Discovery cache `web/model_discovery.py`

**Files:**
- Create: `core/src/web/model_discovery.py`
- Test: `core/tests/test_model_discovery.py` (create)

**Interfaces:**
- Consumes: `resolve_model_discovery_ttl_s()` (Task 1); `list_models(base_url, api_key, timeout)` (Task 2); `LLMProviderEntry` (`id`, `base_url`, `model`, `api_key`).
- Produces:
  - `models_for(entry) -> tuple[list[str], bool]` — `(models, discovered)`; fetches when stale; entry `model` always first-present; `discovered` is False when no successful fetch has ever happened for this entry.
  - `allowed_models(entry) -> list[str]` — cache only, never fetches; always contains `entry.model`.
  - `_reset_cache() -> None` — test hook.
  - `DISCOVERY_TIMEOUT_S = 10`.

- [ ] **Step 1: Write the failing tests** — create `core/tests/test_model_discovery.py`:

```python
"""Model discovery cache for the Ask page (strand llm-model-discovery).

No network: `list_models` is replaced with a scripted double, and time is
controlled through `model_discovery._now`.
"""

from __future__ import annotations

import threading
import time

import pytest

from core.src.env.config import LLMProviderEntry
from core.src.web import model_discovery as md


def _entry(model="default-model", pid="internal"):
    return LLMProviderEntry(id=pid, name="Internal", base_url="http://llm.invalid/v1",
                            model=model)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    md._reset_cache()
    monkeypatch.setattr(md, "resolve_model_discovery_ttl_s", lambda: 100)
    clock = {"t": 1000.0}
    monkeypatch.setattr(md, "_now", lambda: clock["t"])
    yield clock
    md._reset_cache()


def _script(monkeypatch, *results):
    """Each call pops the next result; an Exception instance is raised."""
    calls = []
    queue = list(results)

    def fake(base_url, api_key="", timeout=10):
        calls.append(base_url)
        r = queue.pop(0)
        if isinstance(r, Exception):
            raise r
        return list(r)

    monkeypatch.setattr(md, "list_models", fake)
    return calls


def test_first_call_fetches_and_includes_default(monkeypatch):
    calls = _script(monkeypatch, ["model-x", "default-model"])
    assert md.models_for(_entry()) == (["model-x", "default-model"], True)
    assert len(calls) == 1


def test_default_is_prepended_when_server_does_not_list_it(monkeypatch):
    _script(monkeypatch, ["model-x"])
    assert md.models_for(_entry()) == (["default-model", "model-x"], True)


def test_fresh_cache_is_not_refetched(monkeypatch, _clean):
    calls = _script(monkeypatch, ["model-x"])
    md.models_for(_entry())
    _clean["t"] += 99
    md.models_for(_entry())
    assert len(calls) == 1


def test_stale_cache_is_refetched(monkeypatch, _clean):
    calls = _script(monkeypatch, ["model-x"], ["model-y"])
    md.models_for(_entry())
    _clean["t"] += 101
    assert md.models_for(_entry()) == (["default-model", "model-y"], True)
    assert len(calls) == 2


def test_failed_refresh_keeps_last_good_list(monkeypatch, _clean, caplog):
    _script(monkeypatch, ["model-x"], RuntimeError("down"))
    md.models_for(_entry())
    _clean["t"] += 101
    assert md.models_for(_entry()) == (["default-model", "model-x"], True)
    assert "internal" in caplog.text


def test_failure_with_no_previous_list_is_default_only(monkeypatch):
    _script(monkeypatch, RuntimeError("down"))
    assert md.models_for(_entry()) == (["default-model"], False)


def test_failed_refresh_is_not_retried_until_stale_again(monkeypatch, _clean):
    calls = _script(monkeypatch, RuntimeError("down"))
    md.models_for(_entry())
    md.models_for(_entry())
    assert len(calls) == 1


def test_allowed_models_never_fetches(monkeypatch):
    calls = _script(monkeypatch)
    assert md.allowed_models(_entry()) == ["default-model"]
    assert calls == []


def test_allowed_models_reads_the_cache(monkeypatch):
    _script(monkeypatch, ["model-x"])
    md.models_for(_entry())
    assert md.allowed_models(_entry()) == ["default-model", "model-x"]


def test_concurrent_askers_trigger_one_fetch(monkeypatch):
    calls = []

    def slow(base_url, api_key="", timeout=10):
        calls.append(1)
        time.sleep(0.05)
        return ["model-x"]

    monkeypatch.setattr(md, "list_models", slow)
    threads = [threading.Thread(target=md.models_for, args=(_entry(),)) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest core/tests/test_model_discovery.py -v`
Expected: FAIL — `ImportError: cannot import name 'model_discovery'`

- [ ] **Step 3: Implement** — create `core/src/web/model_discovery.py`:

```python
"""Discovered model lists for roster providers (strand llm-model-discovery).

The Ask page offers every model a provider's `GET /v1/models` lists. Lists
are cached in-process per provider id for `model_discovery_ttl_s`:

  * fresh           -> served from cache;
  * stale / absent  -> refetched; a failed refetch keeps the last good list
                       (logged) so a blip does not shrink the dropdown;
  * always          -> the entry's configured `model` is present, because it
                       is the default and the only model known without a fetch.

A failed attempt still stamps the time, so a down endpoint is retried once
per TTL rather than on every page load. The answer path uses
`allowed_models()`, which never fetches.
"""

from __future__ import annotations

import logging
import threading
import time

from core.src.env.config import resolve_model_discovery_ttl_s
from core.src.llm.openai_provider import list_models

logger = logging.getLogger(__name__)

DISCOVERY_TIMEOUT_S = 10

# provider id -> (checked_at, models from the last SUCCESSFUL fetch or None)
_cache: dict[str, tuple[float, list[str] | None]] = {}
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _now() -> float:
    return time.monotonic()


def _with_default(entry, models: list[str] | None) -> list[str]:
    models = list(models or [])
    return models if entry.model in models else [entry.model, *models]


def _lock_for(provider_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(provider_id, threading.Lock())


def models_for(entry) -> tuple[list[str], bool]:
    """`(models, discovered)` for one roster entry, fetching when stale."""
    with _lock_for(entry.id):
        checked_at, models = _cache.get(entry.id, (None, None))
        stale = checked_at is None or _now() - checked_at >= resolve_model_discovery_ttl_s()
        if stale:
            try:
                models = list_models(entry.base_url, entry.api_key,
                                     timeout=DISCOVERY_TIMEOUT_S)
            except RuntimeError as e:
                logger.warning("Model discovery failed for provider %r: %s — %s",
                               entry.id, e,
                               "keeping last good list" if models else "default model only")
            _cache[entry.id] = (_now(), models)
        return _with_default(entry, models), models is not None


def allowed_models(entry) -> list[str]:
    """Models the answer path may use for `entry`. Cache only — never fetches."""
    _, models = _cache.get(entry.id, (None, None))
    return _with_default(entry, models)


def _reset_cache() -> None:
    """Test hook."""
    _cache.clear()
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest core/tests/test_model_discovery.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add core/src/web/model_discovery.py core/tests/test_model_discovery.py
git commit -m "web: cached per-provider model discovery"
```

---

### Task 4: Route `GET /api/test/providers/{provider_id}/models`

**Files:**
- Modify: `core/src/web/routes/playground.py` (new route next to `GET /api/test/ingested`, ~line 1134)
- Test: `core/tests/test_model_discovery.py` (append); `core/tests/test_team_mode.py` (`test_path_whitelist`)

**Interfaces:**
- Consumes: `models_for(entry)` (Task 3); `resolve_providers()`.
- Produces: JSON `{"models": list[str], "default": str, "discovered": bool}`; 404 `{"error": "unknown provider"}` for an id not in the roster (no default fallback — the page only asks for ids it rendered).

- [ ] **Step 1: Write the failing tests.** Append to `core/tests/test_model_discovery.py`:

```python
class TestModelsRoute:
    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        import json
        from fastapi.testclient import TestClient
        from core.src.env import config as cfg
        from core.src.web.app import app

        path = tmp_path / "llm.json"
        path.write_text(json.dumps({"providers": [
            {"id": "internal", "name": "Internal", "base_url": "http://llm.invalid/v1",
             "model": "default-model"},
        ]}))
        cfg._LLM_CONFIG_CACHE = cfg.LLMConfigFile.load(path)
        yield TestClient(app)
        cfg._reset_llm_config_cache()

    def test_lists_discovered_models(self, client, monkeypatch):
        _script(monkeypatch, ["model-x"])
        r = client.get("/api/test/providers/internal/models")
        assert r.status_code == 200
        assert r.json() == {"models": ["default-model", "model-x"],
                            "default": "default-model", "discovered": True}

    def test_unknown_provider_is_404(self, client):
        assert client.get("/api/test/providers/ghost/models").status_code == 404
```

In `core/tests/test_team_mode.py::TestGateLogic::test_path_whitelist`, add `"/api/test/providers/internal/models"` to the `ok` tuple.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest core/tests/test_model_discovery.py -k ModelsRoute core/tests/test_team_mode.py -v`
Expected: route tests FAIL with 404 on the known id; `test_path_whitelist` PASSES already (the `/api/test` prefix is allowlisted) — that is the gate verification.

- [ ] **Step 3: Implement** — in `playground.py`, above `@router.get("/api/test/ingested", ...)`:

```python
@router.get("/api/test/providers/{provider_id}/models")
async def provider_models(provider_id: str):
    """Models the Ask page may offer for one roster provider (discovered from
    its `/v1/models`, cached — see web/model_discovery.py). Unknown id is a
    404 rather than the default entry: the page only asks for ids it rendered."""
    from core.src.env.config import resolve_providers
    from core.src.web.model_discovery import models_for

    entry = next((p for p in resolve_providers() if p.id == provider_id), None)
    if entry is None:
        return JSONResponse({"error": "unknown provider"}, status_code=404)
    models, discovered = await asyncio.to_thread(models_for, entry)
    return JSONResponse({"models": models, "default": entry.model,
                         "discovered": discovered})
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest core/tests/test_model_discovery.py core/tests/test_team_mode.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add core/src/web/routes/playground.py core/tests/test_model_discovery.py core/tests/test_team_mode.py
git commit -m "web: /api/test/providers/{id}/models route"
```

---

### Task 5: Honour the chosen model on every Ask path

**Files:**
- Modify: `core/src/web/routes/query.py` — `_build_llm_from_env_or_default` (~line 244), `_run_query_sync` (~line 692, the `if provider_id or mode:` block ~line 742)
- Modify: `core/src/web/routes/playground.py` — `_form_model` (after `_form_provider`); `model` param + pass-through in `_run_nora_lane_for_merged`, `_run_sira_lane_for_merged` (and its `_run_query_for_test` call + `lane_config`), `_run_select_synth_lane`, `_select_synth_synthesize`, `_run_query_for_test`; call sites in `playground_ask` (merged runners ~1334/1338, legacy ~1390 and ~1482), `playground_ask_stream` (~1596/1602), `synthesize-group` (~1698)
- Test: `core/tests/test_ask_reasoning.py` (append)

**Interfaces:**
- Consumes: `allowed_models(entry)` (Task 3).
- Produces: `_form_model(form) -> str`; `_build_llm_from_env_or_default(provider_id=None, mode=None, model=None, *, use_roster=True)`; every lane function gains `model: str = ""` (or `str | None = None` where its siblings use that type), placed directly after `mode`.

- [ ] **Step 1: Write the failing tests** — append to `core/tests/test_ask_reasoning.py`:

```python
class TestFormModel:
    def test_reads_and_strips(self):
        from core.src.web.routes.playground import _form_model
        assert _form_model({"model": " model-x "}) == "model-x"

    def test_missing_is_empty(self):
        from core.src.web.routes.playground import _form_model
        assert _form_model({}) == ""


class TestChosenModel:
    """Strand llm-model-discovery: the asker's model is honoured only when the
    discovery cache lists it for that provider; anything else degrades to the
    entry's configured default, as an unknown provider id does."""

    def _roster(self, tmp_path, monkeypatch, cached=None):
        import json
        from core.src.env import config as cfg
        from core.src.web import model_discovery as md
        from core.src.web.routes import query as q

        path = tmp_path / "llm.json"
        path.write_text(json.dumps({
            "providers": [
                {"id": "internal", "name": "Internal",
                 "base_url": "http://llm.invalid/v1", "model": "default-model"},
                {"id": "backup", "name": "Backup",
                 "base_url": "http://backup.invalid/v1", "model": "backup-model"},
            ],
            "fallback_provider": "backup",
        }))
        cfg._LLM_CONFIG_CACHE = cfg.LLMConfigFile.load(path)
        md._reset_cache()
        if cached is not None:
            monkeypatch.setattr(md, "list_models", lambda *a, **k: list(cached))
            md.models_for(cfg.resolve_provider("internal"))
        monkeypatch.setattr(q, "_config_store_get", lambda module, key: None)
        return cfg, md

    def _primary(self, llm):
        return getattr(llm, "_primary", llm)

    def test_listed_model_is_used(self, tmp_path, monkeypatch):
        from core.src.web.routes.query import _build_llm_from_env_or_default
        cfg, md = self._roster(tmp_path, monkeypatch, cached=["model-x"])
        try:
            llm = _build_llm_from_env_or_default(provider_id="internal", model="model-x")
            assert self._primary(llm).model == "model-x"
        finally:
            cfg._reset_llm_config_cache(); md._reset_cache()

    def test_unlisted_model_degrades_to_default(self, tmp_path, monkeypatch, caplog):
        from core.src.web.routes.query import _build_llm_from_env_or_default
        cfg, md = self._roster(tmp_path, monkeypatch, cached=["model-x"])
        try:
            llm = _build_llm_from_env_or_default(provider_id="internal", model="rogue")
            assert self._primary(llm).model == "default-model"
            assert "rogue" in caplog.text
        finally:
            cfg._reset_llm_config_cache(); md._reset_cache()

    def test_no_model_uses_default(self, tmp_path, monkeypatch):
        from core.src.web.routes.query import _build_llm_from_env_or_default
        cfg, md = self._roster(tmp_path, monkeypatch, cached=["model-x"])
        try:
            llm = _build_llm_from_env_or_default(provider_id="internal")
            assert self._primary(llm).model == "default-model"
        finally:
            cfg._reset_llm_config_cache(); md._reset_cache()

    def test_empty_cache_allows_only_default(self, tmp_path, monkeypatch):
        from core.src.web.routes.query import _build_llm_from_env_or_default
        cfg, md = self._roster(tmp_path, monkeypatch)
        try:
            llm = _build_llm_from_env_or_default(provider_id="internal", model="model-x")
            assert self._primary(llm).model == "default-model"
        finally:
            cfg._reset_llm_config_cache(); md._reset_cache()

    def test_fallback_keeps_its_own_default_model(self, tmp_path, monkeypatch):
        from core.src.web.routes.query import _build_llm_from_env_or_default
        monkeypatch.setenv("NORA_LLM_REFUSAL_MARKERS", "I cannot")
        cfg, md = self._roster(tmp_path, monkeypatch, cached=["model-x"])
        try:
            llm = _build_llm_from_env_or_default(provider_id="internal", model="model-x")
            assert llm._fallback.model == "backup-model"
        finally:
            cfg._reset_llm_config_cache(); md._reset_cache()
```

`RefusalFallbackProvider` keeps its sides on `_primary` / `_fallback` (`core/src/llm/refusal.py:97`), and it only wraps when `NORA_LLM_REFUSAL_MARKERS` is set — which is why `_primary()` tolerates an unwrapped provider.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest core/tests/test_ask_reasoning.py -k "FormModel or ChosenModel" -v`
Expected: FAIL — `ImportError: cannot import name '_form_model'` and `TypeError: ... unexpected keyword argument 'model'`.

- [ ] **Step 3: Implement `query.py`.**

Signature:

```python
def _build_llm_from_env_or_default(
    provider_id: str | None = None,
    mode: str | None = None,
    model: str | None = None,
    *,
    use_roster: bool = True,
):
```

Add to its docstring, after the `mode` bullet list paragraph:

```
    `model` is the asker's model on the selected entry. It is honoured only
    when the discovery cache lists it for that entry (web/model_discovery.py
    `allowed_models`, which never fetches); empty or unlisted degrades to the
    entry's configured `model` — its default — with a warning for unlisted.
    The fallback entry always answers with its own default.
```

Inside the `if entry is not None:` branch, before the `logger.info("Web LLM resolved: ...")` call:

```python
        chosen_model = entry.model
        if model and model != entry.model:
            from core.src.web.model_discovery import allowed_models
            if model in allowed_models(entry):
                chosen_model = model
            else:
                logger.warning(
                    "Model %r is not offered by provider %r — using its "
                    "default %r", model, entry.id, entry.model,
                )
```

Then in that same branch replace `entry.model` with `chosen_model` in exactly two places: the `logger.info(...)` argument and `OpenAICompatibleProvider(model=...)`. Leave `fb_entry.model` untouched.

In `_run_query_sync`: add `model: str | None = None,` after `mode: str | None = None,`; change `if provider_id or mode:` to `if provider_id or mode or model:`; pass `model=model` in the `_build_llm_from_env_or_default(...)` call inside it; add `model` to the `logger.warning` format (`provider=%r mode=%r model=%r`).

- [ ] **Step 4: Implement `playground.py`.**

After `_form_provider`:

```python
def _form_model(form) -> str:
    """Read the model from an Ask form post. Validation lives in
    `_build_llm_from_env_or_default`, against the provider's discovered list."""
    return (form.get("model") or "").strip()
```

Thread `model` beside `mode` everywhere `mode` travels — a mechanical change, one parameter and one keyword per site:
- `_run_query_for_test(..., mode=None, model=None)` → `_run_query_sync(..., mode=mode, model=model)`.
- `_select_synth_synthesize(question, packed, provider_id=None, mode=None, model=None)` → `_build_llm_from_env_or_default(provider_id=provider_id, mode=mode, model=model)`.
- `_run_select_synth_lane(..., mode: str = "", model: str = "")` → `_select_synth_synthesize(..., mode=mode or None, model=model or None)`.
- `_run_nora_lane_for_merged(..., mode: str = "", model: str = "")` → `_run_query_for_test(..., mode=mode or None, model=model or None)`.
- `_run_sira_lane_for_merged(..., mode: str = "", model: str = "")` → both `_run_select_synth_lane(..., mode=mode, model=model)` and `_run_query_for_test(..., mode=mode or None, model=model or None)`.
- `playground_ask`: `model = _form_model(form)` after `mode = _form_mode(form)`; add `model=model` to both merged runners, and `model=model or None` to the two legacy `_run_query_for_test` calls (~1390, ~1482).
- `playground_ask_stream`: same as `playground_ask` for its form read and both runners.
- `synthesize-group` handler: add `model=_form_model(form) or None,` after `mode=_form_mode(form) or None,`.

Leave `_snapshot_nora_lane_config` and the SIRA `lane_config` unchanged: `llm_model` already records the model that answered (`answering_model(llm)` / `result["llm_model"]`).

Verify no site was missed:

Run: `grep -n "mode=mode" core/src/web/routes/playground.py core/src/web/routes/query.py`
Expected: every line printed also contains `model=`.

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/python -m pytest core/tests/test_ask_reasoning.py core/tests/test_playground_helpers.py core/tests/test_web_playground.py core/tests/test_web_eval_studio.py -v`
Expected: all PASS (Eval Studio proves `use_roster=False` still works with the new positional order — it is keyword-only, so it must).

- [ ] **Step 6: Commit**

```bash
git add core/src/web/routes/query.py core/src/web/routes/playground.py core/tests/test_ask_reasoning.py
git commit -m "web: honour the asker's discovered model on every Ask path"
```

---

### Task 6: Model select on the Ask page

**Files:**
- Modify: `core/src/web/templates/test/index.html` — markup inside `{% if providers %}` (~line 123), sticky-fields script (~line 640–760)

**Interfaces:**
- Consumes: `GET {root_path}/api/test/providers/{id}/models` (Task 4); form field `model` (Task 5).

- [ ] **Step 1: Markup.** Between the `ask-provider` `</select>` and the `ask-mode` `<select`, insert:

```html
          <select
              id="ask-model"
              name="model"
              class="form-select form-select-sm"
              style="max-width: 230px"
              aria-label="Which model on that provider answers"
              data-url-template="{{ root_path }}/api/test/providers/__ID__/models">
            {% for p in providers %}{% if loop.first %}
            <option value="{{ p.model }}" selected>{{ p.model }}</option>
            {% endif %}{% endfor %}
          </select>
```

The server-rendered option is the first provider's default, so a submit before the fetch returns still posts a valid model.

- [ ] **Step 2: JS.** In the sticky-fields IIFE, after `const modeEl = ...`:

```javascript
  const modelEl = document.getElementById("ask-model");
  let savedModel = "";
```

Inside the existing `try { const last = ... }` block, after the provider restore:

```javascript
    savedModel = last.model || "";
```

After `syncMode();` add:

```javascript
  // Model list per provider, discovered server-side (/v1/models, cached).
  // The select is disabled while loading but keeps showing a valid default,
  // so a quick submit still sends something the server accepts. A disabled
  // select is not submitted, so re-enable before any await can be outrun.
  let modelReq = 0;
  async function syncModels() {
    if (!providerEl || !modelEl) return;
    const reqId = ++modelReq;
    const url = modelEl.dataset.urlTemplate.replace(
      "__ID__", encodeURIComponent(providerEl.value));
    modelEl.title = "";
    try {
      const resp = await fetch(url);
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const data = await resp.json();
      if (reqId !== modelReq) return;           // a newer provider pick won
      modelEl.replaceChildren(...data.models.map(function (m) {
        const o = document.createElement("option");
        o.value = m;
        o.textContent = m;
        return o;
      }));
      const wanted = data.models.includes(savedModel) ? savedModel : data.default;
      modelEl.value = wanted;
      savedModel = "";                          // restore once, on first load
      if (!data.discovered) {
        modelEl.title = "Could not list this provider's models — showing "
          + "its configured default only.";
      }
    } catch (e) {
      if (reqId !== modelReq) return;
      modelEl.title = "Could not load models — the provider's default is used.";
    }
  }
  if (providerEl) providerEl.addEventListener("change", syncModels);
  syncModels();
```

When the provider changes before the fetch returns, the stale option can belong to the old provider. That is safe: the server degrades an unlisted model to the new provider's default (Task 5). Do not add client-side guarding for it.

In the `submit` listener's `localStorage.setItem` object, after `mode:`:

```javascript
        model:     modelEl ? modelEl.value : "",
```

- [ ] **Step 3: Verify in the browser.** Start the app per project memory (`http://127.0.0.1:8000/test` against `~/work/env_demo`) with an `llm.json` roster (via `NORA_LLM_CONFIG`) whose one entry points at a reachable OpenAI-compatible endpoint serving ≥2 models, and a second entry pointing at an unreachable `http://down.invalid/v1`. Check:
  1. Model select fills with the endpoint's models; default preselected.
  2. Asking with a non-default model → the answer epilogue "Synthesized by …" names that model.
  3. Switching to the unreachable provider → select shows only its default; tooltip explains.
  4. Reload → provider, model and mode are restored.
  5. History → re-run uses the currently selected model.
  6. With `NORA_WEB_TEAM_MODE=1` (no admin cookie) the select still fills and a question still answers.
  7. With no roster configured, the page renders no provider/model/mode controls and asks still work.

If no multi-model endpoint is reachable from this machine, report that checks 1–2 were not run rather than claiming them.

- [ ] **Step 4: Commit**

```bash
git add core/src/web/templates/test/index.html
git commit -m "web: Model select on the Ask page, filled from provider discovery"
```

---

### Task 7: Contracts, operator docs, full suite

**Files:**
- Modify: `core/src/env/MODULE.md`, `core/src/llm/MODULE.md`, `core/src/web/MODULE.md`, `docker/README.md`

These are curated `Public surface` edits describing the contract approved in `plan.md`; they ship on this branch per the project Branch-flow rule, and `/close-session` audits them.

- [ ] **Step 1: `core/src/env/MODULE.md`** — in the `resolve_providers()` bullet (line ~24), after "(`LLMProviderEntry`: id, name, base_url, model, …)" add: "Entry `model` is that provider's **default** model; the Ask page may offer others discovered from the endpoint (web `model_discovery`)." Add a bullet after the `default_provider` / `fallback_provider` bullet:

```markdown
- `resolve_model_discovery_ttl_s()` (config.py) — top-level `llm.json` `model_discovery_ttl_s`, default `DEFAULT_MODEL_DISCOVERY_TTL_S` (21600). Seconds a provider's discovered `/v1/models` list is trusted. File-only like the roster; missing/0 is silent, a negative or non-integer value warns and uses the default.
```

- [ ] **Step 2: `core/src/llm/MODULE.md`** — after the `OpenAICompatibleProvider` bullet:

```markdown
- `list_models(base_url, api_key="", timeout=10)` (openai_provider.py) — model ids from `GET {base_url}/models`, in server order; Bearer header only when a key is set. Raises `RuntimeError` on HTTP, network or shape failure — caching and last-good policy belong to the caller (web `model_discovery`).
```

- [ ] **Step 3: `core/src/web/MODULE.md`** — in the playground router description (line ~65), after the `provider` + `mode` sentence ending "…the pre-roster page unchanged)", add: "An optional `model` form field (`_form_model`) picks a model on the selected provider; `_build_llm_from_env_or_default` honours it only if `model_discovery.allowed_models(entry)` lists it (cache read, never a fetch), else the entry's default with a warning; the fallback entry always uses its own default. `GET /api/test/providers/{provider_id}/models` returns `{models, default, discovered}` for the Model select (404 for an id not in the roster; team-gate allowlisted via `/api/test`)." Add a bullet to the Public surface list:

```markdown
- `model_discovery` (model_discovery.py) — in-process per-provider cache of `llm.list_models()` results for `model_discovery_ttl_s`. `models_for(entry)` fetches when stale and keeps the last good list when a refresh fails (a failed attempt is retried once per TTL, not per request); `allowed_models(entry)` reads the cache only. The entry's configured `model` is always present in both.
```

In the `**Depends on**` line, add `[llm](../llm/MODULE.md)` (`model_discovery` imports `llm.list_models`; `playground.py` already imports `llm.openai_provider` without the edge being declared — adding it once covers both, and `/close-session` will see it as a curated-section change).

- [ ] **Step 4: `docker/README.md`** — in the section documenting `NORA_LLM_CONFIG`, add one paragraph:

```markdown
Each roster entry's `model` is that provider's default. The Ask page also offers every model the provider's `GET /v1/models` lists, cached for `model_discovery_ttl_s` seconds (top-level key, default 21600). A model added on the endpoint appears after the TTL or a `nora-web` restart. If the endpoint cannot list models, only the default is offered.
```

- [ ] **Step 5: Full suite**

Run: `.venv/bin/python -m pytest core/tests/ -q`
Expected: no failures beyond those already failing on `main` (if any fail, run the same test on `main` via `git stash`-free comparison — `git worktree add ../nora-main main` — before attributing them).

- [ ] **Step 6: Commit**

```bash
git add core/src/env/MODULE.md core/src/llm/MODULE.md core/src/web/MODULE.md docker/README.md
git commit -m "docs: model discovery contracts (env, llm, web) + operator note"
```

---

## After the tasks

- `/close-session` on this branch before the strand-closing commit: journal + `decisions-draft.md` (D-216 one-model-per-entry relaxed; entry `model` = default; last-good on refresh failure; server-side model check; eval excluded).
- Push + PR only on explicit go-ahead.
