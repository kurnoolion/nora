## D-DRAFT-1: Heading-only entries count as Added and Removed (amends D-294)
**Status**: Draft · **Date**: 2026-11-16.

**Decision.** An id added or removed with no body text stays out of the model's input but is counted and listed under Added / Removed, marked heading only, not under Bookkeeping.
**Why.** Field review: all 85 such removals in one plan were real deletions (absent from the new release, none renumbered or moved). D-294's point was keeping empty text from the model, not from the counts. Rejected: keeping them as bookkeeping.
**Consequences.** Plan Added / Removed counts match the headline's store counts; the Bookkeeping count is the triage's dropped pairs only.

## D-DRAFT-2: The model's "unchanged in meaning" pairs are listed under Changed
**Status**: Draft · **Date**: 2026-11-16.

**Decision.** Pairs the model marks `~same` stay in Changed's count (D-295) and are now listed under Changed, not under Bookkeeping.
**Why.** Field review: the table showed Changed 12 / Bookkeeping 1 while the detail listed 9 and 4. Rejected: moving them into the Bookkeeping count, which would make the headline vary between runs.
**Consequences.** Every table count equals what its section lists.

## D-DRAFT-3: One plan per document is a profile option
**Status**: Draft · **Date**: 2026-11-16.

**Decision.** `plan_metadata.plan_per_document` makes every requirement take the document's plan; off by default, on for the Word-format corpus.
**Why.** About 20 documents per release there mix id-prefix variants of one plan, and History showed each as two plan rows. Rejected: always using the document's plan (one MNO-B PDF holds several plans); an alias list (20+ pairs to curate).
**Consequences.** Parser CODE_VERSION 5, profiler 2.
