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


def test_release_change_question_rule():
    from core.src.query.release_diff import is_release_change_question
    from core.src.query.schema import QueryIntent, QueryType
    rd = QueryIntent(raw_query="", query_type=QueryType.RELEASE_DIFF)
    assert is_release_change_question("what changed?", rd)
    one = QueryIntent(raw_query="", mnos=["mno-a"], query_type=QueryType.CROSS_MNO_COMPARISON)
    assert is_release_change_question(
        "What changed in the latest release of mno-a compared to the previous one?", one)
    assert is_release_change_question("What is new in the latest mno-a release?", one)
    assert not is_release_change_question("Compare mno-a retry timers", one)
    two = QueryIntent(raw_query="", mnos=["mno-a", "mno-b"], query_type=QueryType.CROSS_MNO_COMPARISON)
    assert not is_release_change_question("What changed between mno-a and mno-b latest releases?", two)
    gen = QueryIntent(raw_query="", query_type=QueryType.GENERAL)
    assert not is_release_change_question("What is the retry timer?", gen)


def test_system_prompt_carries_the_reasoning_marker_only_when_enabled():
    from core.src.query.release_diff import FINAL_ANSWER_MARKER, system_prompt
    assert FINAL_ANSWER_MARKER not in system_prompt(sentinel=False)
    assert FINAL_ANSWER_MARKER in system_prompt(sentinel=True)
    assert "cite every requirement id" in system_prompt(sentinel=True)


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


def test_working_text_detection_and_strip():
    from core.src.query.release_diff import looks_like_working_text, strip_working_text
    clean = "[R1] The retry timer moved from 30 s to 31 s."
    assert not looks_like_working_text(clean) and strip_working_text(clean, ["R1"]) == clean
    working = ("Okay, let me tackle this. First, I'll go through each requirement.\n"
               "I need to make sure I cite the ids.\n\n"
               "Summary of changes:\n[R1] The retry timer moved from 30 s to 31 s.\n[R2] Priority changed.")
    assert looks_like_working_text(working)
    out = strip_working_text(working, ["R1", "R2"])
    assert out.startswith("Summary of changes:") and "[R2] Priority changed." in out
    assert "Okay" not in out
    no_ids = "The user wants a summary.\n\nHere is the summary.\n\nNothing changed in this plan."
    assert strip_working_text(no_ids, []) == "Nothing changed in this plan."


def test_working_text_reply_is_retried_then_stripped(caplog):
    import logging
    pl = payload()

    class Thinking(EchoProvider):
        def complete(self, prompt, system="", temperature=0.0, max_tokens=0):
            self.calls.append((system, prompt))
            ids = re.findall(r"\[([A-Z]+\d+)\]", prompt)
            if "Output only the final answer" in prompt:
                return "Okay, let me redo this.\n\nFinal: [" + "] [".join(dict.fromkeys(ids)) + "]"
            return "Okay, let me tackle this.\n\n[" + "] [".join(dict.fromkeys(ids)) + "]"

    prov = Thinking()
    with caplog.at_level(logging.INFO, logger="core.src.query.release_diff"):
        out = synthesize_release_diff(pl, prov, config=ReleaseDiffConfig(max_batch_bytes=4096, parallel=1))
    fired = [m for m in caplog.messages if m.startswith("RHD working text:")]
    assert len(fired) == 4 and all("marks=opener:okay,let me,okay," in m for m in fired)
    assert all("tackle" not in m and "redo" not in m for m in fired)      # the list's words only
    texts = [s["text"] for p in out["plans"] for s in p["sections"]]
    assert texts and all(not t.startswith("Okay") for t in texts)
    assert all(t.startswith("Final:") for t in texts)
    assert len(prov.calls) == 8                                 # 4 batches, each retried once
    assert out["run"]["retried"] == 4 and out["run"]["stripped"] == 4
    assert "retried=4 stripped=4" in out["compact"]
    assert "batch_ms_p50=" in out["compact"] and "errors=" not in out["compact"]
    assert set(out["run"]["batch_ms"]) == {"p50", "p95", "max"}


def test_failed_batch_error_class_in_compact_block():
    out = synthesize_release_diff(payload(), EchoProvider(fail_on="R6"),
                                  config=ReleaseDiffConfig(max_batch_bytes=4096, parallel=1))
    assert out["run"]["errors"] == ["RuntimeError"] and "errors=RuntimeError" in out["compact"]



def test_reasoning_tail_is_detected_and_cut_on_the_same_line():
    from core.src.query.release_diff import looks_like_working_text, strip_working_text
    tail = ("line, but the final answer should only include the added ID and the summary. "
            "I should avoid restating unchanged text and not invent any IDs. The answer needs "
            "to be in plain prose. Let me structure the final answer accordingly. "
            "Added requirement: [R9] a new timer for the retry path.")
    assert looks_like_working_text(tail)
    out = strip_working_text(tail, ["R9"])
    assert out == "Added requirement: [R9] a new timer for the retry path."
    # the same tail with the answer on its own paragraph
    out2 = strip_working_text(tail.replace(" Added requirement:", "\n\nAdded requirement:"), ["R9"])
    assert out2.startswith("Added requirement: [R9]")
    # a genuine answer with one incidental phrase is left alone
    answer = "For [R1], the timer moved from 30 s to 31 s; the user wants the new value."
    assert not looks_like_working_text(answer) and strip_working_text(answer, ["R1"]) == answer


def test_working_text_after_a_draft_is_cut_and_the_draft_dropped():
    """The reply drafts the answer (ids and all), reasons about it, then
    answers again: the working block sits after the first id, and the
    answer that follows it is kept on its own."""
    from core.src.query.release_diff import looks_like_working_text, strip_working_text
    draft_then_answer = (
        "The added requirement is [R9], which adds a retry timer. I'll give the id, "
        "followed by the final answer. The final answer should be in plain prose, listing "
        "the added ID and then the summary. I need to ensure that I don't restate unchanged "
        "text. Also, I must not invent any IDs. The user provided only one new requirement, "
        "so the answer should reflect that. Putting it all together: The added requirement "
        "is [R9] which adds a retry timer.\n\nAdded requirement: [R9] adds a retry timer.")
    assert looks_like_working_text(draft_then_answer)
    assert strip_working_text(draft_then_answer, ["R9"]) == "Added requirement: [R9] adds a retry timer."
    # a long draft, well past the opening paragraph, then the block, then the list
    long_draft = ("[R1] [R2] [R3] were removed, which drops three behaviours from the plan "
                  + "and leaves the profile thinner. " * 30
                  + "Putting it all together, the final answer will have the list of IDs "
                  "followed by the summary sentence. I need to ensure that the output is in "
                  "plain prose, with the IDs in square brackets as given.\n\n"
                  "Removed requirement IDs: [R1] [R2] [R3]. The plan loses three behaviours.")
    assert looks_like_working_text(long_draft)
    assert strip_working_text(long_draft, ["R1", "R2", "R3"]).startswith("Removed requirement IDs: [R1]")
    # working text between two answer parts with different ids: both parts stay
    middle = ("[R1] x changed. [R2] y changed.\n\nWait, I need to make sure I covered "
              "everything. Let me check again.\n\n[R3] z changed.")
    assert strip_working_text(middle, ["R1", "R2", "R3"]) == "[R1] x changed. [R2] y changed.\n\n[R3] z changed."
    # an inline cut keeps the id's bracket
    assert strip_working_text("Okay, let me list them: [R1] x. [R2] y.", ["R1", "R2"]) == "[R1] x. [R2] y."
    # two incidental phrases far apart in a genuine answer do not fire
    spread = ("[R1] the device must make sure that the timer runs." + " More detail here." * 60
              + " [R2] the user wants a clear prompt before reset.")
    assert not looks_like_working_text(spread)


def test_long_reasoning_before_the_first_id_is_cut_whole():
    """Round-8 shape: paragraphs of reasoning with the working phrases
    spread far apart, no id until a 'Let me structure it as:' draft, then
    the answer again. Everything before the answer goes, the draft too."""
    from core.src.query.release_diff import looks_like_working_text, strip_working_text
    reply = ("It has detailed requirements about the retry path, and the old text says 30 s. "
             + "The new text adds more detail about the path. " * 20
             + "I need to make sure to cite the requirement ID exactly as given. "
             + "The old and new texts differ in the timer value and the added clause. " * 8
             + "The answer should state the change plainly. "
             + "There is also a note on the clause ordering. " * 8
             + "I should avoid restating unchanged text. " + "The difference is small. " * 20
             + "So, the final answer will name the id and the change.\n\n"
             "Let me structure it as: The requirement [R4] now sets the timer to 31 s.\n\n"
             "The requirement [R4] now sets the timer to 31 s and adds a clause on ordering.")
    assert looks_like_working_text(reply)
    assert strip_working_text(reply, ["R4"]) == \
        "The requirement [R4] now sets the timer to 31 s and adds a clause on ordering."
    # both shapes in one reply: an opener, a draft with the id, a block, the answer
    mixed = ("I'll begin with the draft. [R1] draft text. Putting it all together, the final "
             "answer should be plain. I need to be concise.\n\n[R1] final text.")
    assert strip_working_text(mixed, ["R1"]) == "[R1] final text."
    # a comma opener counts as a phrase
    assert strip_working_text("Okay, here they are: [R1] x. [R2] y.", ["R1", "R2"]) == "[R1] x. [R2] y."


def test_short_block_after_ids_with_meta_phrases_is_cut():
    """Round-9 shape: an answer with ids, then a 120-character block whose
    phrases are about the answer's form ("followed by the answer", "the
    summary should", "as per the user's instruction"), then the answer
    again. The block and the draft before it go."""
    from core.src.query.release_diff import looks_like_working_text, strip_working_text
    reply = ("For [R1], the retry timer is gone. For [R2], the fallback clause is gone. "
             "For [R3], the audit note is gone. followed by the answer. Also, the summary "
             "should be one sentence as per the user's instruction. Putting it all together: "
             "The removed requirement IDs are [R1], [R2], and [R3]; the plan loses its retry "
             "timer, fallback clause and audit note.")
    assert looks_like_working_text(reply)
    out = strip_working_text(reply, ["R1", "R2", "R3"])
    assert out.startswith("The removed requirement IDs are [R1]") and "followed by" not in out


def test_working_marks_are_list_words_only():
    from core.src.query.release_diff import working_marks
    assert working_marks("[R1] the timer moved from 30 s to 31 s.") == []
    marks = working_marks("line, but the final answer should only list the id. I should keep "
                          "it short. Added requirement: [R9] a timer.")
    assert marks == ["i should", "the final answer"]
    assert working_marks("Looking at the pair, [R1] changed.") == ["opener:looking at"]

