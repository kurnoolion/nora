# ask-rerun

**Status:** in-flight
**Opened:** 2026-09-03
**Landed:**
**Assignees:** Hanif
**Target modules:** web

## Summary

Re-asking a question on the Ask page, for the alpha verification loop: change a
corpus or a config knob, re-run the same question, compare. Two parts —

1. **Re-run.** Today the History page links to a stored snapshot at
   `/ask/s/{row_id}` and deliberately does NOT re-run (`shared.html`). That is
   the gap: verification needs the query re-executed, not the frozen answer.
2. **On-page recents.** Surface the most recent questions on `/test` itself,
   loading one back into the question box, plus one-click re-run of the last
   one — instead of navigating to `/ask/history` and back.

Extends the existing `nora-ask-history` localStorage store from archived strand
`ask-history` rather than adding a second list; one source of truth. Retention
stays unlimited (the History page keeps everything) and the 15-20 cap applies
only to the Ask-page control, so it is a display choice, not a retention policy.

Recording moves to capture EVERY question asked, not only those that minted a
stored answer row. `askRecordHistory` currently bails without a
`data-share-path`, so an errored ask never enters history — exactly the question
a user most wants to re-run after a fix.

Server-side history stays rejected, per the `ask-history` strand: `user_name` is
optional free text defaulting to anonymous, so a server-side "my history" would
merge every anonymous user and be spoofable by typing someone else's name.

## Notes

