## D-DRAFT-1 — `find_req`'s `plan_name` is populated for the primary plan only

**Context.** Manager feedback after the req-bubble-tables strand: show a
readable plan name beside the requirement snippet instead of the plan code.
The bubble's plan comes from `_req_plan`, which is the requirement's OWN
`plan_id` — a within-tree dimension, because a single source document can
carry several plans (one PDF whose sections each correspond to a plan). The
only readable name available is the tree-level `plan_name`, a document-level
scalar.

**Decision (Hanif, 2026-09-18).** `find_req` returns `plan_name` as the tree's
`plan_name` only when the requirement's effective plan equals the tree's own
`plan_id` — i.e. the requirement belongs to the document's primary plan — and
`""` otherwise. Consumers show the plan id when `plan_name` is empty.

**Why.** The document-level name describes one plan, not every plan the
document carries. `graph/builder.py` already encodes exactly this: it stamps
`plan_name` on the primary Plan node and an empty name on secondary ones,
because a sections-as-plans document has no per-plan name. There is no
per-plan-id-to-name mapping anywhere in the codebase, so pairing a secondary
plan's id with the document's name would not be a degraded label — it would be
a wrong one, attributing a requirement to a plan it does not belong to.

**Rejected alternatives.**
- *Return `tree["plan_name"]` unconditionally.* Simplest, and indistinguishable
  from correct on single-plan corpora — which is what makes it dangerous. On a
  multi-plan document every secondary-plan requirement would be labelled with
  another plan's name, and nothing in the UI would reveal it.
- *Build a plan_id-to-name mapping.* There is no source for one. The parser
  extracts a single `plan_name` per document via the profile regex; secondary
  plans are discovered from per-req `plan_id` and have no name anywhere.

**Consequences.** On corpora where the tree-level `plan_id` is empty and every
requirement carries its own — which is the whole of `~/work/env_demo` — the
name is never shown and the bubble keeps displaying the plan code. That is
correct behaviour, but it means the feature is invisible until a corpus
populates `plan_name` for its primary plan. If the work-PC corpus is also
empty, the remaining work is profile-side (the `plan_name` regex), not web-side.

## D-DRAFT-2 — An empty `section_number` falls back to `parent_section`

**Context.** The same feedback asked for the section number beside the snippet.
`Requirement.section_number` is empty for a meaningful share of the corpus:
table-anchored and leading-id-body requirements have none by design — they are
addressed by `req_id` and linked to their owning section through
`parent_section` / `parent_req_id` — and a TOC pair miss also leaves it empty.
The parser's MODULE.md states outright that consumers must not assume it is
non-empty.

**Decision (Hanif, 2026-09-18).** The bubble renders `section_number` when
present, falls back to `parent_section` when it is empty, and omits the line
entirely when both are. `find_req` returns both keys, always present, so the
template branches on emptiness rather than absence.

**Why.** A table-anchored requirement is not section-less — it sits inside a
section that the tree already records. Rendering nothing would discard
information the model holds, and rendering an empty label would be worse than
either. Returning both keys unconditionally keeps the empty-vs-missing
distinction out of the template, where a Jinja `Undefined` would silently
render as blank and hide the difference.

**Consequences.** The line reads "Section 1.4" for a table-anchored req whose
parent is 1.4, which is the parent's number rather than the requirement's own.
That is the honest answer — the requirement has no number of its own — but a
reader cannot tell from the line alone whether it is exact or inherited. If
that distinction turns out to matter, the fix is to label the fallback case
differently, not to drop it.
