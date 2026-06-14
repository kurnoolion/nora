# multi-mno-nora — draft decisions

Draft decisions for this strand. Promoted to canonical `DECISIONS.md` with real
`D-XXX` IDs at `/land-strand` time.

---

## D-DRAFT-1 — Multi-plan documents: promote plan to a per-requirement attribute (Option B), not one-tree-per-plan

**Context:** NORA models "plan" as one-per-document: `RequirementTree.plan_id`
/ `plan_name` are single scalars (set from a first-page metadata regex), the
parser emits one tree per source document (a load-bearing invariant — re-run
incrementality and per-document parse logs key off it), and the SIRA adapter's
multi-granularity rows group every requirement under the single `tree.plan_id`
(one `doc:<plan>` row per document). This fits Verizon (one docx = one plan).
MNO-B's requirements arrive as a **single PDF whose sections each correspond to
a plan**, so under the current model all of MNO-B's plans would collapse into
one. Grounding showed the plan is recoverable per-requirement: req_ids carry a
plan prefix, and `_extract_plan_id_from_req` (driven by the profile's
`RequirementIdPattern.components` = separator + plan_id_position) already
derives it — it's currently computed transiently for cross-reference
classification, then discarded.

**Decision:** Adopt **Option B** — promote plan to a per-requirement
attribute, keeping one tree per document:
- Add a `plan_id` field to `Requirement`, populated during parse via the
  existing profile-configured req_id plan-extraction.
- Change the SIRA adapter to group `doc:<plan>` / `section:<plan>` rows by
  **per-requirement `plan_id`** instead of `tree.plan_id`, so one document
  yields N plans.
- Apply the same per-req plan treatment in the graph builder (FR-7 organizes
  the KG by plan).
- The "one tree per document" invariant is unchanged; plan becomes a
  *within-tree* dimension rather than a tree-level scalar.

**Why:** The plan is already encoded in req_ids and the extraction mechanism
already exists, so B is surgical (store what's already computed; group by it)
and stays profile-driven — MNO behavior lives in the profile, not code
(D-003 / FR-3). It preserves the load-bearing one-tree-per-document invariant.
Alternatives rejected: **Option A** (split a document into N trees at parse
time) breaks that invariant — bigger blast radius across re-run
incrementality and parse-log-per-doc, for no benefit B doesn't already give.
**Option C** (pre-split the PDF into N files before extraction) needs a
reliable PDF-splitter and loses the single-document reality — brittle
operational machinery outside the pipeline.

**Consequences:**
- `Requirement` schema gains `plan_id` (additive). Consumers that grouped by
  `tree.plan_id` (adapter, graph) move to per-req grouping.
- `tree.plan_id` becomes ambiguous for a genuinely multi-plan document — keep
  it as the document's primary/first plan for back-compat, but it is no longer
  the authority for plan grouping. (Define its exact semantics when wiring the
  parser change.)
- The **profile-extraction mechanism is config-only when the plan prefix is
  delimited** (e.g. `PLANB-123` -> set `separator` + `plan_id_position: 0`,
  exactly the Verizon shape). If MNO-B's prefix is **run-together**
  (`PLANB123`, no delimiter), the split-on-separator model can't separate
  prefix from number and `RequirementIdPattern` needs a small **generic**
  extension — a regex-capture-group plan-extraction mode (still profile-driven,
  usable by any future corpus, not MNO-specific). Which shape MNO-B uses is the
  one open detail, resolved on inspecting the real document. The parser /
  adapter / graph changes are identical either way; only the profile's
  extraction config differs.
- Single-tree-per-document Verizon corpora are unaffected: their requirements
  all share one plan prefix, so per-req grouping yields the same single
  `doc:<plan>` as today.
