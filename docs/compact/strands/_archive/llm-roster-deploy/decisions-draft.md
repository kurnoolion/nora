## D-DRAFT-1 — Roster entry is independent of the Config-page DB (reverses a DECIDED item)

**Context.** The Ask page's named provider roster bypassed the Config-page DB
except for one borrowed value (`llm_timeout`). The manager brief of 2026-09-03
listed the fix under DECIDED — "Roster sits BELOW the Config-page DB — do not
relitigate" — with item 6 requiring DB values to win over a selected entry.

**Decision (Hanif, 2026-09-03).** The opposite: a selected roster entry owns its
`model`, `base_url`, `api_key` and `timeout` outright, and nothing in the roster
branch reads the DB.

**Why.** A roster entry is a NAMED endpoint ("130B — DGX"), and D-216 chose those
names deliberately so the choice is recognisable. If the DB can override
`base_url`, picking that name can silently send the question elsewhere — the
label then lies, which is worse than having no label. Independence also made the
change smaller: item 6 as briefed would have threaded DB lookups into the roster
branch; independence deleted the one read that was there.

**Consequences.**
- Config-page LLM fields do not affect Ask synthesis while a roster is
  configured. Item 11's labels say so on the fields themselves, including
  `llm_timeout`, which the brief did not list but D-DRAFT-2 makes equally inert.
- Roster credentials come ONLY from the env var each entry names
  (`LLMProviderEntry.api_key` is a property over `os.getenv`). A deployment must
  ship the roster file AND export those vars; miss the second and the endpoint
  401s with nothing local explaining it. Mitigated by D-DRAFT-6.
- **This reverses a DECIDED item and must be visible to the manager before
  merge** — it leads the PR body, not just this file.

## D-DRAFT-2 — Entry `timeout` falls back to DEFAULT_LLM_TIMEOUT, not the resolver chain

**Context.** Item 7 moves `timeout` onto `LLMProviderEntry`. The roster branch
previously borrowed the global via `resolve_llm_timeout()`, which consults the
Config-page DB and `NORA_LLM_TIMEOUT`.

**Decision.** Unset entry `timeout` resolves to `DEFAULT_LLM_TIMEOUT` (600).

**Why.** Routing this one field through the resolver would leave the entry
independent for every field except it — the inconsistency D-DRAFT-1 exists to
remove.

**Consequences.** `NORA_LLM_TIMEOUT` no longer reaches the roster path. Nothing
depends on the old behaviour: no deployment could ship a roster before #18, which
is the bug this strand fixes.

## D-DRAFT-3 — Refusal fallback restored on the roster path (reverses a prior in-code decision)

**Context.** `query.py`'s roster docstring stated the roster provider was
*deliberately* not refusal-wrapped: "the roster names WHICH endpoint answers, so
silently rerouting to a different one would defeat the choice the asker just
made." The effect was that merely configuring a roster removed refusal coverage
from Ask, while the no-roster chain kept it via `create_llm_provider`. Nothing
asserted the wrap on either path, so the gap was invisible.

**Decision.** The roster path is wrapped when the roster names a
`fallback_provider`, and the answering endpoint is disclosed (D-DRAFT-4).

**Why.** The original objection was to a SILENT reroute, not to rerouting. With
the answering endpoint named on the answer card, the asker's choice stays visible
AND a refusal no longer costs them their answer. The docstring arguing the
opposite is rewritten in the same change — leaving it contradicting the code
would be worse than either state.

**Consequences.** Selecting the fallback entry itself is not wrapped in itself: it
IS the fallback, and a refusal there has nowhere to go. Without markers
(`NORA_LLM_REFUSAL_MARKERS`) there is nothing to detect a refusal with, so the
wrap is skipped rather than added as a decorator that can never fire.

## D-DRAFT-4 — Reroute disclosed via the synthesis epilogue, not a new template block

**Context.** Item 9 requires the endpoint that actually answered to be visible.
Two synthesis paths already append a provenance epilogue ("Synthesized by X")
using `answering_model()`, which reads `last_model` and so already reflects a
reroute at the MODEL level.

**Decision.** A companion `reroute_note(llm)` appends "(rerouted to X after Y
declined)" at both existing epilogue sites. Shown only when a reroute happened.

**Why.** Model names alone cannot report a reroute — two roster endpoints may
serve the same model tag, so comparing names would miss it;
`RefusalFallbackProvider.last_was_fallback` records it per call instead.
Extending the epilogue reuses a mechanism that already lands in the answer body
every asker reads, and touches no template. Rejected: a new above-the-fold
template notice, which would have needed a new result key threaded through every
synthesis path for the same visible outcome.

**Consequences.** The note is empty unless the endpoints are NAMED. The roster
path attaches names; the env-var fallback path does not, and inventing a label
for an unnamed endpoint would say less than nothing. Only shown on reroute, so
the common case gains no clutter.

## D-DRAFT-5 — `fallback=` parameter on `maybe_wrap_with_refusal_fallback`

**Context.** Item 8 said to reuse `maybe_wrap_with_refusal_fallback` AND to take
the fallback endpoint from the roster's `fallback_provider` entry. Those conflict
as written: that function sources its endpoint only from `NORA_LLM_FALLBACK_*`
via `build_fallback_provider`.

**Decision.** An optional `fallback=None` parameter; when supplied, endpoint
construction is skipped.

**Why.** Marker parsing, the idempotence check and the mock guard stay in one
place, and the roster concept never enters `core/src/llm/`. Rejected: adding
`base_url`/`model` overrides to `build_fallback_provider`, which pushes
roster-shaped inputs down into the llm module against the brief's "roster is
scoped to the Ask flow" boundary; and constructing `RefusalFallbackProvider`
inline at the call site, which reimplements the marker parse and the
partial-config warning — exactly how two paths drift.

**Consequences.** `NORA_LLM_REFUSAL_MARKERS` stays an env var and does not move
— it is the hand-synced twin of `sandbox/llm_refusal.py` across the D-111
boundary.

## D-DRAFT-6 — `/api/health` reports a source flag, basename and key presence, not the resolved path

**Context.** Item 4 asked for the resolved config path on "/healthz". NORA's web
app has no `/healthz` — that name belongs to the external SIRA service it fetches;
its own endpoint is `/api/health` (`app.py`), which is team-allowlisted
(`team_mode.py`) and polled by the navbar status dot.

**Decision.** Report `llm_config_source` (`env`|`default`), `llm_config_file`
(basename), `roster_size`, `effective_provider`, and `roster_keys` giving per-entry
api_key presence — never the absolute path, never a key value.

**Why.** An absolute container path on a team-allowlisted route hands gated
non-admin users something they otherwise cannot see. A source flag plus basename
answers the actual operator question ("did my NORA_LLM_CONFIG take effect?")
while leaking nothing, and needs no `is_admin` gating or `Request` injection —
the simpler change as well as the safer one. Key presence is the mitigation for
D-DRAFT-1's env-var-only credential path. Rejected: full path gated to admins
(more machinery), and full path for everyone (the leak).

**Consequences.** An operator debugging a wrong-FILE problem gets the filename,
not the directory. Provenance is recorded on `LLMConfigFile` at load rather than
recomputed, because re-resolving per poll would re-log the resolver's warning
every few seconds.

## D-DRAFT-7 — `use_roster=False` for the Eval Studio curation chat

**Context.** Item 10 requires the curation chat to bypass the roster. Passing no
`provider_id` cannot achieve it: after D-DRAFT-8, `resolve_provider(None)` returns
the roster's default entry.

**Decision.** A keyword-only `use_roster: bool = True` on
`_build_llm_from_env_or_default`; the curation route passes `False`.

**Why.** Curation is not the Ask flow. A golden response curated against whichever
endpoint happened to be the roster default would not be reproducible. The Ask
flow's cached default deliberately STAYS on the roster, since a per-request
choice overrides it anyway.

**Consequences.** Two pre-existing `test_web_eval_studio.py` monkeypatch sites
stubbed this function with a zero-arg lambda — they patch exactly this seam.
Their stubs now accept the keyword, and the bypass is covered against the REAL
function in `test_ask_reasoning.py`, per the brief's requirement that new tests
exercise the real builder in both roster and no-roster configurations.

## D-DRAFT-8 — `default_provider` replaces positional-first roster resolution

**Context.** `resolve_provider(None)` returned `providers[0]`, making the default
an accident of list order.

**Decision.** New top-level `default_provider` / `fallback_provider` ids into
`providers`. `resolve_provider(None)` returns the `default_provider` entry, or the
first entry when the key is unset.

**Why.** An explicit key states intent; positional order states nothing. Keeping
the positional fallback means rosters written before the key behave unchanged.
Unknown ids are dropped with a warning at LOAD time rather than raising, matching
`_parse_providers` — a half-edited roster must not fail someone's question, and a
typo in `fallback_provider` surfaces at startup instead of at refusal time.

**Consequences.** Which endpoint the cached pipeline default resolves to changes
when `default_provider` is set. Intended, and stated in the PR rather than left
for a reviewer to notice.

## D-DRAFT-9 — `NORA_LLM_CONFIG` selects the config FILE; a bad value warns and falls through

**Context.** `config/llm.json` is committed and baked into the web image, so no
deployment could supply a roster — 1a3575f reverted an attempt to ship one in the
committed file.

**Decision.** `NORA_LLM_CONFIG` names the file to read;
`DEFAULT_LLM_CONFIG_PATH` becomes the fallback. A value naming a missing or
unreadable file warns loudly and falls through; it never raises.

**Why.** A bad env var must not take the app down. More subtly, it must not
degrade silently to `providers == []`, which is indistinguishable from "no roster
configured" — the operator would see an empty picker and no explanation. By
contrast a missing DEFAULT path stays silent: that IS the normal no-roster case,
and warning there would fire on every deployment that never configures a roster.

**Consequences.** `LLMConfigFile` carries `config_path` / `config_source`, marked
`compare=False` so provenance is not part of config identity — two instances with
the same content stay equal whatever file they came from, which is what existing
equality assertions rely on.
