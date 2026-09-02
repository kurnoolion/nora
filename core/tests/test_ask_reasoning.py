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
