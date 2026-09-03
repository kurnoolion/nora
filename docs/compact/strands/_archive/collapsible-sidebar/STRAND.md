# collapsible-sidebar

**Status:** landed
**Opened:** 2026-09-02
**Landed:** 2026-09-03
**Assignees:** Hanif
**Target modules:** web
**Active phase:**

## Summary

Make the left sidebar collapsible to an icon-only rail (~56px) so page content can reclaim width. Reuses the existing top-navbar toggle at `base.html:25`, branching its behavior by breakpoint: off-canvas `.show` below `md`, `.collapsed` rail at `md` and up. Collapsed state persists per-browser in localStorage — no new route, so no team-mode allowlist surface. Driven by user feedback on the Web UI.

## Notes


Landed on 2026-09-03 with 4 promoted decisions: D-231, D-232, D-233, D-234.
