## 2026-09-02 — Collapsible sidebar: icon-only rail + collapse-on-ask

### Done this session
- Branched `collapsible-sidebar` off `main` (c1a6d8c); scaffolded and bound the strand.
- Sidebar collapses to a 56px icon-only rail at md+ widths. One `--sidebar-width`
  override on `body.sidebar-collapsed` drives both the sidebar and the content
  offset, since every consumer already read the variable.
- Wrapped all 15 nav labels in `<span class="nav-label">` with `title` tooltips —
  bare text nodes after the icon cannot be hidden by CSS.
- Reused the existing top-navbar button: dropped its `d-md-none` gate and inline
  `onclick`, branched the handler by breakpoint (`.show` off-canvas below md,
  `.sidebar-collapsed` rail above).
- State persists in localStorage, restored pre-paint by an inline script at the
  top of `<body>` so server-rendered nav does not flash expanded on every click.
- Collapse-on-ask on `/test`: bound to `submit` so Enter and the Ask button match;
  guarded on breakpoint and on a non-empty question (the page's own handler bails
  on empty, so a stray Enter would otherwise collapse for no answer).
- New `core/tests/test_web_sidebar.py` — asserts no bare label text survives, in
  both gate branches.
- Verified with the team-mode gate ON: `/` 302, `/test` 200, 3-item nav, no
  Settings group, rail correct on the reduced set.
- Ran `/karpathy-guidelines` as a self-audit and removed two of my own additions
  that traced to no request: `aria-controls` (which had pulled an `id` onto the
  sidebar `<nav>`) and a `font-size: 1.15rem` icon bump.
- User manually confirmed the narrow-window off-canvas case and the feel of the
  sticky collapse-on-ask overwrite.

### In progress
- Nothing — the strand's work is complete pending commit.

### Next
- Commit, push, PR.
- `/land-strand collapsible-sidebar` once merged.

### Flags
- Collapse-on-ask has no automated test: the repo has no JS test harness
  (`node --check` is the only JS tooling present), so the Python suite cannot
  reach it. Verified manually in-browser only.
- 8 pre-existing test failures on this branch, confirmed unrelated by stashing:
  `test_web_config.py` x6, `test_embedding_ollama.py::test_satisfies_embedding_provider_protocol`,
  `test_enrich_overlay_store.py::test_routes_registered`.
