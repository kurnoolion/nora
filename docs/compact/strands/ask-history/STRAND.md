# ask-history

**Status:** in-flight
**Opened:** 2026-08-18
**Landed:**
**Assignees:** hanifm
**Target modules:** web

## Summary

A History page listing the questions asked from this browser, each linking to its
stored answer at `/ask/s/{row_id}`. Builds on the share feature from the
`ask-page-ux` strand (PR #6). The list lives in browser localStorage — no auth, no
schema change.

Server-side history was rejected: `user_name` is optional free text defaulting to
anonymous, so a server-side "my history" would merge every anonymous user's
questions and be spoofable by typing someone else's name.

**Dependency:** `/ask/s/` exists only on `ask-page-ux`, so this branch stacks on it
and its PR takes base `ask-page-ux`. PR #6 must merge first.

## Feedback backlog

- [ ] **History page** — two-pane (question list left, answer right), 10 per page
  with Prev/Next, unlimited retention, Clear history + per-entry delete.

## Notes
