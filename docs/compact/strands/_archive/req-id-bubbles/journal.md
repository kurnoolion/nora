## 2026-08-31 — req-ID bubbles: design, ship, and three browser-only bugs

### Done this session
- Strand opened, scoped, and shipped end to end on branch `req-id-bubbles`
  (6 commits, 18 files, +896). Req IDs in an Ask answer are now click/hover
  bubbles showing the requirement inline, on all three surfaces (live answer,
  `/ask/s/`, history pane).
- **Scope collapsed at design time from `web,query` to `web` alone.** The strand
  opened assuming it needed a generic req-ID matcher (three hardcoded `VZ_REQ_`
  regexes, VZW-only since D-091..D-104). It does not: anchors are the row's own
  req_id set, matched verbatim — the approach `_select_synth_extract_citations`
  (`playground.py:665`) already uses for the SIRA lane. `eval/metrics.py` and
  golden-eval scoring were never touched. Captured as D-DRAFT-1.
- Bubble text reads from the parse layer via `req_tree.find_req` +
  `latest_match`, not RAG chunk text — chunk text is not persisted on stored
  rows (D-209), so a chunk-backed bubble would work live and break on exactly
  the surfaces a teammate opens. D-DRAFT-2.
- New `GET /api/req/{req_id}` (read-only, parse-layer), added to
  `team_mode._TEAM_ALLOWED` in the same change and covered by a gate test.
  D-DRAFT-3.
- Linkification runs over rendered HTML, alternating on tags so matches inside
  attributes are never rewritten, skipping `<a>` / `<pre>`; inline `<code>` IS
  bubbled (LLMs routinely backtick req IDs). Repeated ids get unique collapse
  targets.
- UX iterated with the architect at the keyboard: badge contrast → floating
  overlay instead of inline expansion (inline reflowed the paragraph and split
  the sentence being read) → hover-preview with click-to-pin.
- `Cache-Control: private, max-age=300` on hits only; misses stay uncached
  because a 404 usually means the corpus is mid-rebuild.
- 50 tests across the bubble files; full suite 1820 passed, only the 8 failures
  that pre-exist on clean main.

### Three bugs that only existed in the browser
- **`hx-trigger="revealed"` inside a `.collapse`** — htmx evaluates `revealed`
  on scroll events, and a collapse opening fires no scroll, so the request went
  out only when an unrelated reflow happened. Read as a flaky request.
- **`innerHTML` injection left htmx unwired** — the real cause. Both the Ask
  page's SSE handler and the History detail pane assign the rendered answer
  with raw `innerHTML`; htmx only wires what it swapped itself, so every `hx-*`
  in the answer was inert and NO request was ever attempted. Fixed with
  `htmx.process()` at both sites. **The architect's "History works, Ask
  doesn't" was the diagnostic that located it** — the asymmetry pointed at how
  each surface receives its HTML.
- **A stale cached `app.js` produced a false negative in my own testing** — I
  reported dismissal broken and nearly changed working code. Caught by
  comparing what the server serves against what the browser actually loaded.

The through-line: all three were invisible to pytest, which asserts rendered
markup and cannot tell whether the browser wires, fires, or even loads it.

### In progress
- Nothing mid-edit. Branch is clean and green.

### Next
- Push `req-id-bubbles` and open the PR for the architect.
- `/land-strand` AFTER merge, not before — see Flags.

### Flags
- **SIRA lane unverified — the one open assumption.** D-DRAFT-1's parity claim
  rests on SIRA-lane `req_id` values being byte-identical to parse-tree
  `req_id`s. Only the nora lane is runnable on this machine; SIRA runs in the
  office. If a SIRA bubble 404s, the fix is an id-normalization step inside
  `req_fragment` — not a redesign. Resolve before landing, since it would add a
  consequence to D-DRAFT-1.
- **`htmx.process` trap is repo-wide, not bubble-specific.** Any future `hx-*`
  inside a hand-injected fragment in this template set is dead on arrival.
  Two sites are fixed; others that inject HTML will hit it identically. Worth a
  convention note, and arguably its own decision.
- **Hover timings unvalidated by a human.** `OPEN_DELAY` 220ms / `CLOSE_GRACE`
  180ms in `app.js` were verified programmatically but not judged by feel with
  a real pointer; touch fallback (`(hover: hover)` gate) is untested for lack
  of a device.
- **COMPACT skills were missing on this clone.** `9f03ece` un-vendored them and
  this machine never got the per-machine install; recovered from git history
  into `~/.claude/skills/`. Any other clone that predates the install has the
  same gap and will silently skip the rituals.
