# ask-stream-stop

**Status:** in-flight
**Opened:** 2026-11-13
**Landed:**
**Assignees:** Hanif
**Target modules:** web, llm
**Active phase:**

## Summary

Ask page, SIRA lane (#30, option 1): once the store returns, stream the fetched requirements as a partial result labelled as search candidates ("Searched: N candidates (the answer cites from these)"), in retrieval order, and mark the cited ones when the answer arrives. They appear after query expansion (~11 s on a local model); a pre-expansion search is out of scope. Add a Stop button on the SIRA progress row that renders the card with the candidates and a "stopped before the answer" note, and cancels the in-flight model HTTP call so the shared endpoint is freed. The stored row carries a stopped flag that the History page and feedback exports read as stopped, not as an empty answer. The cancel route is checked with the team-mode gate on, and the change works alongside the `id_lookup` and History lanes without rendering twice.

## Notes
