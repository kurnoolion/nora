# req-bubble-metadata

**Status:** in-flight
**Opened:** 2026-09-18
**Landed:**
**Assignees:** Hanif
**Target modules:** web

**Active phase:**

## Summary

Show a requirement's section number and readable plan name alongside the
snippet in the Ask-page req-ID bubble. Manager feedback following the
req-bubble-tables strand. Two fields, one surface (the bubble panel only —
the Cited-requirements list and Requirement Browser are out of scope).

Section number comes straight off the requirement, falling back to
`parent_section` when empty — table-anchored and leading-id-body reqs have
`section_number=""` by design (parser MODULE.md), so the bubble must not
assume it is present. Plan name comes from the tree-level `plan_name`, but
only for requirements belonging to the tree's PRIMARY plan: `graph/builder.py`
establishes that secondary plans carry no name, and no per-plan-id -> name
mapping exists, so a secondary-plan req falls back to showing its plan id
rather than borrowing the document's name.

Unlike req-bubble-tables this touches `req_tree.find_req`, shared with the
Requirement Browser and the Eval Studio picker. The change is additive (two new
dict keys); all three call sites read by key, so nothing else is affected.

## Notes

Open question carried from scoping (2026-09-18): every tree in `~/work/env_demo`
has an EMPTY tree-level `plan_id` and `plan_name` while its requirements carry
per-req plan ids — i.e. the demo corpus is entirely the secondary-plan case, so
the plan-name half renders nothing locally and cannot be verified here. Pending
check on the work-PC corpus: is top-level `plan_name` non-empty, and does
top-level `plan_id` match the requirements' `plan_id`? If `plan_name` is empty
there too, the corpus carries no plan name and the fix is profile work (the
`plan_name` regex), not web work.
