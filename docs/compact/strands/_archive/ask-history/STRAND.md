# ask-history

**Status:** landed
**Opened:** 2026-08-18
**Landed:** 2026-08-18 (via PR from branch `ask-history`, stacked on `ask-page-ux`)
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

- [x] **History page** — two-pane (30/70), 10 per page with Prev/Next, unlimited
  retention, Clear history + per-entry delete, Share from the answer header.

## Notes

- Shipped in 7 commits on `ask-history`, stacked on `ask-page-ux`. **PR #6 must
  merge before this PR.** Scope: `web` only; no schema or storage change.
- Recording needs no server/SSE change: `_answer.html` exposes `data-share-path`
  and `data-lane`, and the Ask page records one entry per lane answer.
- The detail pane reuses the shared-answer markup via `_shared_body.html` + a
  body-only `/api/ask/s/{id}`, so the two surfaces cannot drift.
- Bug found in live review: the pane first fetched the stored share URL, so the
  whole shared page (heading, nav, explainer) rendered inside the column. Fixed
  by swapping `/ask/s/` for `/api/ask/s/` at fetch time.
- Stale entries (store reset / different `env_dir`) 404 and the pane offers to
  remove the entry rather than rendering blank.
