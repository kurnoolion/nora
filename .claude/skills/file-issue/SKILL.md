---
name: file-issue
description: File a GitHub issue for this project, with a public-vs-internal remote check before anything is published. Resolves the target repo, gates on visibility, drafts the body for user approval, then creates the issue via `gh`. Use when the user wants to raise a bug, discrepancy, or task as a tracked GitHub issue.
---

File a GitHub issue against this project's remote — without accidentally publishing
internal detail to a public mirror.

## Argument

Free-text topic (optional). If omitted, ask what the issue is about.

## Procedure

### 1. Resolve the target repo

```
git remote -v
gh repo view <owner>/<repo> --json visibility,hasIssuesEnabled,viewerPermission
```

Record: remote URL, visibility, issues-enabled, permission level.

- `hasIssuesEnabled: false` → stop, say so.
- `viewerPermission` not WRITE / MAINTAIN / ADMIN → stop, say so.
- More than one remote → ask which, do not guess.

### 2. Visibility gate

**If visibility is PUBLIC**, stop and tell the user plainly, before drafting:

> "`<owner>/<repo>` is PUBLIC. An issue filed here is world-readable and indexed,
> and stays cached even if deleted later. Proceed?"

Name anything in the intended content that is internal-only: paths under a home
directory, machine names, credentials, customer or corpus identifiers,
history-rewrite detail, proprietary plan codes.

Project note: `github.com/kurnoolion/nora` is the deliberately scrubbed public
mirror — see the 2026-05-09 flag in `docs/compact/STATUS.md`. Company-internal
material does not belong there.

Wait for explicit confirmation. Never draft-and-post in one step.

### 3. Draft the body

Write to a temp file, not inline. Structure:

- **Summary** — one paragraph: what is wrong
- **Detail** — evidence, as a table where it fits; quote exact strings and paths
- **Mitigating factors** — anything reducing severity; state plainly if there is
  no active defect
- **Suggested action** — numbered options, with a recommendation
- **Scope** — what is and is not affected

Include only what a reader needs in order to act. Omit background that raises
exposure without helping the fix.

Show the draft. Get approval on the wording before creating anything.

### 4. Create

```
gh issue create --repo <owner>/<repo> \
  --title "<title>" --body-file <tmpfile> [--label <existing-label>]
```

Pick labels from `gh label list` — do not invent them.

Leave unassigned unless the user supplies a GitHub handle. Assignees must have
repo access or the assignment silently fails.

### 5. Verify and report

```
gh issue view <n> --repo <owner>/<repo> \
  --json number,title,url,state,labels,assignees
```

Report URL, label, assignee state. If the issue number is `1`, mention that issues
were not previously in use on this repo — the team may track work elsewhere.

## Rules

- Public repo → step-2 confirmation is mandatory. No exceptions.
- Never invent labels or assignees.
- Draft to a file and show it. The user approves wording before publication.
- Publishing is not reversible. Deleting an issue does not un-index it.
- Read-only until step 4. Steps 1-3 create nothing outward-facing.
