# decision

**Purpose**
Decision models — typed `noul` / `choice` / `score` inference with no token generation. A decision model has a classification head rather than a token decoder, so it answers in a single forward pass and its latency does not scale with the number of options. This module owns the abstraction and the query-classification application of it. Strand jev-experimentation; design `docs/compact/strands/jev-experimentation/decision-model-design.md`.

Separate from [llm](../llm/MODULE.md) deliberately: a different abstraction (typed heads, not completions), and one-package-per-concern is the convention here.

**Public surface**
- `DecisionProvider` (base.py) — `@runtime_checkable Protocol`; the only decision-model interface the rest of the project sees. Attribute `model`, method `decide(state, questions) -> list[DecisionAnswer]`. Shaped on `/v1/systemone`: a batch of typed questions evaluated in parallel against one shared `state`. Implementations may be HTTP clients, in-process adapters, or mocks — a candidate without a `/v1/systemone` endpoint satisfies this as a thin adapter rather than an HTTP hop.
- `DecisionQuestion(question_id, kind, question, options=())` (base.py) — frozen. `kind` is `noul` | `choice` | `score`; `options` is required for `choice` and `score` and rejected for `noul` (whose two options are implicit), validated at construction. `question` and `options` serialise as the wire format's `instructions` and `criteria`.
- `DecisionAnswer(question_id, value, confidence=0.0)` (base.py) — frozen. Matched back to its question by `question_id`, never by position, so a provider may reorder or parallelise. `confidence` is the calibrated probability where the provider reports one, and `ConfidenceRoutedQueryAnalyzer` acts on it: measured on `classify-dev-v1`, mean 0.425 on correct answers against 0.268 on incorrect ones, so it carries signal about its own correctness.
- `DecisionKind` (base.py) — `Literal["noul", "choice", "score"]`. A yes/no question is a **`noul`**; a server validates `type` against this literal and 422s anything else, so the spelling is load-bearing. Verified against `kev/api.py` at jaredpalmer/kev `1f04cfa`.
- `DecisionError` (base.py) — a provider could not answer. Callers degrade rather than fail.
- `SystemOneDecisionProvider(base_url, model="", api_key="", timeout=30)` (systemone_provider.py) — HTTP client for an in-corp `/v1/systemone` endpoint; stdlib `urllib` only, no SDK, the posture that kept `OpenAICompatibleProvider` vendor-neutral. `model`, `call_count`, `last_latency_ms`, `last_server_latency_ms` (the server's own figure; the difference from `last_latency_ms` is network, which is what tells a slow model from a slow hop). The wire schema is **verified** against `kev/api.py` at jaredpalmer/kev `1f04cfa`: `questions` and `answers` are objects keyed by question id, a question carries `instructions` and `criteria`, and an answer keeps its value under a field named after its type (`choice` / `noul` / `score`) rather than a generic `value`. The first draft guessed all four wrong. The endpoint resolves to `/v1/systemone` whether or not `base_url` already ends in `/v1`. The parse still fails loudly and names the keys the server actually sent. An empty `model` is omitted from the payload, meaning "the server's own default".
- `assert_remote_head(base_url, timeout=10, api_key="") -> dict`, `assert_decision_head_present(model_dir) -> dict`, `inspect_weights(model_dir)`, `confidence_is_calibrated(answers)`, `REQUIRED_HEAD_FILES`, `ADAPTER_MARKERS`, `REMOTE_PROBE_PATH` (head_check.py) — the design §7 gate, in two halves. `assert_remote_head` is the authority: it GETs `/v1/models` and raises unless the server reports both an adapter and a calibration temperature, returning the model card to stamp on the run. It carries the same bearer token the decisions do — a probe that cannot authenticate fails on exactly the servers configured correctly — and it diagnoses a 401/403 **separately from unreachability**, because an `HTTPError` is a `URLError` subclass and folding the two reported a healthy server as down (DRIFT-3). It is the only check that runs over the connection the decisions themselves use, and it fails on an unreachable host instead of letting a caller degrade into the rule-based baseline. `assert_decision_head_present` is the local file check, now optional: a head-bearing distribution must carry `head.pt` (which holds the calibration temperature inside it) and a decision adapter, and a GGUF-only directory raises with the hollow-build diagnosis. It applies only where the weights actually sit, which for a remote server is not the eval client. `confidence_is_calibrated` is the weakest signal — an all-zero confidence vector is the shape a stripped build produces — and informs a warning, never a hard failure; it is uninformative for a noul-only response, which carries no `confidence` field.
- `MockDecisionProvider` (mock_provider.py) — deterministic keyword stub, offline, no network; `model` (`"mock"`), `call_count` (requests, not questions). What the `decision` knob value resolves to when no real provider is configured, so the seam is exercisable with nothing installed. `choice` returns the first option named in the state, else the LAST option; `noul` returns 1.0 when a non-stopword capitalised token of the question appears in the state, mirroring the wire format's probability-of-yes rather than a bool; `score` is a constant 0.5.
- `DecisionQueryAnalyzer(provider, fallback=None, confidence_floor=0.0)` (analyzer.py) — `analyze(query) -> QueryIntent`. Decision model for `query_type` and `doc_type_scope`; the rule-based analyzer for everything else. `call_count` and `fallback_count` — queries served, and queries served entirely from the rules because the provider raised. `coverage(question_id) -> float` plus the `routed_to_model` / `routed_to_rules` counters report how much of each field the model actually answered. At the default floor of 0.0 every answer is kept, which is the policy phase 3's `decision` arm measured.
- `ConfidenceRoutedQueryAnalyzer(provider, confidence_floor, fallback=None)` (analyzer.py) — the same machinery with the floor stated as a required argument. Keeps the model's answer where its calibrated confidence clears the floor and lets the rules serve the rest, **per field**: both answers travel in one request but route independently, so a field the model is sure of is not discarded because another field was uncertain. Named separately from `DecisionQueryAnalyzer` because it is a different policy, not a different implementation, and `--arm confidence-routed` / `NORA_QUERY_ANALYZER=confidence-routed` name this class and nothing else. The floor's default of 0.50 is the value measured in `phase-3-measurement.md` and is a **starting point, not a validated setting** — it was selected in-sample on n=50.
- `QUERY_TYPE_DESCRIPTIONS: dict[QueryType, str]`, `DOC_SCOPE_DESCRIPTIONS: dict[DocTypeScope, str]` (analyzer.py) — one-line glosses per option, keyed by the enum so a new member fails a test rather than silently shipping a bare identifier. Carried as the wire format's `criteria` values. **Load-bearing, not documentation:** measured on `classify-dev-v1` against a live Kev-4B, bare identifiers scored 0.460 and the same options glossed scored 0.760 — the difference between "worse than the rules" and "level with them".
- `VALUE_KEY_BY_KIND: dict[str, str]` (systemone_provider.py) — which response field carries an answer's value for each `DecisionKind`. The wire format names it after the type (`choice` / `noul` / `score`) rather than using a generic `value`, verified against `kev/api.py` at `1f04cfa`.
- `QUERY_TYPE_QUESTION`, `DOC_SCOPE_QUESTION`, `CLASSIFICATION_QUESTIONS` (analyzer.py) — the two choice questions sent in one request, options generated from `QueryType` and `DocTypeScope`.

Auth: `NORA_DECISION_API_KEY` / `--decision-api-key`, empty by default. Empty omits the `Authorization` header entirely rather than sending a literal `Bearer `, because an open in-corp server is the normal case. The reference Kev server is **open unless `KEV_API_KEY` is set**, so a key on the server with none configured here used to make classification degrade to the rules with a warning and keep serving.

Selected by the four-valued `NORA_QUERY_ANALYZER` knob (`rules` | `llm` | `decision` | `confidence-routed`) — see [query](../query/MODULE.md)'s `make_query_analyzer_from_knob` and `env.config.resolve_query_analyzer` / `resolve_decision_provider` / `resolve_decision_confidence_floor`.

**Invariants**
- **No external egress.** Every decision provider is self-hosted in-corp. Phase 2 ships no HTTP transport at all — importing this package cannot cause a network call.
- **Option lists are generated from their enums, never hand-written.** A hand-listed option set is exactly how `LLMQueryAnalyzer`'s prompt came to omit `summarize` and `fact` (design §2.1). Adding a `QueryType` member extends the question with no edit here.
- **`QueryIntent` is the contract and does not change.** `DecisionQueryAnalyzer` returns the same shape the rule-based analyzer does; a test asserts the field sets match.
- **A provider failure degrades, never fails.** Any exception from a provider falls back to the rule-based `QueryIntent` whole; an answer that is absent or not a member of its enum falls back **per field**, so one bad answer does not discard a good one. A classifier outage degrades classification; it does not fail the query.
- **Answers are matched by `question_id`.** Never by list position.
- **A measurement never gets recorded against an unverified head.** Three gates, because the same silent degradation arrives three ways. `assert_remote_head` covers the server: it asks `/v1/models` what was actually loaded, over the connection the decisions will use, and raises on an unreachable host. `assert_decision_head_present` covers local weights where they exist: a GGUF or backbone-only build answers without erroring, so a number recorded against it measures the backbone. `fallback_count` covers the transport mid-run: per-query fallback is the right *serving* behaviour but makes a dead provider look like a successful run with the rule-based numbers — `eval.classify_cli` refuses any non-zero count under a `decision` label. Found the hard way during phase 3: a verified head plus an unreachable server reported the rules baseline, 0.720 / 0.136, as the decision arm's result. The local check was the one that gave false assurance, which is why it is no longer the gate.
- **Routing is not a fallback, and the two counters stay separate.** `fallback_count` means the provider was unreachable and the arm is contaminated; a routing means the provider answered and the policy declined the answer. Conflating them would make a healthy routed run trip the `eval.classify_cli` refusal, and would hide a dead provider inside a plausible-looking coverage figure. An unanswered or unparseable field counts as routed to the rules, so coverage never credits the model for a value that was discarded.
- **Free-text fields are not expressible here.** A decision model emits only `noul` / `choice` / `score`, so `entities`, `concepts`, `releases` and `standards_refs` stay with the regex extractors permanently — not as a phase limitation but as a property of the category.

**Key choices**
- `/v1/systemone` as the wire shape, not a bespoke interface: it is the de-facto decision-model format, so a candidate swap is a config change. Same reasoning that kept the LLM roster vendor-neutral behind `/v1/chat/completions` with no SDK dependency.
- `DecisionQueryAnalyzer` composes `MockQueryAnalyzer.analyze()` rather than reusing its private `_extract_*` methods. One call fills every open-extraction field and yields a complete fallback value for free; the wasted rule classification costs microseconds. This also means `query` imports `decision` only function-locally (in `make_query_analyzer`), since `decision` imports `query` at module level — that function-local import is what breaks the cycle.
- Both classification questions travel in one `decide()` call. Parallel questions against one shared state is the mechanism the wire format exists for, so two round trips would waste it.
- **Two names, one implementation, for the two routing policies.** `confidence-routed` is spelled identically as the knob value, as `--arm`, and as the class — one spelling, so a configuration reads as the policy in force rather than requiring an operator to infer it from a second knob. The alternative considered and rejected was a single `decision` value plus a floor: a floor of 0.0 *is* the unrouted policy, so the policy would have been implicit in a number. The word `hybrid` was retired in the same change — `HybridQueryAnalyzer` meant "model for labels, regex for spans, rules on error", which readers consistently mistook for confidence routing.
- **Routing is per field, not per query.** Each answer carries its own confidence, and the two questions travel in one request, so there is no cost to routing them independently. This also subsumes a recommendation the phase-3 measurement had reached by hand — keep `doc_type_scope` on the rules, where the rules score 1.000 against the model's 0.760 — without hard-coding it: at any floor from 0.30 up, scope accuracy is 1.000 because the model's scope answers are the low-confidence ones.
- The three closed-vocabulary multi-selects (`mnos`, `likely_features`, `plan_ids`) are decision-model-ownable as one `noul` question per candidate — 24 booleans plus 2 choices against one state, well inside the concurrency these servers document — but stay on the rules until phase 3 so the first measurement has one variable. A test asserts `CLASSIFICATION_QUESTIONS` still has exactly two entries, so adding them is a deliberate act.

**Non-goals**
- **Not a text generator.** A decision model has a classification head, not a token decoder. It cannot produce an answer, a summary or a rewritten query — anything free-form stays with [llm](../llm/MODULE.md).
- **Not a replacement for the regex extractors.** `entities`, `concepts`, `releases` and `standards_refs` are free text, which this category cannot emit. They stay with the rule-based analyzer permanently — a property of the category, not a phase limitation.
- **No external egress, and no hosted decision API.** Every provider is self-hosted in-corp. Jev itself — the closed-weights hosted service this category is named for — is deliberately not a candidate, which is why the wire format was adopted while the vendor was not.
- **No training or fine-tuning here.** Adapting a decision model is upstream tooling (`kev.train`); this module consumes weights and never produces them. The fine-tuned arm is unrun and conditional on the design's §8 open point 3.
- **Not the retrieval policy.** This module answers *what kind of question is this*; what the pipeline does with the answer — top-k, BM25 weight, grouping — lives in [query](../query/MODULE.md)'s pipeline and is unchanged by the classifier arm.
- **No decision seams beyond classification yet.** Reranking and the other small LLM-backed decisions are the reason the protocol is generic, but `CLASSIFICATION_QUESTIONS` has exactly two entries and a test asserts it, so adding one is a deliberate act rather than a drift.
- **Does not own the confidence floor.** `ConfidenceRoutedQueryAnalyzer` applies a threshold; choosing a defensible one needs held-out labelled traffic and belongs to whoever has it.

**Depends on**
[query](../query/MODULE.md) (`MockQueryAnalyzer` for the open-extraction fields and the fallback; `QueryIntent` / `QueryType` / `DocTypeScope` as the contract).

**Depended on by**
[query](../query/MODULE.md) (`make_query_analyzer` constructs `DecisionQueryAnalyzer`). This edge is **function-local**, not a module-level import — the two modules would otherwise form a cycle, and the local import in the factory is what breaks it.

<!-- BEGIN:STRUCTURE -->
_Alphabetical, regenerated by regen-map._


`analyzer.py`
- `CLASSIFICATION_QUESTIONS` — constant — pub
- `ConfidenceRoutedQueryAnalyzer` — class — pub — The decision model where it is confident, the rules elsewhere.
  - `__init__` — constructor — internal
- `DOC_SCOPE_DESCRIPTIONS` — constant — pub
- `DOC_SCOPE_QUESTION` — constant — pub
- `DecisionQueryAnalyzer` — class — pub — Decision model for the choice fields, regex for the spans.
  - `__init__` — constructor — internal
  - `_count` — staticmethod — internal
  - `_field` — method — internal — Resolve one field, applying the confidence floor.
  - `analyze` — method — pub — Analyze a query into structured intent.
  - `coverage` — method — pub — Fraction of this field's answers that came from the model.
- `QUERY_TYPE_DESCRIPTIONS` — constant — pub
- `QUERY_TYPE_QUESTION` — constant — pub
- `_coerce` — function — internal — Parse one answer into its enum, returning ``fallback`` when the
- `logger` — constant — pub

`base.py`
- `DecisionAnswer` — dataclass — pub — One answer.
- `DecisionError` — class — pub — A decision provider could not answer.
- `DecisionKind` — constant — pub
- `DecisionProvider` — class — pub — The only decision-model interface the rest of the project sees.
  - `decide` — method — pub — Answer every question against one shared state.
- `DecisionQuestion` — dataclass — pub — One typed question against a shared state.
  - `__post_init__` — method — internal

`head_check.py`
- `ADAPTER_MARKERS` — constant — pub
- `REMOTE_PROBE_PATH` — constant — pub
- `REQUIRED_HEAD_FILES` — constant — pub
- `assert_decision_head_present` — function — pub — Raise unless ``model_dir`` holds a head-bearing distribution.
- `assert_remote_head` — function — pub — Raise unless the server at ``base_url`` is serving a calibrated head.
- `confidence_is_calibrated` — function — pub — Whether the answers look like they came from a calibrated head.
- `inspect_weights` — function — pub — Describe what a weights directory contains, without judging it.
- `logger` — constant — pub

`mock_provider.py`
- `MockDecisionProvider` — class — pub — Keyword-matching stub. Deterministic, offline, no network.
  - `__init__` — constructor — internal
  - `_answer` — method — internal
  - `_keyword_hit` — staticmethod — internal — 1.0 when any capitalised or all-caps token of the question
  - `decide` — method — pub
- `_MOCK_SCORE` — constant — internal
- `_QUESTION_STOPWORDS` — constant — internal
- `logger` — constant — pub

`systemone_provider.py`
- `SystemOneDecisionProvider` — class — pub — Decision provider over an in-corp `/v1/systemone` endpoint.
  - `__init__` — constructor — internal
  - `_endpoint` — property — internal — The `/v1/systemone` URL, whether or not `base_url` already ends
  - `_parse` — method — internal
  - `decide` — method — pub — Answer every question against one shared state in one request.
- `VALUE_KEY_BY_KIND` — constant — pub
- `logger` — constant — pub
<!-- END:STRUCTURE -->
