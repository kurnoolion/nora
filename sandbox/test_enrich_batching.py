"""Tests for the batched doc-enrichment logic (strand sira-enrichment-pe):
per-plan packing under the dual token budget, taxonomy blocks, per-MNO
prompt resolution, req_id-keyed response parsing, and the run_batched
orchestration (retries, statuses) with fake LLM/writers."""

from __future__ import annotations

import asyncio
import json
import logging

import sandbox.enrich_batching as eb

TEMPLATE = ("Corpus overview here.\n{taxonomy_block}\n"
            "Requirements:\n{requirements}\n"
            'Output {{"<req_id>": ["..."]}} with at most {max_n} words each.')


class TestHelpers:
    def test_plan_of_composite_and_plain(self):
        assert eb.plan_of("**plan**: PA1 / Alpha Plan Doc\nbody") == "PA1"
        assert eb.plan_of("**plan**: PlanA\nbody") == "PlanA"
        assert eb.plan_of("no stamp") == ""

    def test_is_batched_template(self):
        assert eb.is_batched_template(TEMPLATE)
        assert not eb.is_batched_template("Requirement:\n{doc_text}\n{max_n}")

    def test_resolve_doc_prompt_picks_highest_version(self, tmp_path):
        for name in ("doc_requirement_MNOA_v02.txt",
                     "doc_requirement_MNOA_v03.txt",
                     "doc_requirement_MNOB_v09.txt"):
            (tmp_path / name).write_text("x")
        assert eb.resolve_doc_prompt(str(tmp_path), "MNOA").endswith("_v03.txt")
        assert eb.resolve_doc_prompt(str(tmp_path), "MNOC") is None
        assert eb.resolve_doc_prompt("", "MNOA") is None

    def test_taxonomy_block_and_fail_soft(self, tmp_path):
        (tmp_path / "PA1_features.json").write_text('{"primary_features": []}')
        block = eb.load_taxonomy_block(str(tmp_path), "PA1")
        assert "feature taxonomy" in block and '"primary_features"' in block
        assert eb.load_taxonomy_block(str(tmp_path), "PB2") == ""
        assert eb.load_taxonomy_block("", "PA1") == ""

    def test_compose_prompt_preserves_literal_braces(self):
        p = eb.compose_prompt(TEMPLATE, "TAX", ["R1"], ["text one"], 3)
        assert '### req_id: R1' in p and "TAX" in p
        assert '{"<req_id>": ["..."]}' in p        # {{ }} unescaped once
        assert "at most 3 words" in p


class TestPacking:
    def _reqs(self, n, chars):
        return [(f"R{i}", "x" * chars) for i in range(n)]

    def test_prompt_budget_closes_batches(self):
        cfg = eb.BatchConfig(prompt_cap_tokens=1000, context_tokens=100_000,
                             resp_tokens_per_req=1, chars_per_token=1.0)
        # header 100 -> text budget 900; each req 300 chars -> 301 tokens
        batches = eb.make_plan_batches("P", self._reqs(6, 300), 100, cfg)
        assert [len(b.ids) for b in batches] == [2, 2, 2]
        assert all(b.closed_by in ("prompt", "end") for b in batches)
        assert batches[0].prompt_tokens <= 1000
        # order preserved, nothing fragmented or lost
        assert [rid for b in batches for rid in b.ids] == [f"R{i}" for i in range(6)]

    def test_response_budget_binds_before_prompt(self):
        # tiny texts: prompt side would fit hundreds; reserve 100 tokens at
        # 10 tokens/req -> max 10 reqs per batch
        cfg = eb.BatchConfig(prompt_cap_tokens=10_000, context_tokens=10_100,
                             resp_tokens_per_req=10, chars_per_token=3.5)
        batches = eb.make_plan_batches("P", self._reqs(25, 20), 500, cfg)
        assert [len(b.ids) for b in batches] == [10, 10, 5]
        assert batches[0].closed_by == "response"

    def test_oversized_req_ships_solo(self):
        cfg = eb.BatchConfig(prompt_cap_tokens=500, context_tokens=50_000,
                             resp_tokens_per_req=1, chars_per_token=1.0)
        reqs = [("R0", "x" * 100), ("BIG", "x" * 9000), ("R2", "x" * 100)]
        batches = eb.make_plan_batches("P", reqs, 100, cfg)
        big = [b for b in batches if "BIG" in b.ids]
        assert len(big) == 1 and big[0].oversized and big[0].ids == ["BIG"]
        # neighbors unaffected
        assert [rid for b in batches for rid in b.ids] == ["R0", "BIG", "R2"]

    def test_group_by_plan(self):
        items = [("R1", "**plan**: PA\nx"), ("R2", "**plan**: PB\nx"),
                 ("R3", "**plan**: PA\nx")]
        groups = eb.group_reqs_by_plan(items)
        assert sorted(groups) == ["PA", "PB"]
        assert [r for r, _ in groups["PA"]] == ["R1", "R3"]


class TestParse:
    def test_clean_and_fenced_and_prefixed(self):
        want = {"R1": ["alpha beta"], "R2": ["gamma"]}
        for raw in (json.dumps(want),
                    f"```json\n{json.dumps(want)}\n```",
                    f"Thinking a bit...\n{json.dumps(want)}"):
            res = eb.parse_batch_response(raw, ["R1", "R2"])
            assert res.phrases_by_id == want and not res.missing and not res.error

    def test_missing_and_extra_ids(self):
        raw = json.dumps({"R1": ["a"], "R9": ["ghost"]})
        res = eb.parse_batch_response(raw, ["R1", "R2"])
        assert res.phrases_by_id == {"R1": ["a"]}
        assert res.missing == ["R2"] and res.extra == ["R9"]

    def test_garbage_response(self):
        res = eb.parse_batch_response("not json at all", ["R1"])
        assert res.missing == ["R1"] and res.error

    def test_non_string_phrases_dropped(self):
        raw = json.dumps({"R1": ["ok", None, {"x": 1}, "  ", 42]})
        res = eb.parse_batch_response(raw, ["R1"])
        assert res.phrases_by_id["R1"] == ["ok", "42"]


class _Sink:
    def __init__(self):
        self.enrich, self.kept, self.failed, self.batches = [], [], [], []

    async def w_enrich(self, rid, kept):
        self.enrich.append((rid, kept))

    async def w_kept(self, row):
        self.kept.append(row)

    async def w_failed(self, row):
        self.failed.append(row)

    async def w_batch(self, row):
        self.batches.append(row)


def _run(items, llm_call, cfg=None, taxonomy_dir=""):
    sink = _Sink()
    cfg = cfg or eb.BatchConfig(max_retries=1)
    summary = asyncio.run(eb.run_batched(
        items=items, template=TEMPLATE, max_n=3, taxonomy_dir=taxonomy_dir,
        cfg=cfg, llm_call=llm_call,
        filter_fn=lambda ph: ([p for p in ph if p != "drop"],
                              {"kept": len([p for p in ph if p != "drop"])}),
        write_enrich=sink.w_enrich, write_kept=sink.w_kept,
        write_failed=sink.w_failed, write_batch=sink.w_batch,
        logger=logging.getLogger("test")))
    return sink, summary


class TestRunBatched:
    ITEMS = [("R1", "**plan**: PA\nreq one"), ("R2", "**plan**: PA\nreq two")]

    def test_happy_path_writes_per_req_rows(self):
        async def llm(prompt, max_tokens):
            assert "### req_id: R1" in prompt and "### req_id: R2" in prompt
            return json.dumps({"R1": ["alpha"], "R2": ["beta", "drop"]})

        sink, summary = _run(self.ITEMS, llm)
        assert dict(sink.enrich) == {"R1": ["alpha"], "R2": ["beta"]}
        assert all(r["status"] == "ok" and r["batch_id"] for r in sink.kept)
        assert summary["enriched"] == 2 and summary["batches"] == 1
        assert summary["proposed"] == 3 and summary["filtered"] == 1

    def test_missing_req_requeued_then_recovered(self):
        calls = []

        async def llm(prompt, max_tokens):
            calls.append(prompt)
            if len(calls) == 1:
                return json.dumps({"R1": ["alpha"]})        # R2 missing
            return json.dumps({"R2": ["beta"]})             # retry round

        sink, summary = _run(self.ITEMS, llm)
        assert dict(sink.enrich) == {"R1": ["alpha"], "R2": ["beta"]}
        assert len(calls) == 2 and summary["requeued"] == 1
        assert not sink.failed

    def test_missing_req_fails_after_retries(self):
        async def llm(prompt, max_tokens):
            return json.dumps({"R1": ["alpha"]})            # R2 never answered

        sink, summary = _run(self.ITEMS, llm)
        statuses = {r["doc_id"]: r["status"] for r in sink.failed}
        assert statuses == {"R2": "missing_in_batch_response"}
        assert ("R1", ["alpha"]) in sink.enrich

    def test_batch_llm_error_requeues_then_fails(self):
        async def llm(prompt, max_tokens):
            raise RuntimeError("boom")

        sink, summary = _run(self.ITEMS, llm)
        # round 0 errors -> requeue; round 1 errors -> per-req failure rows
        assert {r["status"] for r in sink.failed} == {"batch_error"}
        assert len(sink.failed) == 2 and summary["errors"] == 2

    def test_taxonomy_block_reaches_prompt(self, tmp_path):
        (tmp_path / "PA_features.json").write_text('{"features": ["F1"]}')
        seen = []

        async def llm(prompt, max_tokens):
            seen.append(prompt)
            return json.dumps({"R1": ["alpha"], "R2": ["beta"]})

        _run(self.ITEMS, llm, taxonomy_dir=str(tmp_path))
        assert "feature taxonomy" in seen[0] and '"F1"' in seen[0]

    def test_plans_batch_separately(self):
        items = self.ITEMS + [("R3", "**plan**: PB\nreq three")]

        async def llm(prompt, max_tokens):
            ids = [ln.split(": ")[1] for ln in prompt.splitlines()
                   if ln.startswith("### req_id")]
            return json.dumps({rid: ["w"] for rid in ids})

        sink, summary = _run(items, llm)
        assert summary["batches"] == 2
        plans = {b["plan"] for b in sink.batches}
        assert plans == {"PA", "PB"}
