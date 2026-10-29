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
    # R2's text is identical and only its priority moved: a note, not a model sentence
    assert a["sections"][0]["text"] == "changed: R1" and a["sections"][0]["status"] == "ok"
    assert any(n.startswith("[R2]: priority Mandatory → Optional") for n in a["notes"])
    assert "R2" in a["note_ids"] and a["counts"]["dropped"] == 1
    assert a["sections"][1]["text"] == "removed: R6" and a["sections"][2]["text"] == "added: R9"
    assert a["notes"] == ["1 requirement(s) renumbered (1 parent); content unchanged.",
                          "Section(s) renumbered: 2 → 3.",
                          "[R2]: priority Mandatory → Optional; text unchanged."]
    assert out["plans"][1]["notes"] == ["Moved: [B4] from 2.1 to 4.1."]
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
    assert len(fired) == 4 and all("marks=opener:okay|let me|okay retried=1" in m for m in fired)
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
    # a key never carries a comma, so the `|`-joined log field parses
    assert working_marks("Wait, I need to check. Okay, [R1] changed.") == ["i need to", "okay", "wait"]


def test_structure_the_response_block_after_ids_is_cut():
    """A 'line.' tail after an answer with ids, phrased about the response's
    shape: structure the response, list the added ids, the summary can be,
    referencing the id."""
    from core.src.query.release_diff import looks_like_working_text, strip_working_text
    reply = ("For [R1], the retry timer is gone. For [R2], the fallback clause is gone. "
             "line. Let me structure the response: first, list the added IDs, then the "
             "summary. Since there's only one requirement, the summary can be a single "
             "sentence mentioning the feature and referencing the ID. "
             "Added requirement id: [R3] a new timer for the retry path.")
    assert looks_like_working_text(reply)
    out = strip_working_text(reply, ["R1", "R2", "R3"])
    assert "structure" not in out and "[R3]" in out and "[R1]" in out


def test_lone_phrase_sentence_in_a_retry_is_cut():
    """A retry can leave one listed phrase as a sentence of its own, under
    the two-phrase rule; it goes. The same phrase inside a longer
    sentence stays."""
    from core.src.query.release_diff import strip_lone_phrases
    retry = ("For [R1], the retry timer is gone. For [R2], the fallback clause is gone. "
             "followed by the answer. Removed requirement IDs: [R1], [R2].")
    assert strip_lone_phrases(retry) == ("For [R1], the retry timer is gone. For [R2], the "
                                         "fallback clause is gone. Removed requirement IDs: [R1], [R2].")
    assert strip_lone_phrases("Putting it all together:\n\nRemoved: [R1].") == "Removed: [R1]."
    keep = "For [R1], the timer the user wants is 31 s."
    assert strip_lone_phrases(keep) == keep


def test_retry_with_a_lone_phrase_counts_as_stripped(caplog):
    import logging
    pl = payload()

    class Residue(EchoProvider):
        def complete(self, prompt, system="", temperature=0.0, max_tokens=0):
            self.calls.append((system, prompt))
            ids = re.findall(r"\[([A-Z]+\d+)\]", prompt)
            body = "[" + "] [".join(dict.fromkeys(ids)) + "]"
            if "Output only the final answer" in prompt:
                return body + ". followed by the answer."
            return "Okay, let me tackle this.\n\n" + body

    with caplog.at_level(logging.INFO, logger="core.src.query.release_diff"):
        out = synthesize_release_diff(pl, Residue(), config=ReleaseDiffConfig(max_batch_bytes=4096, parallel=1))
    texts = [s["text"] for p in out["plans"] for s in p["sections"]]
    assert texts and all("followed by" not in t for t in texts)
    assert out["run"]["retried"] == 4 and out["run"]["stripped"] == 4
    assert all("stripped=1 cut_chars=24" in m for m in caplog.messages if m.startswith("RHD working text:"))


def test_triage_takes_bookkeeping_out_of_the_model_input():
    """Pairs that differ only in an annotation line, a title, an attribute
    or layout become one deterministic note each; heading-only entries
    become a count; the model sees the rest without the annotation lines."""
    from core.src.query.release_diff import triage_pairs, triage_totals
    rev = "Change type: New Requirement\nChange reason: new feature\n"
    pl = {
        "mno": "mno-q", "from": "Apr2031", "to": "Jul2031", "absent_plans": {}, "counts": {},
        "plans": {"PLANC": {
            "plan_name": "Plan C", "present": {"from": True, "to": True},
            "changed": [
                _pair("C1", rev + "the timer shall be 30 s", "the timer shall be 30 s"),
                _pair("C2", rev + "the fallback shall apply", "the fallback shall apply"),
                _pair("C3", "Only Applies to: Phone, Tablet\nthe audit note stands",
                      "Only Applies to: Phone, Tablet, Watch\nthe audit note stands"),
                _pair("C4", "Only Applies to: Phone\nthe retry shall stop", "Only Applies to: Phone, Watch\nthe retry shall stop"),
                _pair("C5", "spacing  here", "spacing here"),
                {**_pair("C6", "same text", "same text"), "new": {"title": "new title", "text": "same text",
                                                                    "priority": "Mandatory", "applicability": ["Phone"]}},
                _pair("C7", rev + "the value shall be 5", "the value shall be 6"),
            ],
            "removed": [_req("C8", ""), _req("C9", "a real removed requirement")],
            "added": [_req("C10", "Change type: New Requirement")],
            "renamed": [], "renumbered": [], "moved": []}},
    }
    out = triage_pairs(pl)
    p = out["plans"]["PLANC"]
    assert [c["req_id"] for c in p["changed"]] == ["C7"]
    assert "Change type" not in p["changed"][0]["old"]["text"]        # lines stripped for the model
    assert [r["req_id"] for r in p["removed"]] == ["C9"] and p["added"] == []
    t = p["triage"]
    assert t["sent"] == 1 and t["dropped"] == 6
    assert t["annotation"]["revision record"]["ids"] == ["C1", "C2"]
    assert t["annotation"]["applicability line"]["ids"] == ["C3", "C4"]
    assert set(t["annotation"]["applicability line"]["deltas"]) == {(("watch",), ())}
    assert t["layout_only"] == ["C5"] and t["title_only"] == ["C6"]
    assert t["heading_removed"] == ["C8"] and t["heading_added"] == ["C10"]
    assert triage_totals(out) == {"sent": 1, "dropped": 6, "same": 0, "annotation": 4, "title_only": 1,
                                  "attrs_only": 0, "layout_only": 1, "heading_only": 2}
    res = synthesize_release_diff(pl, EchoProvider(), config=ReleaseDiffConfig(max_batch_bytes=4096, parallel=1))
    notes = res["plans"][0]["notes"]
    assert "2 requirement(s) differ only in the revision record: [C1], [C2]." in notes
    assert "2 requirement(s) differ only in the applicability line (each gained: watch): [C3], [C4]." in notes
    assert "1 requirement(s): title changed, text identical: [C6]." in notes
    assert "1 requirement(s) differ in layout or markup only: [C5]." in notes
    assert "1 heading-only entry removed: [C8]." in notes and "1 heading-only entry added: [C10]." in notes
    assert sorted(res["plans"][0]["note_ids"]) == ["C1", "C10", "C2", "C3", "C4", "C5", "C6", "C8"]
    assert "sent=1 dropped=6" in res["compact"] and res["triage"]["dropped"] == 6
    assert len(res["plans"][0]["sections"]) == 2            # one pair batch, one removed batch


def test_config_file_rules_and_model_pin(tmp_path, monkeypatch):
    from core.src.query.release_diff import ReleaseDiffConfig, DEFAULT_ANNOTATION_LINES
    cfg = ReleaseDiffConfig.from_env({})
    assert cfg.annotation_lines and cfg.model == ""                 # the shipped file or the defaults
    f = tmp_path / "rd.json"
    f.write_text('{"annotation_lines": [{"name": "note", "pattern": "^note:.*$"}], "model": "think-1"}')
    cfg = ReleaseDiffConfig.from_env({"NORA_HISTORY_DIFF_CONFIG": str(f)})
    assert cfg.annotation_lines == (("note", "^note:.*$", "line"),) and cfg.model == "think-1"
    cfg = ReleaseDiffConfig.from_env({"NORA_HISTORY_DIFF_CONFIG": str(f), "NORA_HISTORY_DIFF_MODEL": "x"})
    assert cfg.model == "x"
    assert ReleaseDiffConfig.from_env({"NORA_HISTORY_DIFF_CONFIG": str(tmp_path / "none.json")}).annotation_lines == DEFAULT_ANNOTATION_LINES


def test_fast_model_idiom_is_working_text():
    from core.src.query.release_diff import looks_like_working_text
    for opening in ("We need summarize changes per requirement. [R1] ...",
                    "Identify changes: [R1] the timer moved.",
                    "Now produce answer.\n\n[R1] the timer moved."):
        assert looks_like_working_text(opening), opening


def test_inline_wrapper_section_reference_layout_and_grouped_deltas():
    from core.src.query.release_diff import (DEFAULT_ANNOTATION_LINES, _compile_rules,
                                             strip_annotations, triage_pairs)
    rx = _compile_rules(DEFAULT_ANNOTATION_LINES)
    text, taken = strip_annotations("[Table: Timer values | col a | col b\nrow 1 | 2\n"
                                    "3.2.1 Retry handling\nThe timer shall be 30 s.", rx)
    assert taken == {"table wrapper": ["[Table: Timer values"], "section reference": ["3.2.1 Retry handling"]}
    assert text.startswith("| col a | col b") and "3.2.1" not in text and "30 s" in text
    pl = {"mno": "mno-q", "from": "a", "to": "b", "absent_plans": {}, "counts": {}, "plans": {"P": {
        "plan_name": "P", "present": {"from": True, "to": True},
        "changed": [
            _pair("W1", "[Table: Timers v1 | a | b\nrow", "[Table: Timers v2 | a | b\nrow"),   # wrapper only
            _pair("L1", "the timer; shall-be <td>30</td> s.", "the timer shall be | 30 s"),        # layout only
            _pair("S1", "the retry shall stop\n3.2.1 Retry handling", "the retry shall stop"),       # reference line
            _pair("A1", "Only Applies to: Phone\nx", "Only Applies to: Phone, Watch\nx"),
            _pair("A2", "Only Applies to: Phone\ny", "Only Applies to: Phone, Watch\ny"),
            _pair("A3", "Only Applies to: Phone\nz", "Only Applies to: Tablet\nz"),
            _pair("A4", "Only Applies to: Phone\nw", "w"),                                           # line vanished
        ], "added": [], "removed": [], "renamed": [], "renumbered": [], "moved": []}}}
    t = triage_pairs(pl)["plans"]["P"]["triage"]
    assert t["sent"] == 0 and t["dropped"] == 7
    assert t["annotation"]["table wrapper"]["ids"] == ["W1"] and t["layout_only"] == ["L1"]
    assert t["annotation"]["section reference"]["ids"] == ["S1"]
    out = synthesize_release_diff(pl, EchoProvider(), config=ReleaseDiffConfig(parallel=1))
    notes = out["plans"][0]["notes"]
    assert "2 requirement(s) differ only in the applicability line (each gained: watch): [A1], [A2]." in notes
    assert "1 requirement(s) differ only in the applicability line (each gained: tablet; each lost: phone): [A3]." in notes
    assert "1 requirement(s) differ only in the applicability line: [A4]." in notes
    assert out["plans"][0]["sections"] == [] and out["plans"][0]["counts"]["changed"] == 0


def test_opener_only_reply_in_one_paragraph_is_cut_to_the_answer():
    from core.src.query.release_diff import looks_like_working_text, strip_working_text
    t = "We need summarize. For [R1], the timer moved."
    assert looks_like_working_text(t) and strip_working_text(t, ["R1"]) == "For [R1], the timer moved."
    t2 = ("We need list changes per requirement and omit those with wording only. Identify actual "
          "changes. Now produce final answer. For [R1], the timer moved to 31 s. Wording or layout only: [R2].")
    assert strip_working_text(t2, ["R1", "R2"]) == "For [R1], the timer moved to 31 s. Wording or layout only: [R2]."


def test_bare_ids_in_reasoning_do_not_anchor_the_cut():
    """The fast model names ids without brackets while reasoning, then
    announces its answer. The cut anchors on the bracketed form and the
    announcement, so the reasoning goes whole."""
    from core.src.query.release_diff import looks_like_working_text, strip_working_text
    r1 = ("We need list changes. R1 : added extra sentence on the timer. R2 : changed wording only. "
          "Identify actual changes. Now produce final answer sentences. List wording/layout only: [R2] "
          "Now output. For [R1], the timer gains a 31 s bound. Wording or layout only: [R2].")
    assert looks_like_working_text(r1)
    assert strip_working_text(r1, ["R1", "R2"]) == "For [R1], the timer gains a 31 s bound. Wording or layout only: [R2]."
    r2 = ("R3 : added a clause. So change: scope expanded to tablets. R4 : changed wording. "
          "Now produce final answer. For [R3], the scope now covers tablets. Wording or layout only: [R4].")
    assert strip_working_text(r2, ["R3", "R4"]) == "For [R3], the scope now covers tablets. Wording or layout only: [R4]."
    # a mid-answer aside in the model's idiom
    r3 = ("For [R5], the retry bound moves to 31 s.\n\nFor R6 no change content appears same. Also maybe "
          "title changed? Title same. So mention nothing.\n\nFor [R7], the fallback clause is new.")
    assert looks_like_working_text(r3)
    assert strip_working_text(r3, ["R5", "R6", "R7"]) == \
        "For [R5], the retry bound moves to 31 s.\n\nFor [R7], the fallback clause is new."
    # an announcement with no id after it is not a cut point
    r4 = "Now output. Nothing else to report."
    assert strip_working_text(r4, ["R1"]) == r4


def test_same_marker_lines_and_stray_no_change_sentences_leave_the_text():
    from core.src.query.release_diff import take_same_lines
    text = ("For [R1], the timer moves from 30 s to 31 s.\n"
            "[R2] ~same\n"
            "[R3]: ~ same\n"
            "[R4] No change; the requirement text is identical between the releases.\n"
            "For [R5] and [R6], the applicability now includes tablets (same change for both).\n"
            "Wording or layout only: [R7], [R8]\n"
            "For [R9], no change in the timer value but the fallback clause is new.")
    out, same = take_same_lines(text, ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9"])
    assert same == ["R2", "R3", "R4", "R7", "R8"]
    assert out.splitlines() == ["For [R1], the timer moves from 30 s to 31 s.",
                                "For [R5] and [R6], the applicability now includes tablets (same change for both).",
                                "For [R9], no change in the timer value but the fallback clause is new."]


def test_reassembly_lists_ids_itself_and_groups_the_notes():
    pl = payload()

    class Marks(EchoProvider):
        def complete(self, prompt, system="", temperature=0.0, max_tokens=0):
            self.calls.append((system, prompt))
            ids = re.findall(r"\[([A-Z]+\d+)\]", prompt)
            if "side by side" in prompt:
                return "\n".join(f"[{i}] ~same" if i == "B2" else f"For [{i}], a real change." for i in dict.fromkeys(ids))
            return "The plan loses or gains a timer."          # no id list from the model

    out = synthesize_release_diff(pl, Marks(), config=ReleaseDiffConfig(parallel=1))
    a, b = out["plans"]
    kinds = {s["kind"]: s for s in a["sections"]}
    assert kinds["removed"]["lead"] == "Removed: [R6]" and kinds["added"]["lead"] == "Added: [R9]"
    assert kinds["removed"]["text"] == "The plan loses or gains a timer." and kinds["pair"]["lead"] == ""
    assert "~same" not in kinds["pair"]["text"]
    assert b["counts"]["same"] == 1 and b["counts"]["dropped"] == 1 and "B2" in b["note_ids"]
    assert b["note_groups"]["structural"] == ["Moved: [B4] from 2.1 to 4.1."]
    assert b["note_groups"]["bookkeeping"] == [
        "1 requirement(s) unchanged in meaning (wording, formatting or layout only): [B2]."]
    assert a["note_groups"]["structural"][0].startswith("1 requirement(s) renumbered")
    assert out["triage"]["same"] == 1 and "sent=4 dropped=1 same=1" in out["compact"]
    pair_prompt = next(pr for _, pr in Marks().calls or [("", prompt_for(build_batches(pl)[0], pl)[1])])
    assert "~same" in pair_prompt and "state it once with all their ids" in pair_prompt
    removed_prompt = prompt_for(next(bt for bt in build_batches(pl) if bt.group.endswith("removed")), pl)[1]
    assert "Do not list the ids" in removed_prompt


def test_quarter_phrases_and_carrier_names_resolve_and_display():
    from core.src.query.release_diff import display_release, normalize_release_ref
    rels = ["Jan2031", "Apr2031", "Jul2031", "Oct2031"]
    assert normalize_release_ref("Q3 2031") == "Q3-2031" and normalize_release_ref("2031-q1") == "Q1-2031"
    assert resolve_release_pair(["Q1 2031", "Q3 2031"], rels) == ("Jan2031", "Jul2031")
    assert resolve_release_pair(["q4 2031"], rels) == ("Jul2031", "Oct2031")
    with pytest.raises(PipelineError) as ei:
        resolve_release_pair(["Q2 2032"], rels)
    assert ei.value.code == "RHD-E002"
    names = {"Jan2031": "Spring 31", "Apr2031": "Summer 31"}
    assert resolve_release_pair(["summer 31"], rels, names) == ("Jan2031", "Apr2031")
    assert display_release("Jan2031", names) == "Spring 31 (Jan2031)" and display_release("Jul2031", names) == "Jul2031"
    cfg = ReleaseDiffConfig(release_names={"mno-q": names})
    assert cfg.names_for("mno-q") == names and cfg.names_for("other") == {}

