# Redaction rules — what never goes in a commit

The repo is a shared, portable artifact. Anything describing *our* deployment —
endpoints, carriers, plan codes, product names, document content — is
operational state, not code, and stays out of every commit: code, tests,
comments, docs, strand journals, decision drafts, commit messages. These rules
exist because the repo outlives and outtravels the machines it runs on.

## The categories

| Never commit | Use instead |
|---|---|
| Real IPs, hostnames, URLs, ports | `127.0.0.1:PORT`, `example.test`, `<serving-host>`, `<endpoint-32b>` |
| Carrier / operator names | `<MNO>`, `MNO-A`, `MNO-B` |
| Real plan codes, requirement numbers, document numbers | `<plan>`, `<doc_id>`, invented codes that are obviously fictional |
| Proprietary model / product names | describe the infrastructure: "the internal 32B endpoint", `<serve-label>` |
| API keys, tokens, credentials | the **name** of an env var (`api_key_env`), never a value |
| Requirement text, query text, document excerpts — in logs, error messages, reports, fixtures | counts, percentages, error codes, digests (the paste-safe rule, NFR-8) |
| Filesystem paths from a real deployment | `<env_dir>`, `/data/env/...` (container paths are fine — they're ours by construction) |
| Internal workflow codenames, role names, org/team-dynamics framing | technical framing only — describe the change, not the process that produced it |

Committed example configs stay **fictional end to end**: every entry in an
`.example` file must be an address, model tag, and id that resolves nowhere.
If an example is copied from a working config, redact it *before* it enters
the working tree, not at review time.

## Where real values live

- Machine-local, gitignored files: env files, `~/.nora/`, per-clone state.
- Deployment directories outside the checkout (`<env_dir>`, `/srv/nora/env/`).
- Chat / session conversation — fine to discuss, never to paste into a file
  that gets committed.

## The trap to watch: evidence

Almost every leak arrives as **quoted evidence** — probe output, error bodies,
`curl` transcripts, config snippets pasted into a journal or decision draft to
support a finding. The finding is committable; the transcript usually is not.
Rewrite evidence in terms of *behavior*: "endpoint A returns 400
`extra_forbidden`, endpoint B returns 400 `literal_error`" carries the full
technical content with zero infrastructure identifiers. If the exact bytes
matter, keep them in a gitignored local note and cite the behavior in the repo.

## Allowed, by standing convention

- Teammate names in COMPACT files (`Assignees:` fields, journal attribution).
- Release-label keys of the form `<Mon><Year>` in **core code and tests only**
  (where they're schema, not disclosure) — never in ops docs or operational
  narratives.
- `localhost` / `127.0.0.1`, container-internal paths, and anything from a
  committed `.example` file — these are ours by construction.

## Self-check before pushing

Run against your branch diff, additions only:

```sh
# IP-shaped strings (loopback excluded)
git diff origin/main...HEAD | grep -E '^\+' \
  | grep -nE '([0-9]{1,3}\.){3}[0-9]{1,3}' | grep -v '127\.0\.0\.1'

# URLs that aren't obviously placeholders
git diff origin/main...HEAD | grep -E '^\+' | grep -inE 'https?://' \
  | grep -viE 'example\.|localhost|127\.0\.0\.1|<[a-z0-9-]+>'

# key-shaped strings
git diff origin/main...HEAD | grep -E '^\+' \
  | grep -inE '(api[_-]?key|token|secret)["'"'"']?\s*[:=]\s*["'"'"'][^"'"'"']{8,}'
```

An empty result from all three is necessary, not sufficient — the greps catch
shapes, not names. Also re-read, by hand, any line where you quoted output
from a real machine, and search your diff for the carrier names, plan codes,
and product names you actually worked with that day (you know them; the grep
doesn't).

If something sensitive was already pushed on a branch: say so before the
merge. A single-commit branch can be recreated redacted so main's history
stays clean; silence is the only unrecoverable version.
