"""Per-question provider + Fast/Think mode on the Ask page (Phase 1).

Covers the two seams the feature adds:
  - `QueryPipeline.query(synthesizer=...)` — a per-call synthesizer override,
    so a request can vary the LLM without rebuilding the cached pipeline.
  - `_form_mode` / `_form_provider` — what the Ask form is allowed to send.
  - `_reasoning_for` — how a Fast/Think choice becomes a wire value.

No network: the store, embedder and synthesizers are all doubles.
"""

from __future__ import annotations

import pathlib

import networkx as nx

from core.src.query.pipeline import QueryPipeline
from core.src.query.schema import QueryResponse
from core.src.vectorstore.store_base import QueryResult
from core.src.web.routes.playground import _form_mode, _form_provider


class _FixedEmbedder:
    def embed_query(self, text):
        return [0.0] * 8

    def embed(self, texts):
        return [[0.0] * 8] * len(texts)

    @property
    def dimension(self):
        return 8

    @property
    def model_name(self):
        return "fixed-zero"


class _ScriptedStore:
    def __init__(self, result: QueryResult) -> None:
        self._result = result

    def query(self, query_embedding, n_results=10, where=None):
        return self._result

    @property
    def count(self):
        return len(self._result.ids)

    def reset(self):
        pass

    def get_all(self):
        return QueryResult(
            ids=self._result.ids,
            documents=self._result.documents,
            metadatas=self._result.metadatas,
            distances=[],
        )


class _NamedSynthesizer:
    """Records that it ran and stamps its name into the answer."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    def synthesize(self, context, intent) -> QueryResponse:
        self.calls += 1
        return QueryResponse(answer=f"answer from {self.name}", citations=[])


def _result() -> QueryResult:
    return QueryResult(
        ids=["req:R1"],
        documents=["text R1"],
        metadatas=[{
            "req_id": "R1",
            "plan_id": "PLAN",
            "mno": "VZW",
            "release": "2026",
            "section_number": "1.0",
            "zone_type": "",
            "feature_ids": [],
            "hierarchy_path": ["DOC"],
        }],
        distances=[0.2],
    )


def _pipeline(constructed: _NamedSynthesizer) -> QueryPipeline:
    return QueryPipeline(
        graph=nx.DiGraph(),
        embedder=_FixedEmbedder(),
        store=_ScriptedStore(_result()),
        enable_bm25=False,
        enable_grouping=False,
        synthesizer=constructed,
    )


class TestSynthesizerOverride:
    def test_constructed_synthesizer_used_by_default(self):
        """No override — behaviour is exactly what it was before."""
        constructed = _NamedSynthesizer("cached")
        resp = _pipeline(constructed).query("what are the R1 requirements")
        assert constructed.calls == 1
        assert "cached" in resp.answer

    def test_override_replaces_it_for_one_call(self):
        constructed = _NamedSynthesizer("cached")
        per_query = _NamedSynthesizer("per-query")
        pipeline = _pipeline(constructed)

        resp = pipeline.query(
            "what are the R1 requirements", synthesizer=per_query,
        )
        assert per_query.calls == 1
        assert constructed.calls == 0
        assert "per-query" in resp.answer

    def test_override_does_not_persist(self):
        """The override is per call — the next query falls back to the
        pipeline's own synthesizer, so one request's reasoning level can
        never leak into the next."""
        constructed = _NamedSynthesizer("cached")
        per_query = _NamedSynthesizer("per-query")
        pipeline = _pipeline(constructed)

        pipeline.query("first question about R1", synthesizer=per_query)
        resp = pipeline.query("second question about R1")

        assert per_query.calls == 1
        assert constructed.calls == 1
        assert "cached" in resp.answer


class TestFormMode:
    def test_accepts_fast_and_think(self):
        assert _form_mode({"mode": "fast"}) == "fast"
        assert _form_mode({"mode": "think"}) == "think"

    def test_normalizes_case_and_whitespace(self):
        assert _form_mode({"mode": "  FAST "}) == "fast"

    def test_missing_means_provider_default(self):
        assert _form_mode({}) == ""
        assert _form_mode({"mode": "   "}) == ""

    def test_unknown_value_degrades(self):
        """A stale page must not cost someone their answer."""
        assert _form_mode({"mode": "ultra"}) == ""


class TestFormProvider:
    def test_reads_the_id(self):
        assert _form_provider({"provider": "dgx-130b"}) == "dgx-130b"

    def test_missing_is_empty(self):
        assert _form_provider({}) == ""

    def test_unknown_ids_are_not_rejected_here(self):
        """Validation belongs to env.config.resolve_provider, which falls
        back to the default entry — one owner for that rule."""
        assert _form_provider({"provider": "nope"}) == "nope"


class TestModeToReasoning:
    """Fast means "skip thinking"; Think means "send nothing and let the
    deployment decide" — we never invent an effort level for it."""

    def _entry(self, supports, default_mode="think"):
        from core.src.env.config import LLMProviderEntry
        return LLMProviderEntry(
            id="p", name="P", base_url="u", model="m",
            supports_reasoning_control=supports, default_mode=default_mode,
        )

    def test_fast_sends_none(self):
        from core.src.web.routes.query import _reasoning_for
        assert _reasoning_for(self._entry(True), "fast") == "none"

    def test_think_sends_nothing(self):
        from core.src.web.routes.query import _reasoning_for
        assert _reasoning_for(self._entry(True), "think") is None

    def test_absent_mode_uses_the_provider_default(self):
        from core.src.web.routes.query import _reasoning_for
        assert _reasoning_for(self._entry(True, "fast"), "") == "none"
        assert _reasoning_for(self._entry(True, "think"), "") is None

    def test_unsupported_provider_sends_nothing_whatever_the_mode(self):
        """The field would be dropped anyway; pretending otherwise would let
        the UI claim a change that never reached the wire."""
        from core.src.web.routes.query import _reasoning_for
        assert _reasoning_for(self._entry(False), "fast") is None
        assert _reasoning_for(self._entry(False), "think") is None


class TestRosterResolution:
    def _write(self, tmp_path, entries):
        import json
        from core.src.env import config as cfg
        path = tmp_path / "llm.json"
        path.write_text(json.dumps({"providers": entries}))
        cfg._LLM_CONFIG_CACHE = cfg.LLMConfigFile.load(path)
        return cfg

    def test_no_roster_returns_none(self, tmp_path):
        cfg = self._write(tmp_path, [])
        try:
            assert cfg.resolve_providers() == []
            assert cfg.resolve_provider("anything") is None
        finally:
            cfg._reset_llm_config_cache()

    def test_unknown_id_falls_back_to_first_entry(self, tmp_path):
        cfg = self._write(tmp_path, [
            {"id": "a", "name": "130B", "base_url": "u", "model": "m1"},
            {"id": "b", "name": "14B", "base_url": "u", "model": "m2"},
        ])
        try:
            assert cfg.resolve_provider("ghost").id == "a"
            assert cfg.resolve_provider("b").id == "b"
        finally:
            cfg._reset_llm_config_cache()

    def test_incomplete_entry_is_dropped_not_fatal(self, tmp_path):
        cfg = self._write(tmp_path, [
            {"id": "half"},                                    # no url/model
            {"id": "ok", "base_url": "u", "model": "m"},
        ])
        try:
            assert [p.id for p in cfg.resolve_providers()] == ["ok"]
        finally:
            cfg._reset_llm_config_cache()

    def test_name_defaults_to_id_and_bad_mode_defaults_to_think(self, tmp_path):
        cfg = self._write(tmp_path, [
            {"id": "solo", "base_url": "u", "model": "m", "default_mode": "zoom"},
        ])
        try:
            p = cfg.resolve_provider("")
            assert p.name == "solo"
            assert p.default_mode == "think"
        finally:
            cfg._reset_llm_config_cache()


class TestExampleConfigStaysValid:
    """`config/llm.json.example` is the file people copy from, so it has to
    parse and to name every field — an example that drifts is worse than none."""

    _PATH = pathlib.Path(__file__).resolve().parents[2] / "config" / "llm.json.example"

    def test_it_parses_and_yields_its_providers(self):
        from core.src.env.config import LLMConfigFile

        cfg = LLMConfigFile.load(self._PATH)
        assert [p.id for p in cfg.providers] == ["dgx-130b", "internal-14b"]
        assert cfg.providers[0].supports_reasoning_control is True
        assert cfg.providers[1].supports_reasoning_control is False

    def test_it_documents_every_entry_field(self):
        """A field added to LLMProviderEntry must reach the example, or the
        next person copying it silently misses the new knob."""
        import dataclasses
        import json

        from core.src.env.config import LLMProviderEntry

        raw = json.loads(self._PATH.read_text())
        documented = set(raw["providers"][0]) | set(raw["providers"][1])
        expected = {f.name for f in dataclasses.fields(LLMProviderEntry)}
        assert expected <= documented, f"undocumented: {expected - documented}"

    def test_it_contains_no_literal_keys(self):
        """Keys are referenced by env-var NAME. A literal here would be a
        secret in a committed file."""
        import json

        raw = json.loads(self._PATH.read_text())
        for entry in raw["providers"]:
            assert "api_key" not in entry
            assert entry["api_key_env"].startswith("NORA_")


class TestNoRosterBuildPath:
    """Regression: with no roster configured, `_build_llm_from_env_or_default`
    falls through to the single-provider chain — which crashed with
    UnboundLocalError on the `reasoning` local (assigned only in the roster
    branch). Every earlier run exercised the roster branch, because the tree
    under test still carried a committed dev roster; this test pins the
    designed "no roster = chain unchanged" case by running the real fallback
    path end to end."""

    def _no_roster(self, tmp_path):
        import json
        from core.src.env import config as cfg
        path = tmp_path / "llm.json"
        path.write_text(json.dumps({}))
        cfg._LLM_CONFIG_CACHE = cfg.LLMConfigFile.load(path)
        return cfg

    def test_no_roster_build_does_not_raise(self, tmp_path, monkeypatch):
        from core.src.web.routes.query import _build_llm_from_env_or_default
        cfg = self._no_roster(tmp_path)
        monkeypatch.setenv("NORA_LLM_PROVIDER", "mock")
        try:
            assert _build_llm_from_env_or_default() is not None
        finally:
            cfg._reset_llm_config_cache()

    def test_stale_mode_without_roster_is_ignored_not_fatal(self, tmp_path, monkeypatch):
        """A stale page can still post mode=fast after a roster is removed;
        the chain has no declared capability, so the mode is dropped."""
        from core.src.web.routes.query import _build_llm_from_env_or_default
        cfg = self._no_roster(tmp_path)
        monkeypatch.setenv("NORA_LLM_PROVIDER", "mock")
        try:
            assert _build_llm_from_env_or_default(mode="fast") is not None
        finally:
            cfg._reset_llm_config_cache()


class TestRosterIndependentOfConfigDB:
    """Strand llm-roster-deploy (#19 items 6-7).

    A selected roster entry owns its configuration end to end — the
    Config-page DB does not reach it. This REVERSES the brief's stated
    ordering ("roster sits BELOW the Config-page DB"); the call was taken so a
    named endpoint always means that endpoint, since a label that can silently
    point elsewhere is worse than no label.

    Both tests below call the REAL `_build_llm_from_env_or_default`. The three
    pre-existing references to it monkeypatch it away, which is how a latent
    UnboundLocalError on the no-roster path reached production.
    """

    def _roster(self, tmp_path, monkeypatch, *, timeout=None, db=None):
        import json
        from core.src.env import config as cfg
        from core.src.web.routes import query as q

        entry = {"id": "dgx", "name": "DGX", "base_url": "http://dgx.invalid/v1",
                 "model": "roster-model"}
        if timeout is not None:
            entry["timeout"] = timeout
        path = tmp_path / "llm.json"
        path.write_text(json.dumps({"providers": [entry]}))
        cfg._LLM_CONFIG_CACHE = cfg.LLMConfigFile.load(path)
        # Stand in for the Config-page DB having values saved.
        monkeypatch.setattr(q, "_config_store_get",
                            lambda module, key: (db or {}).get(key))
        return cfg

    def test_db_values_do_not_reach_a_selected_roster_entry(self, tmp_path, monkeypatch):
        from core.src.web.routes.query import _build_llm_from_env_or_default

        cfg = self._roster(tmp_path, monkeypatch, db={
            "llm_model": "db-model",
            "llm_base_url": "http://db.invalid/v1",
            "llm_timeout": 42,
        })
        try:
            llm = _build_llm_from_env_or_default(provider_id="dgx")
            assert llm.model == "roster-model"
            assert "dgx.invalid" in llm._base_url
            assert llm._timeout != 42
        finally:
            cfg._reset_llm_config_cache()

    def test_entry_timeout_used_and_unset_falls_back_to_default(
        self, tmp_path, monkeypatch,
    ):
        """Unset resolves to DEFAULT_LLM_TIMEOUT, not through
        resolve_llm_timeout() — that would reintroduce the DB and
        NORA_LLM_TIMEOUT tiers this entry is independent of."""
        from core.src.env.config import DEFAULT_LLM_TIMEOUT
        from core.src.web.routes.query import _build_llm_from_env_or_default

        cfg = self._roster(tmp_path, monkeypatch, timeout=900,
                           db={"llm_timeout": 42})
        try:
            assert _build_llm_from_env_or_default(provider_id="dgx")._timeout == 900
        finally:
            cfg._reset_llm_config_cache()

        cfg = self._roster(tmp_path, monkeypatch, db={"llm_timeout": 42})
        monkeypatch.setenv("NORA_LLM_TIMEOUT", "77")
        try:
            llm = _build_llm_from_env_or_default(provider_id="dgx")
            assert llm._timeout == DEFAULT_LLM_TIMEOUT
        finally:
            cfg._reset_llm_config_cache()


class TestRosterRefusalWrap:
    """Strand llm-roster-deploy (#19 item 8).

    Before this, configuring a roster silently REMOVED refusal coverage from
    Ask: the no-roster chain gets wrapped by `create_llm_provider`, while the
    roster branch returned a bare provider. Nothing asserted the wrap on either
    path, so the gap was invisible.
    """

    def _roster(self, tmp_path, monkeypatch, *, fallback_id=None):
        import json
        from core.src.env import config as cfg
        from core.src.web.routes import query as q

        doc = {"providers": [
            {"id": "dgx", "name": "DGX", "base_url": "http://dgx.invalid/v1",
             "model": "primary"},
            {"id": "safe", "name": "Safe", "base_url": "http://safe.invalid/v1",
             "model": "fallback-model"},
        ]}
        if fallback_id:
            doc["fallback_provider"] = fallback_id
        path = tmp_path / "llm.json"
        path.write_text(json.dumps(doc))
        cfg._LLM_CONFIG_CACHE = cfg.LLMConfigFile.load(path)
        monkeypatch.setattr(q, "_config_store_get", lambda module, key: None)
        return cfg

    def test_roster_path_is_wrapped_when_fallback_configured(
        self, tmp_path, monkeypatch,
    ):
        from core.src.llm.refusal import RefusalFallbackProvider
        from core.src.web.routes.query import _build_llm_from_env_or_default

        cfg = self._roster(tmp_path, monkeypatch, fallback_id="safe")
        monkeypatch.setenv("NORA_LLM_REFUSAL_MARKERS", "I cannot help")
        try:
            llm = _build_llm_from_env_or_default(provider_id="dgx")
            assert isinstance(llm, RefusalFallbackProvider)
        finally:
            cfg._reset_llm_config_cache()

    def test_no_fallback_entry_leaves_provider_bare(self, tmp_path, monkeypatch):
        """A roster without `fallback_provider` has nowhere to reroute, so the
        provider stays unwrapped rather than quietly using the env-var
        endpoint the roster deliberately does not consult."""
        from core.src.llm.refusal import RefusalFallbackProvider
        from core.src.web.routes.query import _build_llm_from_env_or_default

        cfg = self._roster(tmp_path, monkeypatch)
        monkeypatch.setenv("NORA_LLM_REFUSAL_MARKERS", "I cannot help")
        try:
            llm = _build_llm_from_env_or_default(provider_id="dgx")
            assert not isinstance(llm, RefusalFallbackProvider)
        finally:
            cfg._reset_llm_config_cache()

    def test_fallback_endpoint_comes_from_the_roster_not_env_vars(
        self, tmp_path, monkeypatch,
    ):
        """The whole roster is configured in one file — NORA_LLM_FALLBACK_*
        must not decide where a roster reroute lands."""
        from core.src.web.routes.query import _build_llm_from_env_or_default

        cfg = self._roster(tmp_path, monkeypatch, fallback_id="safe")
        monkeypatch.setenv("NORA_LLM_REFUSAL_MARKERS", "I cannot help")
        monkeypatch.setenv("NORA_LLM_FALLBACK_BASE_URL", "http://envvar.invalid/v1")
        monkeypatch.setenv("NORA_LLM_FALLBACK_MODEL", "env-var-model")
        try:
            llm = _build_llm_from_env_or_default(provider_id="dgx")
            assert llm._fallback.model == "fallback-model"
            assert "safe.invalid" in llm._fallback._base_url
        finally:
            cfg._reset_llm_config_cache()

    def test_markers_unset_means_no_wrap(self, tmp_path, monkeypatch):
        """Without markers there is nothing to detect a refusal with, so
        wrapping would add a decorator that can never fire."""
        from core.src.llm.refusal import RefusalFallbackProvider
        from core.src.web.routes.query import _build_llm_from_env_or_default

        cfg = self._roster(tmp_path, monkeypatch, fallback_id="safe")
        monkeypatch.delenv("NORA_LLM_REFUSAL_MARKERS", raising=False)
        try:
            llm = _build_llm_from_env_or_default(provider_id="dgx")
            assert not isinstance(llm, RefusalFallbackProvider)
        finally:
            cfg._reset_llm_config_cache()

    def test_fallback_entry_selected_directly_is_not_wrapped_in_itself(
        self, tmp_path, monkeypatch,
    ):
        """Picking the fallback endpoint itself has nothing behind it — it IS
        the fallback. Wrapping it in itself would loop a refusal back to the
        same endpoint."""
        from core.src.llm.refusal import RefusalFallbackProvider
        from core.src.web.routes.query import _build_llm_from_env_or_default

        cfg = self._roster(tmp_path, monkeypatch, fallback_id="safe")
        monkeypatch.setenv("NORA_LLM_REFUSAL_MARKERS", "I cannot help")
        try:
            llm = _build_llm_from_env_or_default(provider_id="safe")
            assert not isinstance(llm, RefusalFallbackProvider)
        finally:
            cfg._reset_llm_config_cache()


class TestRerouteIsDisclosed:
    """Strand llm-roster-deploy (#19 item 9).

    Making a reroute visible is what resolves the original objection to
    wrapping the roster path at all — that quietly answering from a different
    endpoint defeats the choice the asker just made. The epilogue already
    named the answering MODEL; it could not say a reroute HAPPENED, because
    two roster endpoints may serve the same model tag.
    """

    class _Refusing:
        model = "primary-model"

        def complete(self, prompt, system="", temperature=0.0, max_tokens=4096):
            return "I cannot help with that."

    class _Answering:
        model = "fallback-model"

        def complete(self, prompt, system="", temperature=0.0, max_tokens=4096):
            return "Here is the answer."

    def _wrapped(self):
        from core.src.llm.refusal import RefusalFallbackProvider
        llm = RefusalFallbackProvider(
            self._Refusing(), self._Answering(), ("I cannot help",),
        )
        llm.primary_entry_name = "130B — DGX"
        llm.fallback_entry_name = "14B — internal"
        llm.fallback_entry_id = "internal-14b"
        return llm

    def test_note_names_both_endpoints_after_a_reroute(self):
        from core.src.llm.base import reroute_note

        llm = self._wrapped()
        assert reroute_note(llm) == ""          # nothing answered yet
        llm.complete("q")
        note = reroute_note(llm)
        assert "14B — internal" in note and "130B — DGX" in note

    def test_no_note_when_the_primary_answered(self):
        from core.src.llm.base import reroute_note
        from core.src.llm.refusal import RefusalFallbackProvider

        llm = RefusalFallbackProvider(
            self._Answering(), self._Refusing(), ("I cannot help",),
        )
        llm.primary_entry_name = "A"
        llm.fallback_entry_name = "B"
        llm.complete("q")
        assert reroute_note(llm) == ""

    def test_no_note_when_endpoints_are_unnamed(self):
        """The env-var fallback path attaches no names. Inventing a label for
        an unnamed endpoint would say less than nothing."""
        from core.src.llm.base import reroute_note
        from core.src.llm.refusal import RefusalFallbackProvider

        llm = RefusalFallbackProvider(
            self._Refusing(), self._Answering(), ("I cannot help",),
        )
        llm.complete("q")
        assert llm.last_was_fallback is True
        assert reroute_note(llm) == ""

    def test_plain_provider_has_no_note(self):
        """`reroute_note` is called on every synthesis, most of which use an
        unwrapped provider — it must be inert there, not raise."""
        from core.src.llm.base import reroute_note

        assert reroute_note(self._Answering()) == ""
        assert reroute_note(None) == ""

    def test_synthesizer_epilogue_carries_the_note(self):
        """End to end through the real epilogue path, so the note reaches the
        answer body the asker reads rather than only existing as a helper."""
        from core.src.query.synthesizer import LLMSynthesizer

        llm = self._wrapped()
        synth = LLMSynthesizer(llm, max_tokens=256)

        class _Ctx:
            context_text = "ctx"
            system_prompt = "sys"
            chunks = []          # no citation recovery pass for this stub

        resp = synth.synthesize(_Ctx(), None)
        assert "Synthesized by fallback-model" in resp.answer
        assert "rerouted to 14B — internal" in resp.answer


class TestCurationBypassesRoster:
    """Strand llm-roster-deploy (#19 item 10).

    The Eval Studio curation chat is not the Ask flow. A golden response
    curated against whichever endpoint happened to be the roster default would
    not be reproducible, so it takes the single-provider chain regardless of
    the roster.

    Calls the REAL `_build_llm_from_env_or_default`. The two pre-existing
    references in test_web_eval_studio.py monkeypatch it away — they patch
    exactly this seam, which is why the coverage lives here instead.
    """

    def _roster(self, tmp_path, monkeypatch):
        import json
        from core.src.env import config as cfg
        from core.src.web.routes import query as q

        path = tmp_path / "llm.json"
        path.write_text(json.dumps({
            "providers": [{
                "id": "dgx", "name": "DGX",
                "base_url": "http://dgx.invalid/v1", "model": "roster-model",
            }],
            "default_provider": "dgx",
        }))
        cfg._LLM_CONFIG_CACHE = cfg.LLMConfigFile.load(path)
        monkeypatch.setattr(q, "_config_store_get", lambda module, key: None)
        return cfg

    def test_use_roster_false_ignores_a_configured_roster(
        self, tmp_path, monkeypatch,
    ):
        from core.src.web.routes.query import _build_llm_from_env_or_default

        cfg = self._roster(tmp_path, monkeypatch)
        monkeypatch.setenv("NORA_LLM_PROVIDER", "mock")
        try:
            llm = _build_llm_from_env_or_default(use_roster=False)
            assert llm is not None
            assert getattr(llm, "model", "") != "roster-model"
        finally:
            cfg._reset_llm_config_cache()

    def test_default_still_uses_the_roster(self, tmp_path, monkeypatch):
        """The Ask flow's cached default must stay ON the roster — only the
        curation caller opts out. Guards against the bypass being applied too
        widely."""
        from core.src.web.routes.query import _build_llm_from_env_or_default

        cfg = self._roster(tmp_path, monkeypatch)
        try:
            assert _build_llm_from_env_or_default().model == "roster-model"
        finally:
            cfg._reset_llm_config_cache()

    def test_no_provider_id_would_not_have_been_enough(
        self, tmp_path, monkeypatch,
    ):
        """Documents WHY use_roster exists: after item 5,
        resolve_provider(None) returns the default entry, so passing no
        provider_id still lands on the roster."""
        from core.src.web.routes.query import _build_llm_from_env_or_default

        cfg = self._roster(tmp_path, monkeypatch)
        try:
            assert _build_llm_from_env_or_default(provider_id="").model == "roster-model"
        finally:
            cfg._reset_llm_config_cache()
