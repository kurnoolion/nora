# Self-serve Ingestion Guide

How the requirements team brings a new release of an MNO that NORA
already serves into the system, from the web page, without a shell.
One page: what you need, what to type, what you see, what to do when
it stops. The administrator's side (host setup, the runner, what the
page runs underneath) is `docker/README.md` §"Self-serve ingestion";
the design is `docs/architecture.md` §28.

## What you need

- **The unlock link**, once per browser: `<web>/ingest-unlock?token=…`.
  The administrator gives it to you. It sets a cookie; after that the
  Ingest entry appears in the sidebar and the page opens.
- **The documents in a shared folder** you can see from Windows, one
  folder per release: every `.pdf`, `.docx` and `.xlsx` in it is taken,
  nothing in sub-folders. The folder stays as it is; the system copies.
- **An MNO NORA already serves.** The page's MNO list is exactly that.
  A new MNO's first release is the administrator's job: it needs a
  profile choice and derived prompts before any page can run it.

## Submit

1. Open `<web>/ingest`.
2. Paste the folder's path as Windows shows it, `\\SERVER\Share\…`.
3. Pick the MNO; type the release as `MMMYYYY` (for example `Apr2031`);
   type your name; a note if you like.
4. Submit. The page answers with a queue position. Submits queue. The
   Runner box says how many ingests run at once: with one, a position
   above 1 means waiting for the ones before yours; with several, each
   MNO has one ingest in flight at a time, so a release of an MNO whose
   last ingest is still running or staged waits until that one is
   served, abandoned or failed (the list says "waits for …"). A release
   that is already served, or already queued, running or staged, is
   refused with a message saying so.
5. When several run at once, another MNO's ingest may be served while
   yours is staged. Serve then publishes yours again on the new served
   set and runs the check again before serving it: a few minutes more,
   and the page shows the new label.

## What you see

The ingest's own page shows twelve stages as pills, the current one
highlighted, and the job log underneath with every command the system
runs. Enrichment is the long stage, about an hour; the rest take
seconds to minutes. The whole run takes about an hour and a half.

Under the stage pills, badges say where each early stage's result came
from. **Found** means another build on this host had already done that
stage on the same documents with the same settings, so it was reused in
seconds. **Computed** means it ran now.

At the end the ingest is **staged**: built, published to a staging
store, and measured by the golden eval. The Gate card shows the
verdict. **Pass** means the eval's recall over the staged set is
within tolerance of the last gated run. **Fail** means it is not, or
the eval hit an error; the card says which.

Nobody has to watch the page. Leave it open and it updates on its
own; come back later and the state is there. A chat or mail bridge can
be told of each state change (the administrator sets that up).

## Serve, roll back

A staged ingest whose gate passed has a **Serve** button. Serve makes
the new release visible to everyone on the Ask page, in minutes. The
previous set stays available: the page then offers **Roll back**, one
click, which puts the previous set back. Rolling back does not undo
the build; the release can be served again later.

A **served** ingest has a **Redo** button. Redo runs the release's
taxonomy and enrichment again, as a new ingest with its own page (about
an hour); the documents and the early stages are reused. Use it when
the enrichment came out wrong, for example after a prompt fix. The
original stays served until you serve the redo, and a failed or
abandoned redo leaves it served.

A staged ingest whose gate **failed** has no Serve button. Tell the
administrator what the Gate card says; they can look at the eval,
and they can serve with a recorded waiver if the failure is understood.

## When it stops

- **Failed at a stage.** The page names the stage and the error. **Retry**
  runs again from that stage (a verify failure retries from enrichment).
  Most failures are transient: a share that was unreachable, a model
  that was busy. Retry once; if it fails the same way, tell the
  administrator with the ingest's link.
- **Refused at submit.** The message says why: the path is not a
  Windows share path, the release is not `MMMYYYY`, the MNO is not
  served, or the release is already there. Fix the field and submit
  again.
- **A failed ingest ahead of yours** (one ingest at a time only). The
  page names it; the queue moves once it is retried or abandoned. Ask
  its owner, or the administrator. With several at once, a failed ingest
  holds nothing back.
- **Abandon.** A failed or cancelled ingest, or a staged one you do not
  want, can be abandoned: the cycle is closed and the staged copy of the
  documents is removed. Its build stays for the administrator to look
  at. **Purge**, which removes every trace of a dead ingest, is the
  administrator's button; ask them when you want one gone for good.
- **Cancel** stops a queued or running ingest between stages. A stage
  already running finishes first.

## What not to expect

- A new MNO. Ask the administrator.
- A second ingest of the same release from the form. To build a served
  release again, use **Redo** on its page; the form refuses while the
  release is served or in flight.
- Changes to what the documents say. Ingestion takes the folder as it
  is; corrections to parsed or enriched content are the review pages'
  job (`docs/enrichment-review-guide.md`).
