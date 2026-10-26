"""Size-bounded batching of prompt items (strand rel-history).

A caller has N items it wants a language model to look at (requirement
pairs, requirements to summarize, one requirement per MNO to compare)
and a byte budget per call. `pack` groups the items into batches that
stay under the budget without ever splitting an item, keeps a batch
inside one group when the caller names one (one plan per batch, so a
batch's output belongs to one plan), and numbers the batches so results
can be reassembled in order whatever order they complete in.

Pure, stdlib, deterministic: the same items in the same order give the
same batches. Knows nothing about prompts or providers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


@dataclass
class Batch:
    """One call's worth of items. `index` is the position in the plan,
    `group` the caller's group key (``""`` when ungrouped), `bytes` the
    sum of the items' sizes plus the per-batch overhead, `oversized`
    whether a single item alone exceeded the budget (it still gets a
    batch of its own; the caller decides what to do with it)."""
    index: int
    group: str
    items: list[Any] = field(default_factory=list)
    bytes: int = 0
    oversized: bool = False


def pack(items: Iterable[Any], size_of: Callable[[Any], int], *,
         max_bytes: int = 32768, overhead: int = 0,
         group_key: "Callable[[Any], str] | None" = None) -> list[Batch]:
    """Group `items` into batches under `max_bytes`, in input order.

    `size_of(item)` is the caller's byte estimate of the item's prompt
    contribution; `overhead` is a fixed per-batch cost (framing,
    instructions) counted against the budget. A batch never spans two
    groups when `group_key` is given. An item larger than the budget on
    its own becomes a one-item batch marked `oversized`.
    """
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if overhead < 0 or overhead >= max_bytes:
        raise ValueError("overhead must be non-negative and below max_bytes")
    budget = max_bytes - overhead
    batches: list[Batch] = []
    current: "Batch | None" = None

    def close() -> None:
        nonlocal current
        if current is not None and current.items:
            batches.append(current)
        current = None

    for item in items:
        group = group_key(item) if group_key else ""
        size = max(0, int(size_of(item)))
        if size > budget:
            close()
            batches.append(Batch(index=len(batches), group=group, items=[item],
                                 bytes=size + overhead, oversized=True))
            continue
        if current is None or current.group != group or current.bytes + size > max_bytes:
            close()
            current = Batch(index=len(batches), group=group, bytes=overhead)
        current.items.append(item)
        current.bytes += size
    close()
    return batches


def utf8_size(text: str) -> int:
    """The byte size most callers want for `size_of`."""
    return len(text.encode("utf-8"))
