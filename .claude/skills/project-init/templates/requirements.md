# Requirements

Last updated: YYYY-MM-DD. Behavioral specs only — project identity and scope live in `PROJECT.md`.

<!--
How to use this file:

- Each requirement has a stable ID. IDs are never reused and never renumbered.
  - New functional requirement → next `FR-N` (or the project's prefix if retrofitted — e.g. preserve existing `REQ-042`).
  - New non-functional requirement → next `NFR-N`.
- One sentence per requirement. Active voice. Testable where possible.
- Removed requirements are struck through in place:
    ~~**FR-3** — <original text>~~ (removed YYYY-MM-DD: <reason>)
- Items agreed to postpone go under `## Deferred` — they are not drift.
- `drift-check` reads this file. Keep it current; it is the authority for what the
  system is supposed to do, which design and implementation are checked against.
-->

## Functional

<!--
Behaviors the system must exhibit. One sentence each.

Examples:
- **FR-1** — The system accepts a long URL via `POST /shorten` and returns a unique short code.
- **FR-2** — The system 301-redirects `GET /<code>` to the originally submitted long URL.
- **FR-3** — Short codes are 7 characters from a base62 alphabet.
-->

- **FR-1** — <behavior>

## Non-functional

<!--
Cross-cutting constraints (performance, reliability, security, usability, compliance).
Include a measurable criterion where applicable.

Examples:
- **NFR-1** — 99th-percentile latency for redirect requests is under 100ms at 10K mappings.
- **NFR-2** — No short code is served before its mapping is durably stored.
- **NFR-3** — The service runs as a single binary; no external dependencies at v1.
-->

- **NFR-1** — <constraint + measurable criterion if applicable>

## Deferred

<!--
Requirements explicitly postponed. Not drift. Drift-check surfaces these as notes.

Entry format:
- **FR-N** — <requirement> (deferred: <why> — revisit: <trigger or date>)

Examples:
- **FR-14** — Custom short codes on shorten (deferred: v2 scope — revisit: after v1 ships)
- **NFR-7** — Horizontal scale beyond single-node (deferred: in-memory storage acceptable until usage forces multi-node — revisit: when daily mappings exceed 100K)
-->

<!-- (none yet) -->
