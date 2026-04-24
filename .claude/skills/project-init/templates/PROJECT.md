# Project: <name>

*Identity: who / why / scope boundaries. Behavioral specs (FR / NFR) live in `requirements.md`.*

**One-line**: Single-sentence elevator pitch.

**Problem**: 1-2 paragraphs. What pain is this solving, for whom?

**Users**: Brief description of who uses this.

**In scope for v1**:
-

**Out of scope (explicit non-goals)**:
-

**Success criteria**: How do we know v1 is done?

**Open questions** *(maintained during Requirements phase; removed when resolved or deferred)*:
-

**Contributors**:

| Stakeholder / Role | Contributes | Interface | Feedback loop |
|---|---|---|---|
| *e.g. Dev team* | Code, design, reviews | Direct git | Normal PR flow |
| *e.g. QA team* | Eval sets, failure cases, test data | `contributions/eval/*.yaml` drop | Ingested by CI on merge to main |
| *e.g. TPM* | Requirements clarifications, priority calls | Web UI at `/admin/requirements` | Staged edits → dev review → `PROJECT.md` |
| *e.g. Domain expert* | Corrections on AI output, ground-truth labels | Structured form w/ diff view | Writes to `contributions/corrections/`, reviewed at close-session |
| *e.g. End user* | UI feedback, bug reports | In-app feedback widget | Funneled to issue tracker, triaged weekly |

*Every row names who, what they contribute, how they submit it, and how it reaches the system. Missing rows — unowned validation, no correction path for AI output, no eval-data channel — are v1 risks; call them out in Open questions above.*
