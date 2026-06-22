"""Per-cell vectorstore build + cell-store loader (D-DRAFT-11 slice 1)."""

from __future__ import annotations

from pathlib import Path

from core.src.parser.structural_parser import Requirement, RequirementTree
from core.src.pipeline.runner import PipelineContext
from core.src.pipeline.stages import run_vectorstore
from core.src.vectorstore.cell_loader import (
    FLAT_CELL,
    embedder_config_path,
    load_cell_stores,
)
from core.src.vectorstore.config import VectorStoreConfig


class _StubEmbedder:
    """Deterministic offline embedder (no model download)."""

    def __init__(self, dim: int = 8):
        self._dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float((hash(t) >> i) & 1) for i in range(self._dim)] for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return [float((hash(text) >> i) & 1) for i in range(self._dim)]

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return "stub-embedder"


# ── loader ───────────────────────────────────────────────────────────

def _seed_cell_dir(root: Path, mno: str, rel: str) -> None:
    d = root / mno / rel
    d.mkdir(parents=True, exist_ok=True)
    VectorStoreConfig(persist_directory=str(d)).save_json(d / "config.json")


class TestLoadCellStores:
    def test_per_cell(self, tmp_path):
        _seed_cell_dir(tmp_path, "VZW-OA", "Feb2026")
        _seed_cell_dir(tmp_path, "MNO-B", "Mar2025")
        stores = load_cell_stores(tmp_path)
        assert set(stores) == {("VZW-OA", "Feb2026"), ("MNO-B", "Mar2025")}

    def test_flat_fallback(self, tmp_path):
        VectorStoreConfig(persist_directory=str(tmp_path)).save_json(tmp_path / "config.json")
        stores = load_cell_stores(tmp_path)
        assert set(stores) == {FLAT_CELL}

    def test_per_cell_preferred_over_flat(self, tmp_path):
        # both a root config.json and per-cell dirs -> per-cell wins
        VectorStoreConfig(persist_directory=str(tmp_path)).save_json(tmp_path / "config.json")
        _seed_cell_dir(tmp_path, "MNO-B", "Mar2025")
        assert set(load_cell_stores(tmp_path)) == {("MNO-B", "Mar2025")}

    def test_empty(self, tmp_path):
        assert load_cell_stores(tmp_path) == {}
        assert load_cell_stores(tmp_path / "nope") == {}


class TestEmbedderConfigPath:
    """D-DRAFT-11: read the embedder config from a per-cell config when the flat
    one is absent (else callers default to sentence-transformers/HF)."""

    def test_prefers_flat_config(self, tmp_path):
        VectorStoreConfig(persist_directory=str(tmp_path)).save_json(tmp_path / "config.json")
        _seed_cell_dir(tmp_path, "VZW-OA", "Feb2026")
        assert embedder_config_path(tmp_path) == tmp_path / "config.json"

    def test_falls_back_to_per_cell_config(self, tmp_path):
        _seed_cell_dir(tmp_path, "MNO-B", "Mar2025")   # no flat config
        assert embedder_config_path(tmp_path) == tmp_path / "MNO-B" / "Mar2025" / "config.json"

    def test_none_when_no_config(self, tmp_path):
        assert embedder_config_path(tmp_path) is None
        assert embedder_config_path(tmp_path / "nope") is None


# ── per-cell build ───────────────────────────────────────────────────

class TestRunVectorstorePerCell:
    def test_builds_one_store_per_cell(self, tmp_path, monkeypatch):
        import core.src.vectorstore as vs
        monkeypatch.setattr(vs, "make_embedder", lambda config: _StubEmbedder())

        cells = [("MNO-A", "Feb2026"), ("MNO-B", "Mar2025")]
        for mno, rel in cells:
            (tmp_path / "input" / mno / rel).mkdir(parents=True)
            (tmp_path / "input" / mno / rel / "d.pdf").write_text("x", encoding="utf-8")
            pd = tmp_path / "out" / "parse" / mno / rel
            pd.mkdir(parents=True)
            RequirementTree(
                mno=mno, release=rel, plan_id="P",
                requirements=[Requirement(req_id="R-1", title="T",
                                          text="Device shall support X.")],
            ).save_json(pd / "d_tree.json")

        ctx = PipelineContext.standalone(env_dir=tmp_path)
        res = run_vectorstore(ctx)
        assert res.status == "OK", res
        assert res.stats["cells"] == 2
        for mno, rel in cells:
            assert (tmp_path / "out" / "vectorstore" / mno / rel / "config.json").is_file()
            assert (tmp_path / "out" / "vectorstore" / mno / rel / "build_stats.json").is_file()

        # the loader sees both cell stores
        assert set(load_cell_stores(tmp_path / "out" / "vectorstore")) == {
            ("MNO-A", "Feb2026"), ("MNO-B", "Mar2025")
        }


class _CapEmbedder(_StubEmbedder):
    max_input_chars = 8000


class _MemStore:
    count = 0

    def reset(self):
        pass

    def add(self, ids, embeddings, documents, metadatas):
        pass


class TestOversizeChunkWarning:
    """Builder logs the chunk_id (req:<id>) for chunks over the embedder limit."""

    def _tree(self, big_text: str) -> RequirementTree:
        return RequirementTree(
            mno="MNO-A", release="Feb2026", plan_id="P",
            requirements=[
                Requirement(req_id="VZ_REQ_BIG_1", section_number="1.1",
                            title="Big", text=big_text),
                Requirement(req_id="VZ_REQ_OK_2", section_number="1.2",
                            title="Ok", text="small body"),
            ],
        )

    def test_warns_with_req_id(self, tmp_path, caplog):
        from core.src.vectorstore.builder import VectorStoreBuilder
        from core.src.vectorstore.config import VectorStoreConfig

        td = tmp_path / "parse"
        td.mkdir()
        self._tree("X" * 20000).save_json(td / "P_tree.json")

        builder = VectorStoreBuilder(
            embedder=_CapEmbedder(), store=_MemStore(), config=VectorStoreConfig(),
        )
        with caplog.at_level("WARNING"):
            builder.build(trees_dir=td, rebuild=True)

        msgs = "\n".join(caplog.messages)
        assert "oversize chunk req:VZ_REQ_BIG_1" in msgs
        assert "req:VZ_REQ_OK_2" not in msgs  # under the limit → not listed
