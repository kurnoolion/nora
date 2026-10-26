"""Release-change synthesis (core/src/query/release_diff.py, strand
rel-history): release resolution, batches per plan and kind, the three
prompt shapes, reassembly in plan order, streaming partials, cancel, and
a counts-only compact block."""

from __future__ import annotations

import re
import threading

import pytest

from core.src.pipeline.error_codes import PipelineError
from core.src.query.release_diff import (
    ReleaseDiffConfig,
    build_batches,
    compact_block,
    normalize_release_ref,
    prompt_for,
    reassemble,
    resolve_release_pair,
    synthesize_release_diff,
)

RELS = ["Jan2031", "Apr2031", "Jul2031"]


def _req(rid, text, sec="1.1", title="", prio="Mandatory"):
    return {"req_id": rid, "section": sec, "title": title or f"title {rid}", "text": text,
            "priority": prio, "applicability": ["Phone"]}


def _pair(rid, old, new, sec="1.1", kinds=None, prio_new="Mandatory"):
    return {"req_id": rid, "section": sec, "kinds": kinds or [],
            "old": {"title": f"title {rid}", "text": old, "priority": "Mandatory", "applicability": ["Phone"]},
            "new": {"title": f"title {rid}", "text": new, "priority": prio_new, "applicability": ["Phone"]}}


def payload():
    return {
        "mno": "mno-q", "from": "Apr2031", "to": "Jul2031",
        "plans": {
            "PLANA": {"plan_name": "Plan A", "present": {"from": True, "to": True},
                      "changed": [_pair("R1", "retry after 30 s", "retry after 31 s"),
                                  _pair("R2", "log every error", "log every error", prio_new="Optional")],
                      "added": [_req("R9", "new timer T9 shall be 5 s", sec="1.9")],
                      "removed": [_req("R6", "timer T3 shall be 40 s", sec="2.3")],
                      "renamed": [{"kind": "section", "old_id": "2", "new_id": "3", "section": "3"}],
                      "renumbered": [{"req_id": "R5", "section": "3.2", "from": "2.2", "detail": "parent"}],
                      "moved": []},
            "PLANB": {"plan_name": "Plan B", "present": {"from": True, "to": True},
                      "changed": [_pair("B1", "a", "b"), _pair("B2", "c", "d"), _pair("B3", "e", "f")],
                      "added": [], "removed": [], "renamed": [], "renumbered": [],
                      "moved": [{"req_id": "B4", "section": "4.1", "from": "2.1"}]},
        },
        "absent_plans": {"PLANX": {"absent_in": "Jul2031"}},
        "counts": {"changed": 5, "added": 1, "removed": 1, "renamed": 1, "renumbered": 1,
                   "moved": 1, "plans": 2, "absent_plans": 1},
    }


class EchoProvider:
    """Answers with the ids it was asked about; records calls."""
    def __init__(self, fail_on: str = "", slow: float = 0.0):
        self.calls: list[tuple[str, str]] = []
        self.fail_on, self.slow = fail_on, slow
        self.last_call_stats = {"model": "echo"}

    def complete(self, prompt: str, system: str = "", temperature: float = 0.0,
                 max_tokens: int = 0) -> str:
        import time
        self.calls.append((system, prompt))
        if self.slow:
            time.sleep(self.slow)
        ids = re.findall(r"\[([A-Z]+\d+)\]", prompt)
        if self.fail_on and self.fail_on in ids:
            raise RuntimeError("declined")
        kind = "changed" if "side by side" in prompt else ("removed" if "absent" in prompt else "added")
        return f"{kind}: " + ", ".join(dict.fromkeys(ids))


def test_normalize_release_ref():
    assert normalize_release_ref("April 2031") == "Apr2031"
    assert normalize_release_ref("apr 2031") == "Apr2031"
    assert normalize_release_ref("2031-apr") == "Apr2031"
    assert normalize_release_ref("Apr2031") == "Apr2031"
    assert normalize_release_ref("latest") == "latest" and normalize_release_ref("N") == "latest"
    assert normalize_release_ref("previous") == "previous" and normalize_release_ref("n-1") == "previous"
    assert normalize_release_ref("whenever") == "" and normalize_release_ref("") == ""


def test_resolve_release_pair():
    assert resolve_release_pair([], RELS) == ("Apr2031", "Jul2031")
    assert resolve_release_pair(["latest"], RELS) == ("Apr2031", "Jul2031")
    assert resolve_release_pair(["Apr2031"], RELS) == ("Jan2031", "Apr2031")
    assert resolve_release_pair(["Jan 2031"], RELS) == ("Jan2031", "Apr2031")     # oldest: it and the next
    assert resolve_release_pair(["Jul2031", "Jan2031"], RELS) == ("Jan2031", "Jul2031")
    assert resolve_release_pair(["previous", "latest"], RELS) == ("Apr2031", "Jul2031")
    with pytest.raises(PipelineError) as e1:
        resolve_release_pair([], ["Jan2031"])
    assert e1.value.code == "RHD-E001"
    with pytest.raises(PipelineError) as e2:
        resolve_release_pair(["Dec2099"], RELS)
    assert e2.value.code == "RHD-E002"


def test_batches_are_per_plan_and_kind_in_plan_order():
    b = build_batches(payload(), max_bytes=4096)
    groups = [x.group.replace("\x00", "/") for x in b]
    assert groups == ["PLANA/pair", "PLANA/removed", "PLANA/added", "PLANB/pair"]
    assert [x.index for x in b] == [0, 1, 2, 3] and not any(x.oversized for x in b)
    # a budget that fits exactly one PLANA pair (512 bytes of overhead + the pair's size)
    from core.src.query.release_diff import _pair_text
    from core.src.llm.batching import utf8_size
    one = 512 + max(utf8_size(_pair_text(c)) for c in payload()["plans"]["PLANA"]["changed"]) + 8
    small = build_batches(payload(), max_bytes=one)
    groups = [x.group.replace("\x00", "/") for x in small]
    assert groups[:3] == ["PLANA/pair", "PLANA/pair", "PLANA/removed"]
    assert all(len(x.items) == 1 and not x.oversized for x in small[:2])


def test_prompts_have_the_three_shapes_and_both_texts():
    pl = payload()
    b = build_batches(pl, max_bytes=4096)
    system, pair = prompt_for(b[0], pl)
    assert "cite every requirement id" in system
    assert "side by side" in pair and "Plan: PLANA (Plan A)" in pair and "Apr2031 → Jul2031" in pair
    assert "retry after 30 s" in pair and "retry after 31 s" in pair and "[R1]" in pair
    assert "priority: Optional" in pair and "priority: Mandatory" in pair
    _, removed = prompt_for(b[1], pl)
    assert "absent from" in removed and "[R6]" in removed and "timer T3" in removed
    _, added = prompt_for(b[2], pl)
    assert "new in the current release" in added and "[R9]" in added


def test_synthesize_reassembles_in_plan_order_and_streams_partials():
    pl = payload()
    prov = EchoProvider()
    partials: list[dict] = []
    out = synthesize_release_diff(pl, prov, config=ReleaseDiffConfig(max_batch_bytes=4096, parallel=2),
                                  on_partial=partials.append)
    assert [p["plan_id"] for p in out["plans"]] == ["PLANA", "PLANB"]
    a = out["plans"][0]
    assert [s["kind"] for s in a["sections"]] == ["pair", "removed", "added"]
    assert a["sections"][0]["text"] == "changed: R1, R2" and a["sections"][0]["status"] == "ok"
    assert a["sections"][1]["text"] == "removed: R6" and a["sections"][2]["text"] == "added: R9"
    assert a["notes"] == ["1 requirement(s) renumbered (1 parent); content unchanged.",
                          "Section(s) renumbered: 2 → 3."]
    assert out["plans"][1]["notes"] == ["Moved: B4 from 2.1 to 4.1."]
    assert out["absent_plans"] == [{"plan_id": "PLANX", "absent_in": "Jul2031"}]
    assert out["run"]["batches"] == 4 and out["run"]["done"] == 4 and out["run"]["failed"] == 0
    assert not out["cancelled"]
    assert len(partials) == 4 and sorted(p["done"] for p in partials) == [1, 2, 3, 4]
    assert all(p["total"] == 4 for p in partials)
    assert len(prov.calls) == 4 and all(s for s, _ in prov.calls)
    block = out["compact"]
    assert block.startswith("RHD mno=mno-q from=Apr2031 to=Jul2031 plans=2 absent=1 pairs=5")
    assert "retry" not in block and "timer" not in block and "batches=4" in block


def test_failed_batch_is_marked_and_the_rest_answer():
    out = synthesize_release_diff(payload(), EchoProvider(fail_on="R6"),
                                  config=ReleaseDiffConfig(max_batch_bytes=4096, parallel=1))
    secs = out["plans"][0]["sections"]
    assert secs[1]["status"] == "error" and secs[1]["error"] == "RHD-E004" and secs[1]["ids"] == ["R6"]
    assert secs[0]["status"] == "ok" and out["run"]["failed"] == 1
    assert "failed=1" in out["compact"]


def test_cancel_marks_unstarted_batches():
    cancel = threading.Event()
    prov = EchoProvider(slow=0.02)
    out = synthesize_release_diff(payload(), prov,
                                  config=ReleaseDiffConfig(max_batch_bytes=4096, parallel=1),
                                  cancel=cancel, on_partial=lambda p: cancel.set())
    assert out["cancelled"] and out["run"]["done"] == 1 and out["run"]["skipped"] == 3
    statuses = [s["status"] for p in out["plans"] for s in p["sections"]]
    assert statuses.count("ok") == 1 and statuses.count("cancelled") == 3
    assert "cancelled=1" in out["compact"]


def test_config_from_env():
    c = ReleaseDiffConfig.from_env({})
    assert c.max_batch_bytes == 32768 and c.parallel == 4 and c.enabled is False
    c = ReleaseDiffConfig.from_env({"NORA_HISTORY_URL": "http://x", "NORA_HISTORY_DIFF_PARALLEL": "2",
                                    "NORA_HISTORY_DIFF_MAX_BATCH_BYTES": "4096"})
    assert c.enabled and c.parallel == 2 and c.max_batch_bytes == 4096
    assert ReleaseDiffConfig.from_env({"NORA_HISTORY_DIFF_ENABLED": "0", "NORA_HISTORY_URL": "u"}).enabled is False


def test_reassemble_without_outcomes_marks_everything_cancelled():
    pl = payload()
    b = build_batches(pl, max_bytes=4096)
    out = reassemble(pl, b, {}, cancelled=True)
    assert all(s["status"] == "cancelled" for p in out["plans"] for s in p["sections"])
    assert "RHD" in compact_block({**out, "run": {}})
