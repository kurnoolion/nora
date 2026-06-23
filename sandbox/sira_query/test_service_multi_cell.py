"""End-to-end test of the multi-cell query path (multi-mno-sira D-DRAFT-7/10).

bm25x and the LLM are faked; the service module imports fine here, so the
real wiring (scope -> retrieve per cell -> rerank pool -> rank + provenance)
is exercised via FastAPI TestClient with synthetic cells.
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

import sandbox.sira_query.service as svc
from sandbox.sira_query.service import CellState


class _FakeBM25:
    """Duck-typed bm25x.BM25. search returns (idx, score) in doc order;
    expansion is a no-op (keep all phrases)."""
    def __init__(self, n: int):
        self._n = n

    def search_with_expansion(self, queries, expansions, k, weight):
        return [[(i, float(self._n - i)) for i in range(self._n)][:k]]

    def filter_query_expansion(self, query, phrases, max_df):
        return (phrases, [])

    def tokenize(self, p):
        return p.split()

    def enrich_batch(self, items):
        pass


def _cell(cellkey, docs):
    """docs: list of (doc_id, title, text). text may carry 'DOCSCORE:NN'
    to drive the fake reranker."""
    doc_ids = [d[0] for d in docs]
    return CellState(
        cell=cellkey,
        bm25=_FakeBM25(len(docs)),
        doc_ids=doc_ids,
        doc_id_to_idx={d: i for i, d in enumerate(doc_ids)},
        corpus_by_id={d[0]: {"title": d[1], "text": d[2]} for d in docs},
        max_df=100,
    )


async def _fake_llm_call(client, prompt, max_tokens, temperature,
                         base_url=None, model=None, api_key=None):
    # Rerank: echo the DOCSCORE embedded in the doc text as {"score": NN}.
    m = re.search(r"DOCSCORE:(\d+)", prompt)
    return json.dumps({"score": int(m.group(1)) if m else 0})


VZW_F = ("VZW", "Feb2026")
VZW_O = ("VZW", "Oct2025")
TMO_J = ("TMO", "Jan2026")


def _setup(monkeypatch, cells):
    monkeypatch.setattr(svc, "_cells", cells)
    monkeypatch.setattr(svc, "_llm_call", _fake_llm_call)
    monkeypatch.setattr(svc, "_QUERY_ENRICH_ENABLED", False)
    monkeypatch.setattr(svc, "_RERANK_ENABLED", True)
    monkeypatch.setattr(svc, "_rerank_prompt_template", "{query} {document}")
    monkeypatch.setattr(svc, "_RERANK_TOP_N", 10)
    return TestClient(svc.app)


@pytest.fixture
def client(monkeypatch):
    return _setup(monkeypatch, {
        VZW_F: _cell(VZW_F, [("req:v1", "v1", "DOCSCORE:40"),
                             ("req:v2", "v2", "DOCSCORE:20")]),
        TMO_J: _cell(TMO_J, [("req:t1", "t1", "DOCSCORE:95"),
                             ("req:t2", "t2", "DOCSCORE:30")]),
    })


def _post(client, q, top_k=10):
    r = client.post("/sira-query", json={"query": q, "top_k": top_k})
    assert r.status_code == 200, r.text
    return r.json()


def test_cross_mno_routes_to_multi_cell_with_provenance(client):
    body = _post(client, "compare vzw and tmo VoWiFi")
    assert body["mode"] == "multi-cell"
    assert body["resolved_cells"] == ["TMO__Jan2026", "VZW__Feb2026"]
    # ranked by rerank score (t1=95, v1=40, t2=30, v2=20)
    assert [r["req_id"] for r in body["results"]] == \
        ["req:t1", "req:v1", "req:t2", "req:v2"]
    top = body["results"][0]
    assert top["mno"] == "TMO" and top["release"] == "Jan2026"
    assert body["results"][1]["mno"] == "VZW"


def test_mno_scoped_query_hits_one_cell(client):
    body = _post(client, "what does tmobile support")
    assert body["resolved_cells"] == ["TMO__Jan2026"]
    assert {r["mno"] for r in body["results"]} == {"TMO"}


def test_no_mno_named_expands_to_all_cells(client):
    body = _post(client, "what 5G bands are supported")
    assert set(body["resolved_cells"]) == {"TMO__Jan2026", "VZW__Feb2026"}


def test_unresolved_scope_surfaced(client):
    r = client.post("/sira-query", json={"query": "what does at&t require"})
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert ["ATT", "*"] in detail["requested_unresolved"]
    assert "VZW__Feb2026" in detail["available_cells"]


def test_release_diff_same_reqid_both_cells(monkeypatch):
    c = _setup(monkeypatch, {
        VZW_O: _cell(VZW_O, [("req:FOO:5.1", "x", "DOCSCORE:70")]),
        VZW_F: _cell(VZW_F, [("req:FOO:5.1", "x", "DOCSCORE:80")]),
    })
    body = _post(c, "how did vzw change from Oct 2025 to Feb 2026")
    assert body["resolved_cells"] == ["VZW__Feb2026", "VZW__Oct2025"]
    assert len(body["results"]) == 2                       # not deduped
    assert {r["release"] for r in body["results"]} == {"Oct2025", "Feb2026"}
    assert body["results"][0]["release"] == "Feb2026"      # 80 > 70


def test_empty_query_rejected(client):
    r = client.post("/sira-query", json={"query": "   "})
    assert r.status_code == 400


def test_healthz_reports_cell_state(client):
    # D-DRAFT-7: in multi-cell mode healthz is cell-aware (ok=true even with no
    # legacy _bm25; mode + cells + aggregated corpus_size across cells).
    body = client.get("/healthz").json()
    assert body["ok"] is True
    assert body["mode"] == "multi-cell"
    assert body["cells"] == ["TMO__Jan2026", "VZW__Feb2026"]   # sorted
    assert body["corpus_size"] == 4        # 2 + 2 across both cells
    assert body["n_req_rows"] == 4
    assert body["cells_load_error"] is None


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


class _FakeClient:
    def __init__(self, payload):
        self._p = payload
        self.last = None

    async def post(self, url, json=None, headers=None):
        self.last = (url, json, headers)
        return _FakeResp(self._p)


def test_rerank_bulk_tei(monkeypatch):
    import asyncio
    from sandbox.sira_query.fusion import Candidate
    monkeypatch.setattr(svc, "_cells", {VZW_F: _cell(VZW_F, [
        ("req:a", "A", "x"), ("req:b", "B", "y")])})
    monkeypatch.setattr(svc, "_RERANK_LLM_URL", "http://tei:8080")
    cands = [Candidate(VZW_F, "req:a", 1.0), Candidate(VZW_F, "req:b", 1.0)]
    client = _FakeClient([{"index": 0, "score": 0.9}, {"index": 1, "score": 0.2}])
    scores = asyncio.run(svc._rerank_bulk(client, "q", cands, "tei"))
    assert scores[("VZW", "Feb2026", "req:a")] == 90.0    # 0.9 → 0-100
    assert scores[("VZW", "Feb2026", "req:b")] == 20.0
    assert client.last[0] == "http://tei:8080/rerank"     # no /v1 for TEI
    assert client.last[1]["texts"] == ["A\n\nx", "B\n\ny"]


def test_rerank_bulk_openai_dedicated(monkeypatch):
    import asyncio
    from sandbox.sira_query.fusion import Candidate
    monkeypatch.setattr(svc, "_cells", {VZW_F: _cell(VZW_F, [("req:a", "A", "x")])})
    monkeypatch.setattr(svc, "_RERANK_LLM_URL", "http://dgx:8000")
    monkeypatch.setattr(svc, "_RERANK_LLM_MODEL", "bge-reranker")
    cands = [Candidate(VZW_F, "req:a", 1.0)]
    client = _FakeClient({"results": [{"index": 0, "relevance_score": 0.75}]})
    scores = asyncio.run(svc._rerank_bulk(client, "q", cands, "openai-dedicated"))
    assert scores[("VZW", "Feb2026", "req:a")] == 75.0
    assert client.last[0] == "http://dgx:8000/v1/rerank"   # /v1/rerank for vLLM
    assert client.last[1]["model"] == "bge-reranker"


def test_rerank_bulk_no_url_scores_zero(monkeypatch):
    import asyncio
    from sandbox.sira_query.fusion import Candidate
    monkeypatch.setattr(svc, "_cells", {VZW_F: _cell(VZW_F, [("req:a", "A", "x")])})
    monkeypatch.setattr(svc, "_RERANK_LLM_URL", "")
    cands = [Candidate(VZW_F, "req:a", 1.0)]
    scores = asyncio.run(svc._rerank_bulk(_FakeClient(None), "q", cands, "tei"))
    assert scores == {("VZW", "Feb2026", "req:a"): 0.0}


def test_healthz_aggregates_per_cell_doc_enrich(monkeypatch):
    # doc enrichment is per-cell (CellState); healthz must aggregate it, not
    # report the legacy global (which _load_state — skipped in multi-cell —
    # would set). Otherwise it falsely reads "(none) / 0" even when cells are
    # enriched.
    a = _cell(VZW_F, [("req:v1", "v1", "x")])
    a.doc_enrich_source = "/db/VZW__Feb2026/runs/doc-enrich/enrich-1/enrichments.kept.jsonl"
    a.doc_enrich_applied_docs = 10
    b = _cell(TMO_J, [("req:t1", "t1", "x")])
    b.doc_enrich_source = "/db/TMO__Jan2026/runs/doc-enrich/enrich-2/enrichments.kept.jsonl"
    b.doc_enrich_applied_docs = 7
    client = _setup(monkeypatch, {VZW_F: a, TMO_J: b})

    body = client.get("/healthz").json()
    assert body["doc_enrich_applied_docs"] == 17        # summed across cells
    assert set(body["doc_enrich_source"]) == {"TMO__Jan2026", "VZW__Feb2026"}
    assert "enrich-1" in body["doc_enrich_source"]["VZW__Feb2026"]


def test_load_prompts_from_run_dir(tmp_path, monkeypatch):
    # D-DRAFT-7: service-level prompts must load in multi-cell mode (where
    # _load_state is skipped). _load_prompts resolves the run dir under a cell
    # base and loads both prompt templates.
    monkeypatch.setattr(svc, "_USE_LATEST", True)
    monkeypatch.setattr(svc, "_QUERY_ENRICH_RUN", "")
    monkeypatch.setattr(svc, "_RERANK_RUN", "")
    base = tmp_path / "VZW__Feb2026"
    (base / "runs" / "query-enrich" / "r1").mkdir(parents=True)
    (base / "runs" / "query-enrich" / "r1" / "query_prompt.txt").write_text("QP", encoding="utf-8")
    (base / "runs" / "rerank" / "r1").mkdir(parents=True)
    (base / "runs" / "rerank" / "r1" / "prompt.txt").write_text("RP", encoding="utf-8")

    svc._load_prompts(base)
    assert svc._query_prompt_template == "QP"
    assert svc._rerank_prompt_template == "RP"


def test_healthz_single_dataset_when_no_cells(monkeypatch):
    # No cells loaded → legacy single-dataset reporting (ok reflects _bm25).
    monkeypatch.setattr(svc, "_cells", {})
    monkeypatch.setattr(svc, "_bm25", None)
    body = TestClient(svc.app).get("/healthz").json()
    assert body["mode"] == "single-dataset"
    assert body["ok"] is False
    assert body["cells"] == []
