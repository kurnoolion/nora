"""Size-bounded batching (core/src/llm/batching.py, strand rel-history):
never splits an item, honours the budget and the group, numbers batches,
and is deterministic."""

from __future__ import annotations

import pytest

from core.src.llm.batching import Batch, pack, utf8_size


def _items(sizes, groups=None):
    return [{"n": i, "size": s, "g": (groups[i] if groups else "")} for i, s in enumerate(sizes)]


def test_packs_under_budget_in_order():
    b = pack(_items([10, 10, 10, 10, 10]), lambda it: it["size"], max_bytes=25)
    assert [[it["n"] for it in x.items] for x in b] == [[0, 1], [2, 3], [4]]
    assert [x.index for x in b] == [0, 1, 2] and all(x.bytes <= 25 for x in b)
    assert all(not x.oversized for x in b) and all(x.group == "" for x in b)


def test_overhead_counts_against_the_budget():
    b = pack(_items([10, 10, 10]), lambda it: it["size"], max_bytes=25, overhead=6)
    assert [[it["n"] for it in x.items] for x in b] == [[0], [1], [2]]
    assert all(x.bytes == 16 for x in b)


def test_oversized_item_gets_its_own_batch_and_a_flag():
    b = pack(_items([5, 100, 5]), lambda it: it["size"], max_bytes=20)
    assert [[it["n"] for it in x.items] for x in b] == [[0], [1], [2]]
    assert [x.oversized for x in b] == [False, True, False]
    assert b[1].bytes == 100


def test_groups_never_mix_and_order_is_kept():
    items = _items([5, 5, 5, 5], groups=["A", "A", "B", "A"])
    b = pack(items, lambda it: it["size"], max_bytes=100, group_key=lambda it: it["g"])
    assert [(x.group, [it["n"] for it in x.items]) for x in b] == [("A", [0, 1]), ("B", [2]), ("A", [3])]


def test_deterministic_and_empty():
    items = _items([7, 9, 3])
    assert pack(items, lambda it: it["size"]) == pack(items, lambda it: it["size"])
    assert pack([], lambda it: 1) == []
    assert isinstance(pack(items, lambda it: it["size"])[0], Batch)


def test_argument_validation():
    with pytest.raises(ValueError):
        pack([], lambda it: 1, max_bytes=0)
    with pytest.raises(ValueError):
        pack([], lambda it: 1, max_bytes=10, overhead=10)


def test_utf8_size():
    assert utf8_size("abc") == 3 and utf8_size("é") == 2 and utf8_size("") == 0
