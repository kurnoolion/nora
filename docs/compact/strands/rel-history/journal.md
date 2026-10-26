## 2026-10-26 — Part one: the History store validated on two real releases

### Done this session
- Design v0.1: validation procedure; the synthesized release diff with
  a general pair packer and parallel runner in `llm`.
- Validation on one MNO, two consecutive releases, three rounds. Found
  and fixed: `most_changed_sections` keyed per requirement (now per
  containing section, ranked by edit rows); every `moved` row was a
  renumbering with the parent unchanged (new kind `renumbered`, detail
  chapter/sibling/parent/other; `moved` needs a new parent); nine
  removed plans were absent from the newer release's documents (now
  `absent_plans` + `edits`); 194 of 326 `changed` rows were byte-order
  marks and zero-width characters (canonical form v3 strips Unicode
  format characters, the IR strips them at extraction, the Overlay
  restamp tool recomputes stored fingerprints, the registry's unit
  identity carries the fingerprint version so a bump republishes once).
- Roll-out: every stack on v3 with expansion-off Stage-1 at 0 of 39;
  the diff reads moved 0 / renumbered 444 / 9 absent plans, 530 rows /
  changed 135 / 7 renamed pairs, as predicted.
- Test image runs its files by path; the cell-DB loader suite restores
  the service module after each test (its reload leaked cell-db mode
  into later suites wherever bm25x is installed).

### Next
- Part two, development phase: `changed_pairs` on the History store;
  packer and runner in `llm`; prompts and reassembly in `query`; the
  SSE route with cancel in `web`; behind `NORA_HISTORY_DIFF_ENABLED`.

### Flags
- Trees built before the strip still carry the characters in their
  text; v3 hashes ignore them. The next build cycle's trees are clean.
- Cells converted from labels without the fingerprint sidecar have no
  fingerprint column; they republish once a sidecar label exists.
- `req_history` reports a renumbered parent as `moved` (one node's
  intervals cannot see the parent pair); the diff resolves it.
