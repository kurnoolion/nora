# rel-history

**Status:** in-flight
**Opened:** 2026-10-25
**Landed:**
**Assignees:** kurnoolion
**Target modules:** stores, store_service, llm, query, web
**Active phase:** architecture

## Summary

Validate the History store against real releases and give it a consumer.
First, run the release diff for one MNO across two consecutive releases
from a served History store, read the change list against the documents,
and correct the change classification where it is wrong: identity rules,
moved and reordered detection, the likely-renamed heuristic. Second,
answer release-change questions on the Ask page from the History store.
Changed requirements go to the language model as previous and current
text side by side, packed pair-wise into batches under a configurable
size (default 32 KB) and sent as configurable parallel calls. Deleted
requirements are not paired: per plan, the model names the deleted IDs
and gives one short summary of what was removed; added requirements are
handled the same way. The page streams progress and the partial
syntheses as they arrive, groups the result by plan, and offers cancel.
The batching and parallel-call runner are built as a general capability
so summaries and cross-MNO comparisons can use them later.

## Notes

