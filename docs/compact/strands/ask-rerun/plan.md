# Implementation plan — ask-rerun

Branch `ask-rerun` off `main` @ 9c2bbfa. Target module: `web`.

## Context

Alpha verification loop: change a corpus or a config knob, re-ask the same
question, compare. Today that means retyping the question every time.

A History page already ships (archived strand `ask-history`) — but it links to a
FROZEN snapshot at `/ask/s/{row_id}` and `shared.html` states outright that
opening one "does not re-run the query." Viewing a stored answer is not
re-asking, so the existing feature cannot serve this loop.

## What already exists (verified, do not rebuild)

| Store | Holds | Notes |
|---|---|---|
| `nora-ask-history` | `{q, path, lane, ts}`, newest-first, unlimited | Written by `askRecordHistory` (index.html ~446). **Bails when the answer has no `data-share-path`** — an errored ask never enters history |
| `ask-last-fields` | name, label, provider, mode, lanes | Prefilled on load. Comment: "the question is always fresh" |

The page therefore already persists everything about an ask EXCEPT the question,
deliberately. This strand adds that missing half.

Server-side history stays rejected (`ask-history` strand): `user_name` is
optional free text defaulting to anonymous, so server-side "my history" would
merge every anonymous user and be spoofable by typing someone else's name.

## Settled

- **Extend `nora-ask-history`**; no second store. One source of truth.
- **Capture every question asked**, independent of whether an answer row was
  minted — the errored ask is the one most worth re-running after a fix.
- **Retention unlimited**; the 15-20 cap applies only to the Ask-page control, so
  it is a display choice, not a retention policy. The History page keeps
  everything it keeps today.
- **Re-run uses CURRENT form settings**, not the settings the question was
  originally asked with. The changed knob is the point of re-running; reproducing
  original conditions would defeat it, and would need per-entry provider/mode
  fields that do not exist. Cheap to add later if true A/B reproduction is ever
  wanted — not built speculatively now.
- **Dedupe by question text in the control** (most recent wins), so a two-lane
  ask appears once and repeated verification of one question does not crowd the
  list. The History page keeps per-lane rows unchanged — those give engineers the
  lane-compare view.
- **UI**: a recents dropdown beside the Ask controls plus a distinct "Re-run
  last" button.

## Approach

### 1. Record the question at submit time — `templates/test/index.html`

Today recording happens after an answer arrives, keyed off `data-share-path`.
Move the question capture to the form's `submit` event so it is unconditional,
and keep the share path as a later enrichment.

- On submit (non-empty question, mirroring the existing guard at ~316): unshift
  `{q, ts, path: "", lane: ""}`.
- When the answer lands, `askRecordHistory` **updates that entry** with the
  share path(s) instead of unshifting new ones — otherwise every ask
  double-records. A two-lane answer still needs its per-lane rows for the
  History page, so: the submit-time entry is replaced by the per-lane entries
  when paths exist, and left as-is when none do.

This is the load-bearing change; get it right before any UI work.

### 2. Make the History page tolerate pathless entries — `templates/test/history.html`

**Required, not optional.** `history.html:135` does
`entry.path.replace("/ask/s/", "/api/ask/s/")` and `:170` reads `entry.path`, so
a pathless entry throws a TypeError and breaks the page.

- Render a pathless entry as an unlinked row, labelled so it reads as
  intentional (the ask produced no stored answer).
- Its Share button is omitted rather than rendered dead.
- Pagination (10/page, Prev/Next) and per-entry delete keep working across mixed
  entries.

### 3. Recents dropdown + Re-run last — `templates/test/index.html`

- Build the list from `askReadHistory()`, dedupe by `q` keeping the newest,
  slice to 20.
- Choosing an entry writes its text into `#test-question` and focuses it —
  loading, not asking, so the user can edit before submitting.
- **Re-run last** sets `#test-question` to the newest question and calls
  `form.requestSubmit()`. Deliberately reuses the form's own submit path so
  every existing behaviour applies unchanged: validation, the sticky-fields
  write, the streaming handler, history recording, and the sidebar
  collapse-on-ask from strand `collapsible-sidebar`. No duplicate ask logic.
- Both controls hide when history is empty rather than rendering disabled.
- Every `localStorage` access in `try/catch`, matching `askReadHistory` /
  `askWriteHistory` — blocked storage degrades to "no recents", never a broken
  Ask page.

### 4. No server-side changes

Entirely client-side. Re-run posts to the existing
`/api/test/ask-stream`; `/api/test` and `/ask/history` are already in
`_TEAM_ALLOWED` (`team_mode.py:31`), so **no route is added and no allowlist
entry is needed**. Still verify with the gate ON per CLAUDE.md — the Ask page is
the one surface gated users have.

## Tests

The repo has no JS harness (`node --check` is the only JS tooling), so the
recents behaviour is browser-verified. What CAN be asserted server-side:

- `test_web_playground.py` — the Ask page renders the recents control and the
  Re-run button, in both gate states (the gated nav is where this matters most).
- A pathless-entry render check for `history.html` if it is reachable through a
  template render test; otherwise browser-only and stated as such.

Do not claim coverage the harness cannot provide.

## Verification

1. Full suite; baseline on `main` is 8 pre-existing failures — confirm no growth.
2. Browser, against `~/work/env_demo` at `http://127.0.0.1:8000/test`:
   - Ask a question; it appears in recents immediately.
   - **Ask a question that errors** (stop the LLM endpoint) — it must still
     appear, which is the whole point of capturing at submit.
   - Re-run last re-executes and streams a fresh answer.
   - Two-lane ask shows once in recents, twice on the History page.
   - Same question asked repeatedly shows once in recents.
   - `/ask/history` still renders, paginates and deletes correctly with a mix of
     linked and pathless entries.
   - Recents survives reload; empty-history state hides both controls.
3. Gate ON: `/test` serves, controls render, no route regression.

## Decisions to draft

1. Extend `nora-ask-history` rather than a second store; dedupe at display time
   so the History page's per-lane rows are preserved.
2. Capture at submit rather than on answer — makes errored asks re-runnable, and
   forces `history.html` to tolerate pathless entries.
3. Re-run uses current form settings, not the original ask's; per-entry
   provider/mode deliberately not recorded.
4. Unlimited retention with a display-only cap.
5. Re-run via `form.requestSubmit()` rather than a parallel ask path, so it
   inherits validation, sticky fields, streaming and collapse-on-ask for free.
