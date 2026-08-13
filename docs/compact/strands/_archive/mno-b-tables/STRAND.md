# mno-b-tables

**Status:** landed
**Opened:** 2026-08-12
**Landed:** 2026-08-12
**Assignees:** kurnoolion
**Target modules:** parser, profiler, extraction
**Active phase:** development

## Summary

Team members reported that tables are missing from MNO-B parsed chunks. MNO-B ships all plans/requirements in a single PDF (unlike the per-plan document layouts of the other MNOs), so its profile-driven parse path is structurally different. Debug where table content is being dropped — profile, parser, or downstream extraction — and fix it so table content survives into the chunk stream.

## Notes

<!-- appended to over the strand's lifetime -->

Landed on 2026-08-12 with 3 promoted decisions: D-183, D-184, D-185.
