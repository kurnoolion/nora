# stage-graph

**Status:** in-flight
**Opened:** 2026-11-06
**Landed:**
**Assignees:** kurnoolion
**Target modules:** pipeline, env, stores, store_service, ingest_runner, web, docker (cycle driver, compose)
**Active phase:** development

## Summary

Builds become a graph of content-addressed stages with recipes on top, so
stacks share exactly the stage outputs whose inputs and parameters are the
same and own the rest: a stage output is identified by a fingerprint over
its code, its parameters and its inputs; outputs live write-once in one
stage store; a build is a manifest mapping each stage and cell to a
fingerprint; a recipe is a build's stage list with parameters plus how
serving composes the result (SIRA alone, SIRA with the knowledge graph,
retrieval fusion). Today's special case, corpus stages shared and
enrichment per stack, is the first use; recipes without resolve, with a
graph or with a vector store are the ones the design must carry without
another layout change. Doc-first: the design note in `design.md`, two
rulings, then four implementation steps that each leave the system
working.

## Notes

- Opened from strand self-serve-ingestion, where the build layout changed
  three times in one day (per-release taxonomy, SIRA inside the build, one
  cell spelling); this strand is where the layout stops moving.
- 2026-11-08: baseline agreed and both rulings taken (recipe per stack,
  no fan-out; one stage store per host). Two tiers by rule: the corpus
  prefix extract→resolve is shared by every stack; variant stages per
  recipe. D-DRAFT-1..6. Diagram: artifact "Stage Graph Host Layout".
- 2026-11-09: step 2 built. Stage store (`pipeline/stage_store.py`) and
  corpus root; builds keep their `out/` tree as clones of stored outputs,
  because about thirty modules outside the pipeline read that layout
  (D-DRAFT-8 revises the baseline's manifest-only build). Migration from
  the 2026-11-08 inventory: no legacy import (D-DRAFT-7). The one
  taxonomy-gap cell is being re-derived by hand on the host.
- 2026-11-10: step 3 planned in four slices (design §10b); slice 1
  built: recipes as data (`pipeline/recipes.py`, two example recipes),
  frozen in the build by `cycle.sh start --recipe`, followed by the
  driver and the runner, carried into taxonomy fingerprints, recorded on
  the release set. D-DRAFT-9.
- 2026-11-10: slice 2 built: enrich as a driver-side stage (spec outside
  the pipeline order, taxonomy input per recipe, document prompt, model
  and SIRA revision), recorded by `verify-enrich`, never stored.
  D-DRAFT-10.
- 2026-11-10: slice 3 built: the Ingest page shows per stage whether the
  output was found or computed; Redo on a served ingest makes a new
  ingest of the cell under a bumped per-cell generation
  (`pipeline/generations.py`). D-DRAFT-11.
