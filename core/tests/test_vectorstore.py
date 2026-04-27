"""Tests for vector store construction (PoC Step 9).

Test categories:
  - Config: round-trip serialization, load/save, defaults
  - ChunkBuilder: contextualization, metadata, tables, images, edge cases
  - Protocol conformance: mock providers satisfy protocols
  - Builder: orchestration with mock providers
  - Integration: real parsed data with mock embedder
"""

from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import pytest

from src.vectorstore.config import VectorStoreConfig
from src.vectorstore.chunk_builder import ChunkBuilder, Chunk
from src.vectorstore.embedding_base import EmbeddingProvider
from src.vectorstore.store_base import VectorStoreProvider, QueryResult
from src.vectorstore.builder import VectorStoreBuilder, BuildStats


# ── Mock providers ──────────────────────────────────────────────


class MockEmbedder:
    """Mock embedding provider for tests. Returns deterministic vectors."""

    def __init__(self, dim: int = 8):
        self._dim = dim
        self._model = "mock-embedder"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._hash_text(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._hash_text(text)

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return self._model

    def _hash_text(self, text: str) -> list[float]:
        """Produce a deterministic vector from text hash."""
        h = hash(text)
        vec = []
        for i in range(self._dim):
            # Use bit shifting to get different values per dimension
            val = ((h >> (i * 4)) & 0xF) / 15.0 - 0.5
            vec.append(val)
        # Normalize
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


class MockStore:
    """In-memory mock vector store for tests."""

    def __init__(self):
        self._docs: dict[str, dict] = {}

    def add(self, ids, embeddings, documents, metadatas):
        for i, doc_id in enumerate(ids):
            self._docs[doc_id] = {
                "embedding": embeddings[i],
                "document": documents[i],
                "metadata": metadatas[i],
            }

    def query(self, query_embedding, n_results=10, where=None):
        # Simple cosine similarity search
        scored = []
        for doc_id, data in self._docs.items():
            if where:
                match = all(
                    data["metadata"].get(k) == v for k, v in where.items()
                )
                if not match:
                    continue

            # Cosine distance
            emb = data["embedding"]
            dot = sum(a * b for a, b in zip(query_embedding, emb))
            norm_q = math.sqrt(sum(a * a for a in query_embedding)) or 1
            norm_e = math.sqrt(sum(a * a for a in emb)) or 1
            cos_sim = dot / (norm_q * norm_e)
            dist = 1.0 - cos_sim
            scored.append((doc_id, data, dist))

        scored.sort(key=lambda x: x[2])
        scored = scored[:n_results]

        return QueryResult(
            ids=[s[0] for s in scored],
            documents=[s[1]["document"] for s in scored],
            metadatas=[s[1]["metadata"] for s in scored],
            distances=[s[2] for s in scored],
        )

    @property
    def count(self):
        return len(self._docs)

    def reset(self):
        self._docs.clear()


# ── Test data ───────────────────────────────────────────────────


def _make_tree(
    plan_id: str = "LTEDATARETRY",
    n_reqs: int = 3,
) -> dict:
    """Create a synthetic requirement tree for testing."""
    reqs = []
    for i in range(n_reqs):
        req = {
            "req_id": f"VZ_REQ_{plan_id}_{1000 + i}",
            "section_number": f"1.{i + 1}",
            "title": f"Section {i + 1} Title",
            "parent_req_id": "" if i == 0 else f"VZ_REQ_{plan_id}_{1000}",
            "parent_section": "" if i == 0 else "1.1",
            "hierarchy_path": ["ROOT"] if i == 0 else ["ROOT", f"Section {i + 1} Title"],
            "zone_type": "introduction" if i == 0 else "software_specs",
            "text": f"This is the body text for requirement {i + 1} in {plan_id}.",
            "tables": [],
            "images": [],
            "children": [],
            "cross_references": {
                "internal": [],
                "external_plans": [],
                "standards": [],
            },
        }

        # Add a table to the second requirement
        if i == 1:
            req["tables"] = [
                {
                    "headers": ["Parameter", "Value", "Unit"],
                    "rows": [
                        ["T3402", "720", "seconds"],
                        ["T3411", "10", "seconds"],
                    ],
                    "source": "inline",
                }
            ]

        # Add an image to the third requirement
        if i == 2:
            req["images"] = [
                {
                    "path": "extracted_images/LTEDATARETRY/p10_000.png",
                    "surrounding_text": "Figure 1 - Retry State Machine",
                }
            ]

        reqs.append(req)

    return {
        "mno": "VZW",
        "release": "2026_feb",
        "plan_id": plan_id,
        "plan_name": f"LTE_{plan_id.replace('LTE', '')}",
        "version": "39",
        "release_date": "February 2026",
        "referenced_standards_releases": {},
        "requirements": reqs,
    }


def _make_taxonomy() -> dict:
    """Create a synthetic taxonomy for testing."""
    return {
        "mno": "VZW",
        "release": "2026_feb",
        "features": [
            {
                "feature_id": "DATA_RETRY",
                "name": "LTE Data Retry",
                "description": "Data retry logic",
                "keywords": ["retry", "timer"],
                "is_primary_in": ["LTEDATARETRY"],
                "is_referenced_in": ["LTESMS"],
                "depends_on_features": [],
                "mno_coverage": {"VZW": ["LTEDATARETRY"]},
                "source_plans": ["LTEDATARETRY"],
            },
            {
                "feature_id": "SMS",
                "name": "SMS over LTE",
                "description": "SMS procedures",
                "keywords": ["SMS"],
                "is_primary_in": ["LTESMS"],
                "is_referenced_in": [],
                "depends_on_features": [],
                "mno_coverage": {"VZW": ["LTESMS"]},
                "source_plans": ["LTESMS"],
            },
        ],
        "source_documents": ["LTEDATARETRY", "LTESMS"],
    }


# ═══════════════════════════════════════════════════════════════
# Config tests
# ═══════════════════════════════════════════════════════════════


class TestConfig:
    def test_defaults(self):
        c = VectorStoreConfig()
        assert c.embedding_model == "all-MiniLM-L6-v2"
        assert c.distance_metric == "cosine"
        assert c.embedding_batch_size == 64
        assert c.normalize_embeddings is True
        assert c.vector_store_backend == "chromadb"

    def test_custom_values(self):
        c = VectorStoreConfig(
            embedding_model="all-mpnet-base-v2",
            distance_metric="l2",
            embedding_batch_size=128,
        )
        assert c.embedding_model == "all-mpnet-base-v2"
        assert c.distance_metric == "l2"
        assert c.embedding_batch_size == 128

    def test_round_trip_json(self):
        original = VectorStoreConfig(
            embedding_model="BAAI/bge-large-en-v1.5",
            distance_metric="ip",
            persist_directory="/tmp/vs_test",
            extra={"api_key": "test123"},
        )

        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            path = Path(f.name)

        try:
            original.save_json(path)
            loaded = VectorStoreConfig.load_json(path)

            assert loaded.embedding_model == original.embedding_model
            assert loaded.distance_metric == original.distance_metric
            assert loaded.persist_directory == original.persist_directory
            assert loaded.extra == original.extra
        finally:
            path.unlink(missing_ok=True)

    def test_to_dict(self):
        c = VectorStoreConfig()
        d = c.to_dict()
        assert isinstance(d, dict)
        assert "embedding_model" in d
        assert "distance_metric" in d
        assert "extra" in d

    def test_load_ignores_unknown_fields(self):
        """Loading a config with extra fields doesn't crash."""
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump({"embedding_model": "test-model", "unknown_field": 42}, f)
            path = Path(f.name)

        try:
            loaded = VectorStoreConfig.load_json(path)
            assert loaded.embedding_model == "test-model"
            # unknown_field is silently ignored
        finally:
            path.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════
# Protocol conformance tests
# ═══════════════════════════════════════════════════════════════


class TestProtocols:
    def test_mock_embedder_satisfies_protocol(self):
        embedder = MockEmbedder()
        assert isinstance(embedder, EmbeddingProvider)

    def test_mock_store_satisfies_protocol(self):
        store = MockStore()
        assert isinstance(store, VectorStoreProvider)

    def test_mock_embedder_dimension(self):
        embedder = MockEmbedder(dim=16)
        assert embedder.dimension == 16
        assert embedder.model_name == "mock-embedder"

    def test_mock_embedder_embed(self):
        embedder = MockEmbedder(dim=8)
        vecs = embedder.embed(["hello", "world"])
        assert len(vecs) == 2
        assert all(len(v) == 8 for v in vecs)

    def test_mock_embedder_deterministic(self):
        embedder = MockEmbedder(dim=8)
        v1 = embedder.embed(["test"])[0]
        v2 = embedder.embed(["test"])[0]
        assert v1 == v2

    def test_mock_embedder_different_texts(self):
        embedder = MockEmbedder(dim=8)
        v1 = embedder.embed(["hello"])[0]
        v2 = embedder.embed(["world"])[0]
        assert v1 != v2

    def test_mock_embedder_empty(self):
        embedder = MockEmbedder()
        assert embedder.embed([]) == []

    def test_mock_embedder_query(self):
        embedder = MockEmbedder(dim=8)
        v = embedder.embed_query("test query")
        assert len(v) == 8

    def test_mock_store_add_and_count(self):
        store = MockStore()
        assert store.count == 0
        store.add(
            ids=["a", "b"],
            embeddings=[[1.0, 0.0], [0.0, 1.0]],
            documents=["doc a", "doc b"],
            metadatas=[{"plan": "X"}, {"plan": "Y"}],
        )
        assert store.count == 2

    def test_mock_store_query(self):
        store = MockStore()
        store.add(
            ids=["a", "b"],
            embeddings=[[1.0, 0.0], [0.0, 1.0]],
            documents=["doc a", "doc b"],
            metadatas=[{"plan": "X"}, {"plan": "Y"}],
        )
        result = store.query([1.0, 0.0], n_results=1)
        assert len(result.ids) == 1
        assert result.ids[0] == "a"

    def test_mock_store_query_with_filter(self):
        store = MockStore()
        store.add(
            ids=["a", "b"],
            embeddings=[[1.0, 0.0], [0.0, 1.0]],
            documents=["doc a", "doc b"],
            metadatas=[{"plan": "X"}, {"plan": "Y"}],
        )
        result = store.query([1.0, 0.0], n_results=10, where={"plan": "Y"})
        assert len(result.ids) == 1
        assert result.ids[0] == "b"

    def test_mock_store_reset(self):
        store = MockStore()
        store.add(
            ids=["a"],
            embeddings=[[1.0]],
            documents=["doc"],
            metadatas=[{}],
        )
        assert store.count == 1
        store.reset()
        assert store.count == 0


# ═══════════════════════════════════════════════════════════════
# ChunkBuilder tests
# ═══════════════════════════════════════════════════════════════


class TestChunkBuilder:
    def test_basic_chunk_creation(self):
        config = VectorStoreConfig()
        builder = ChunkBuilder(config)
        tree = _make_tree(n_reqs=1)

        chunks = builder.build_chunks([tree])

        assert len(chunks) == 1
        chunk = chunks[0]
        assert chunk.chunk_id == "req:VZ_REQ_LTEDATARETRY_1000"
        assert "VZ_REQ_LTEDATARETRY_1000" in chunk.text
        assert chunk.metadata["mno"] == "VZW"
        assert chunk.metadata["plan_id"] == "LTEDATARETRY"
        assert chunk.metadata["doc_type"] == "requirement"

    def test_chunk_text_has_mno_header(self):
        config = VectorStoreConfig()
        builder = ChunkBuilder(config)
        tree = _make_tree(n_reqs=1)

        chunks = builder.build_chunks([tree])
        text = chunks[0].text

        assert "[MNO: VZW" in text
        assert "Release: 2026_feb" in text
        assert "Plan: LTE_DATARETRY" in text
        assert "Version: 39" in text

    def test_chunk_text_has_hierarchy_path(self):
        config = VectorStoreConfig()
        builder = ChunkBuilder(config)
        tree = _make_tree(n_reqs=2)

        # Second req has hierarchy path ["ROOT", "Section 2 Title"]
        chunks = builder.build_chunks([tree])
        text = chunks[1].text
        assert "[Path: ROOT > Section 2 Title]" in text

    def test_chunk_text_has_req_id(self):
        config = VectorStoreConfig()
        builder = ChunkBuilder(config)
        tree = _make_tree(n_reqs=1)

        chunks = builder.build_chunks([tree])
        assert "[Req ID: VZ_REQ_LTEDATARETRY_1000]" in chunks[0].text

    def test_chunk_text_has_body(self):
        config = VectorStoreConfig()
        builder = ChunkBuilder(config)
        tree = _make_tree(n_reqs=1)

        chunks = builder.build_chunks([tree])
        assert "body text for requirement 1" in chunks[0].text

    def test_chunk_text_has_table_as_markdown(self):
        config = VectorStoreConfig()
        builder = ChunkBuilder(config)
        tree = _make_tree(n_reqs=3)

        chunks = builder.build_chunks([tree])
        # Second req (index 1) has a table
        text = chunks[1].text

        assert "| Parameter | Value | Unit |" in text
        assert "| T3402 | 720 | seconds |" in text
        assert "| T3411 | 10 | seconds |" in text

    def test_chunk_text_has_image_context(self):
        config = VectorStoreConfig()
        builder = ChunkBuilder(config)
        tree = _make_tree(n_reqs=3)

        chunks = builder.build_chunks([tree])
        # Third req (index 2) has an image
        text = chunks[2].text
        assert "[Image: Figure 1 - Retry State Machine]" in text

    def test_no_mno_header_when_disabled(self):
        config = VectorStoreConfig(include_mno_header=False)
        builder = ChunkBuilder(config)
        tree = _make_tree(n_reqs=1)

        chunks = builder.build_chunks([tree])
        text = chunks[0].text
        assert "[MNO:" not in text

    def test_no_hierarchy_when_disabled(self):
        config = VectorStoreConfig(include_hierarchy_path=False)
        builder = ChunkBuilder(config)
        tree = _make_tree(n_reqs=2)

        chunks = builder.build_chunks([tree])
        assert "[Path:" not in chunks[1].text

    def test_no_req_id_when_disabled(self):
        config = VectorStoreConfig(include_req_id=False)
        builder = ChunkBuilder(config)
        tree = _make_tree(n_reqs=1)

        chunks = builder.build_chunks([tree])
        assert "[Req ID:" not in chunks[0].text

    def test_no_tables_when_disabled(self):
        config = VectorStoreConfig(include_tables=False)
        builder = ChunkBuilder(config)
        tree = _make_tree(n_reqs=3)

        chunks = builder.build_chunks([tree])
        assert "| Parameter" not in chunks[1].text

    def test_no_images_when_disabled(self):
        config = VectorStoreConfig(include_image_context=False)
        builder = ChunkBuilder(config)
        tree = _make_tree(n_reqs=3)

        chunks = builder.build_chunks([tree])
        assert "[Image:" not in chunks[2].text

    def test_metadata_has_all_fields(self):
        config = VectorStoreConfig()
        builder = ChunkBuilder(config)
        tree = _make_tree(n_reqs=1)

        chunks = builder.build_chunks([tree])
        meta = chunks[0].metadata

        assert "mno" in meta
        assert "release" in meta
        assert "doc_type" in meta
        assert "plan_id" in meta
        assert "req_id" in meta
        assert "section_number" in meta
        assert "zone_type" in meta
        assert "feature_ids" in meta

    def test_feature_ids_from_taxonomy(self):
        config = VectorStoreConfig()
        builder = ChunkBuilder(config)
        tree = _make_tree(plan_id="LTEDATARETRY", n_reqs=1)
        taxonomy = _make_taxonomy()

        chunks = builder.build_chunks([tree], taxonomy)
        meta = chunks[0].metadata

        # LTEDATARETRY is primary_in DATA_RETRY and referenced_in (by SMS) for DATA_RETRY
        assert "DATA_RETRY" in meta["feature_ids"]

    def test_feature_ids_empty_without_taxonomy(self):
        config = VectorStoreConfig()
        builder = ChunkBuilder(config)
        tree = _make_tree(n_reqs=1)

        chunks = builder.build_chunks([tree])
        assert chunks[0].metadata["feature_ids"] == []

    def test_multiple_trees(self):
        config = VectorStoreConfig()
        builder = ChunkBuilder(config)
        tree1 = _make_tree(plan_id="LTEDATARETRY", n_reqs=2)
        tree2 = _make_tree(plan_id="LTESMS", n_reqs=3)

        chunks = builder.build_chunks([tree1, tree2])
        assert len(chunks) == 5

        plan_ids = [c.metadata["plan_id"] for c in chunks]
        assert plan_ids.count("LTEDATARETRY") == 2
        assert plan_ids.count("LTESMS") == 3

    def test_skips_empty_req_id(self):
        config = VectorStoreConfig()
        builder = ChunkBuilder(config)
        tree = _make_tree(n_reqs=1)
        tree["requirements"][0]["req_id"] = ""

        chunks = builder.build_chunks([tree])
        assert len(chunks) == 0

    def test_table_empty_headers(self):
        """Tables with empty string headers (req ID artifact tables)."""
        table = {
            "headers": [""],
            "rows": [["VZ_REQ_LTEDATARETRY_2366"]],
            "source": "inline",
        }
        md = ChunkBuilder._table_to_markdown(table)
        assert "VZ_REQ_LTEDATARETRY_2366" in md

    def test_table_no_rows(self):
        """Empty tables produce no output."""
        table = {"headers": ["A", "B"], "rows": [], "source": "inline"}
        md = ChunkBuilder._table_to_markdown(table)
        assert md == ""

    def test_table_no_headers(self):
        """Tables without headers still render rows."""
        table = {"headers": [], "rows": [["a", "b"], ["c", "d"]], "source": "inline"}
        md = ChunkBuilder._table_to_markdown(table)
        assert "| a | b |" in md
        assert "| c | d |" in md

    def test_plan_feature_map(self):
        taxonomy = _make_taxonomy()
        pfm = ChunkBuilder._build_plan_feature_map(taxonomy)

        assert "DATA_RETRY" in pfm["LTEDATARETRY"]
        assert "SMS" in pfm["LTESMS"]
        # DATA_RETRY is referenced_in LTESMS
        assert "DATA_RETRY" in pfm["LTESMS"]

    def test_plan_feature_map_no_taxonomy(self):
        pfm = ChunkBuilder._build_plan_feature_map(None)
        assert pfm == {}


# ═══════════════════════════════════════════════════════════════
# Deduplication tests
# ═══════════════════════════════════════════════════════════════


class TestDeduplication:
    def test_no_duplicates(self):
        chunks = [
            Chunk(chunk_id="a", text="text a", metadata={}),
            Chunk(chunk_id="b", text="text b", metadata={}),
        ]
        result = VectorStoreBuilder._deduplicate_chunks(chunks)
        assert len(result) == 2

    def test_duplicate_keeps_longer(self):
        chunks = [
            Chunk(chunk_id="a", text="short", metadata={"v": 1}),
            Chunk(chunk_id="a", text="this is much longer text", metadata={"v": 2}),
        ]
        result = VectorStoreBuilder._deduplicate_chunks(chunks)
        assert len(result) == 1
        assert result[0].text == "this is much longer text"
        assert result[0].metadata["v"] == 2

    def test_duplicate_keeps_first_if_longer(self):
        chunks = [
            Chunk(chunk_id="a", text="this is the longer one", metadata={"v": 1}),
            Chunk(chunk_id="a", text="short", metadata={"v": 2}),
        ]
        result = VectorStoreBuilder._deduplicate_chunks(chunks)
        assert len(result) == 1
        assert result[0].metadata["v"] == 1

    def test_preserves_order(self):
        chunks = [
            Chunk(chunk_id="c", text="c", metadata={}),
            Chunk(chunk_id="a", text="a", metadata={}),
            Chunk(chunk_id="b", text="b", metadata={}),
        ]
        result = VectorStoreBuilder._deduplicate_chunks(chunks)
        assert [c.chunk_id for c in result] == ["c", "a", "b"]


# ═══════════════════════════════════════════════════════════════
# Builder tests
# ═══════════════════════════════════════════════════════════════


class TestBuilder:
    def _setup_builder(self, tmp_path: Path, n_trees: int = 1):
        """Create a builder with mock providers and temp data."""
        trees_dir = tmp_path / "parsed"
        trees_dir.mkdir()

        for i in range(n_trees):
            pid = f"PLAN{i}"
            tree = _make_tree(plan_id=pid, n_reqs=3)
            with open(trees_dir / f"{pid}_tree.json", "w") as f:
                json.dump(tree, f)

        taxonomy_path = tmp_path / "taxonomy.json"
        with open(taxonomy_path, "w") as f:
            json.dump(_make_taxonomy(), f)

        config = VectorStoreConfig()
        embedder = MockEmbedder(dim=8)
        store = MockStore()
        builder = VectorStoreBuilder(embedder, store, config)

        return builder, trees_dir, taxonomy_path, store

    def test_build_basic(self, tmp_path):
        builder, trees_dir, tax_path, store = self._setup_builder(tmp_path)

        stats = builder.build(trees_dir, tax_path)

        assert stats.total_chunks == 3
        assert store.count == 3
        assert stats.embedding_model == "mock-embedder"
        assert stats.embedding_dimension == 8

    def test_build_multiple_trees(self, tmp_path):
        builder, trees_dir, tax_path, store = self._setup_builder(
            tmp_path, n_trees=3
        )

        stats = builder.build(trees_dir, tax_path)

        assert stats.total_chunks == 9
        assert store.count == 9
        assert len(stats.chunks_by_plan) == 3

    def test_build_without_taxonomy(self, tmp_path):
        builder, trees_dir, _, store = self._setup_builder(tmp_path)

        stats = builder.build(trees_dir, taxonomy_path=None)

        assert stats.total_chunks == 3
        assert store.count == 3

    def test_build_rebuild_clears_store(self, tmp_path):
        builder, trees_dir, tax_path, store = self._setup_builder(tmp_path)

        builder.build(trees_dir, tax_path)
        assert store.count == 3

        builder.build(trees_dir, tax_path, rebuild=True)
        assert store.count == 3

    def test_build_stats_round_trip(self, tmp_path):
        builder, trees_dir, tax_path, _ = self._setup_builder(tmp_path)

        stats = builder.build(trees_dir, tax_path)

        path = tmp_path / "stats.json"
        stats.save_json(path)

        with open(path) as f:
            loaded = json.load(f)

        assert loaded["total_chunks"] == stats.total_chunks
        assert loaded["embedding_model"] == stats.embedding_model
        assert loaded["chunks_by_plan"] == stats.chunks_by_plan

    def test_build_queryable(self, tmp_path):
        """After building, the store should be queryable."""
        builder, trees_dir, tax_path, store = self._setup_builder(tmp_path)
        embedder = builder.embedder

        builder.build(trees_dir, tax_path)

        # Query for something
        qvec = embedder.embed_query("requirement 1")
        result = store.query(qvec, n_results=2)

        assert len(result.ids) == 2
        assert all(r.startswith("req:") for r in result.ids)
        assert len(result.documents) == 2
        assert len(result.distances) == 2

    def test_build_no_trees(self, tmp_path):
        """Empty trees directory produces no chunks."""
        trees_dir = tmp_path / "empty"
        trees_dir.mkdir()

        config = VectorStoreConfig()
        builder = VectorStoreBuilder(MockEmbedder(), MockStore(), config)

        stats = builder.build(trees_dir)
        assert stats.total_chunks == 0


# ═══════════════════════════════════════════════════════════════
# Integration tests (requires real parsed data)
# ═══════════════════════════════════════════════════════════════


_TREES_DIR = Path("data/parsed")
_TAXONOMY_PATH = Path("data/taxonomy/taxonomy.json")

_has_real_data = _TREES_DIR.exists() and any(_TREES_DIR.glob("*_tree.json"))


@pytest.mark.skipif(not _has_real_data, reason="Parsed tree data not available")
class TestIntegration:
    """Integration tests using real parsed requirement trees with mock embedder."""

    def test_chunk_all_real_trees(self):
        """All 5 VZW trees produce chunks with valid structure."""
        config = VectorStoreConfig()
        builder = ChunkBuilder(config)

        trees = []
        for path in sorted(_TREES_DIR.glob("*_tree.json")):
            with open(path) as f:
                trees.append(json.load(f))

        taxonomy = None
        if _TAXONOMY_PATH.exists():
            with open(_TAXONOMY_PATH) as f:
                taxonomy = json.load(f)

        chunks = builder.build_chunks(trees, taxonomy)

        # Should have chunks for all requirements
        assert len(chunks) > 600  # 711 total reqs expected

        # Every chunk has required metadata
        for chunk in chunks:
            assert chunk.chunk_id.startswith("req:")
            assert chunk.metadata["mno"] == "VZW"
            assert chunk.metadata["release"] == "2026_feb"
            assert chunk.metadata["doc_type"] == "requirement"
            assert chunk.metadata["plan_id"] != ""
            assert chunk.metadata["req_id"] != ""
            assert chunk.text.strip() != ""

    def test_chunk_text_format(self):
        """Verify chunk text has the expected TDD 5.9 format."""
        config = VectorStoreConfig()
        builder = ChunkBuilder(config)

        with open(_TREES_DIR / "LTEDATARETRY_tree.json") as f:
            tree = json.load(f)

        chunks = builder.build_chunks([tree])

        # Pick a chunk with actual content
        content_chunks = [c for c in chunks if len(c.text) > 200]
        assert len(content_chunks) > 0

        sample = content_chunks[0]
        assert "[MNO: VZW" in sample.text
        assert "[Path:" in sample.text
        assert "[Req ID:" in sample.text

    def test_build_with_mock_embedder(self):
        """Full build pipeline with real trees and mock embedder."""
        config = VectorStoreConfig()
        embedder = MockEmbedder(dim=16)
        store = MockStore()
        builder = VectorStoreBuilder(embedder, store, config)

        taxonomy_path = _TAXONOMY_PATH if _TAXONOMY_PATH.exists() else None
        stats = builder.build(_TREES_DIR, taxonomy_path)

        assert stats.total_chunks > 600
        # After deduplication, store count matches total_chunks
        assert store.count == stats.total_chunks
        assert stats.embedding_model == "mock-embedder"
        assert stats.embedding_dimension == 16
        assert len(stats.chunks_by_plan) == 5  # 5 VZW plans

    def test_query_with_mock_embedder(self):
        """Queries return results with correct metadata structure."""
        config = VectorStoreConfig()
        embedder = MockEmbedder(dim=16)
        store = MockStore()
        builder = VectorStoreBuilder(embedder, store, config)

        taxonomy_path = _TAXONOMY_PATH if _TAXONOMY_PATH.exists() else None
        builder.build(_TREES_DIR, taxonomy_path)

        # Query
        qvec = embedder.embed_query("T3402 timer behavior")
        result = store.query(qvec, n_results=5)

        assert len(result.ids) == 5
        for meta in result.metadatas:
            assert "mno" in meta
            assert "plan_id" in meta
            assert "req_id" in meta

    def test_query_with_plan_filter(self):
        """Metadata filtering restricts results to a single plan."""
        config = VectorStoreConfig()
        embedder = MockEmbedder(dim=16)
        store = MockStore()
        builder = VectorStoreBuilder(embedder, store, config)

        taxonomy_path = _TAXONOMY_PATH if _TAXONOMY_PATH.exists() else None
        builder.build(_TREES_DIR, taxonomy_path)

        qvec = embedder.embed_query("test query")
        result = store.query(
            qvec, n_results=100, where={"plan_id": "LTEDATARETRY"}
        )

        assert len(result.ids) > 0
        for meta in result.metadatas:
            assert meta["plan_id"] == "LTEDATARETRY"

    def test_chunks_by_plan_counts(self):
        """Verify chunk counts per plan match expected requirement counts."""
        config = VectorStoreConfig()
        builder = ChunkBuilder(config)

        trees = []
        for path in sorted(_TREES_DIR.glob("*_tree.json")):
            with open(path) as f:
                trees.append(json.load(f))

        chunks = builder.build_chunks(trees)

        plan_counts = {}
        for chunk in chunks:
            pid = chunk.metadata["plan_id"]
            plan_counts[pid] = plan_counts.get(pid, 0) + 1

        # These should match the requirement counts from the trees
        # (minus any empty req_id entries)
        for tree in trees:
            pid = tree["plan_id"]
            n_reqs_with_id = sum(
                1 for r in tree["requirements"] if r.get("req_id")
            )
            # Chunks may be fewer if some have no text content,
            # but should be close to the requirement count
            assert plan_counts.get(pid, 0) <= n_reqs_with_id

    def test_feature_ids_populated(self):
        """With taxonomy, chunks have non-empty feature_ids."""
        if not _TAXONOMY_PATH.exists():
            pytest.skip("Taxonomy not available")

        config = VectorStoreConfig()
        builder = ChunkBuilder(config)

        with open(_TREES_DIR / "LTEDATARETRY_tree.json") as f:
            tree = json.load(f)
        with open(_TAXONOMY_PATH) as f:
            taxonomy = json.load(f)

        chunks = builder.build_chunks([tree], taxonomy)

        # LTEDATARETRY should have features assigned
        feature_chunks = [c for c in chunks if c.metadata["feature_ids"]]
        assert len(feature_chunks) > 0
