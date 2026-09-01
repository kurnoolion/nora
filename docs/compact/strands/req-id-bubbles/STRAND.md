# req-id-bubbles

**Status:** in-flight
**Opened:** 2026-08-31
**Landed:**
**Assignees:** Hanif
**Target modules:** web
**Active phase:** development

## Summary

Render req IDs inline in the Ask portal answer as clickable bubbles, so a reader
sees the requirement text without leaving the answer or opening engineering
details. Click → inline expand under the paragraph; requirement text comes from
the parse tree via the existing `req_tree.find_req` helper (authoritative, works
for any req_id, no new persistence) rather than RAG chunk text — which is what
makes the feature work on shared links, where D-209 persists the normal user
view only. Applies to all three surfaces: live Ask answer, shared links
(`/ask/s/`), and the history pane.

Scoped web-only at design time. The strand opened suspecting it needed a generic
req-ID matcher (req-ID extraction is hardcoded `VZ_REQ_` in
`query/citation_audit.py:33`, `query/analyzer.py:97`, `eval/metrics.py:181`, on a
corpus that went multi-MNO at D-091..D-104). It does not: bubbles anchor on the
req_id set the row already carries, matched verbatim — the corpus-agnostic
approach the SIRA lane already uses at `playground.py:665`
(`_select_synth_extract_citations`). The three `VZ_REQ_` sites stay a real latent
bug and are recorded as a Flag, not this strand's work.

## Notes

<!-- appended to over the strand's lifetime -->

### 2026-08-31 — Load-bearing assumption for the SIRA lane

The two lanes are exercised on different machines: nora locally on the
architect's Mac, sira only in the office. The bubble path itself is
lane-agnostic by construction (D-DRAFT-1), so the one thing that can still make
SIRA differ is **identity**:

> **Assumption (unverified):** SIRA-lane `req_id` values are byte-identical to
> the `req_id` values in `out/parse/<mno>/<release>/` trees.

If false, SIRA bubbles 404 while NORA bubbles work. Not idle worry — golden-eval
D-DRAFT-9 ("SIRA corpus requirement rows stamp bare plan_id, never plan_name")
is precedent for the two sides diverging on an identity field. First thing to
run in the office; if it fails the fix is an id-normalization step inside the
endpoint, not a redesign.

### 2026-08-31 — Verification loop

```
1. Filter unit tests    → req_id in prose bubbles; inside <a>/<pre> does not;
                          markup byte-identical when req_ids is empty
2. GET /api/req/{id}    → known id returns fragment; unknown returns 404
3. Team gate ON         → /api/req/ reachable (extend test_team_mode.py)
4. NORA lane, local     → click a bubble, requirement text appears
5. Shared + history     → same bubble works on /ask/s/{row_id}
6. SIRA lane, office    → a SIRA answer's req_id resolves, not 404
                          ^ the assumption above, not a formality
```

Steps 1-5 are local. Step 6 is the office gate.
