## D-DRAFT-1 — Sidebar collapse state persists in localStorage, not server-side

**Context.** The collapsed rail has to survive navigation: NORA's nav is
server-rendered Jinja, so every click reloads the page and an in-memory flag
would reset constantly.

**Decision.** Persist the collapsed state per-browser in localStorage, read back
by a pre-paint inline script in base.html.

**Why.** A server-side per-user preference would need a new route, and in this
codebase a new route is not a small thing — it lands in the `web/team_mode.py`
gate allowlist and owes a gate-ON verification per the CLAUDE.md branch flow.
That is a large integration surface for what is a per-browser view preference
with no cross-device value.

**Consequences.** The preference does not follow a user across browsers or
devices, and is lost when site data is cleared. Every localStorage access is
wrapped in try/catch so blocked storage degrades to "toggle works, does not
persist" rather than a dead button. No route, no allowlist entry, no MODULE.md
route-contract change.

## D-DRAFT-2 — Asking a question collapses the sidebar stickily

**Context.** Answers on /test run long and render NORA and SIRA lanes
side-by-side, so the 240px sidebar costs real reading width exactly when the
user stops needing the nav.

**Decision.** Submitting the ask form collapses the sidebar and writes the
localStorage preference — the same effect as tapping the toggle. Bound to the
form's `submit` event, not the Enter key, so the Ask button behaves identically.
Guarded on the breakpoint and on a non-empty question.

**Why.** Chosen over a transient reading mode (collapse without writing the
preference) and over a variant that auto-collapses only when the user has never
touched the toggle. Both alternatives were surfaced with the overwrite cost
named. Sticky won on being the simplest code with one state to reason about:
the toggle and the ask path write the same preference by the same route, and
there is no second, transient notion of "collapsed" to keep straight.

**Consequences.** A user who deliberately keeps the sidebar expanded loses that
choice the first time they ask anything, with no notification — this was raised
before implementing and accepted. The manual toggle remains the way back, and
the state is per-browser as in D-DRAFT-1.

## D-DRAFT-3 — Rail CSS is scoped to a media query rather than managed by a resize listener

**Context.** The sidebar already had an off-canvas behaviour below 768px
(`.sidebar.show`, translateX). Adding a desktop rail meant two behaviours on one
control, and a user who collapses at desktop width can then narrow the window.

**Decision.** Every rail rule lives inside `@media (min-width: 769px)`, the exact
complement of the existing `max-width: 768px` block. The JS only branches on
`matchMedia("(min-width: 769px)")` at click time.

**Why.** The alternative was a `matchMedia` change listener that strips the
class when the viewport narrows. Scoping the CSS instead makes a stale
`.sidebar-collapsed` class simply inert below the breakpoint — the state can be
wrong without the rendering being wrong, so there is no listener to forget, and
no ordering problem between the class and the breakpoint.

**Consequences.** The two breakpoint values must stay in step: 768px in the
existing mobile block, 769px in the rail block and in the JS matchMedia string.
A future change to one is a change to three.

## D-DRAFT-4 — Nav labels wrapped in spans rather than clipped by CSS

**Context.** Sidebar labels were bare text nodes after each icon
(`<i class="bi ..."></i>Dashboard`). CSS cannot target a bare text node, so
hiding labels in the rail required a structural change to base.html.

**Decision.** Wrap all 15 labels in `<span class="nav-label">` and add a `title`
to each anchor.

**Why.** The alternative — clipping with `overflow: hidden` on a 56px sidebar —
needs no template edit, but at that width it leaves a sliver of the first letter
visible next to the icon, and it provides no hook for a tooltip. Icon-only
navigation needs tooltips: two items (Corrections, SIRA Enrichment Review) share
`bi-pencil-square`, and the `title` is the only thing distinguishing them.

**Consequences.** base.html's sidebar markup now has a required shape — a new nav
item that forgets the span will show a stray label in the rail. This is what
`core/tests/test_web_sidebar.py` exists to catch, in both gate branches. The
duplicate icon was deliberately left as-is rather than reassigned.
