# mno-b-tables

**Status:** in-flight
**Opened:** 2026-08-12
**Landed:**
**Assignees:** kurnoolion
**Target modules:** parser, profiler, extraction
**Active phase:** development

## Summary

Team members reported that tables are missing from MNO-B parsed chunks. MNO-B ships all plans/requirements in a single PDF (unlike the per-plan document layouts of the other MNOs), so its profile-driven parse path is structurally different. Debug where table content is being dropped — profile, parser, or downstream extraction — and fix it so table content survives into the chunk stream.

## Notes

<!-- appended to over the strand's lifetime -->
