# Provisioning Checklist — build machine and serving host

Architect-run (sudo) setup for the two operator machines. Each step
has a probe; a machine is provisioned when every probe passes, and the
probes double as the diagnostic when an operator reports "it worked
last week". The build machine is greenfield; the serving host is
migrated in place (last section) once the running eval campaign is done.

Layout both machines converge on (`NORA_ROOT=/srv/nora`):

```
/srv/nora/
├── requirements/     # NAS mount (build machine)         — source docs, read-only for jobs
├── archive/          # NAS mount (both)                  — retired builds / labels / images
├── builds/{nora,sira}/<build-id>/   # build machine      — local disk (SQLite inside)
├── serve/<label>/    # both (push target on the serving host)
├── eval/golden/      # serving host                      — pooled golden set + runs
├── web-state-<stack>/ feedback/ corrections/ models/     # serving host, per README layout
└── env/              # both — wiring + runtime env files, 0640 root:nora-ops, NEVER on the share
```

Rule of thumb from the design: file-shaped, cold, or exchanged data may
live on the NAS; anything database-shaped or hot (builds, cells,
vector stores, web-state, serve labels) stays on local disk —
SQLite over NFS is a corruption hazard, and hardlink promotion needs
one local filesystem.

## A. Build machine (greenfield)

### A1. OS + base tools

- Ubuntu LTS (server), static hostname, NTP on.
- `sudo apt install -y git rsync curl python3 openssh-server`
  — `python3` on the HOST is required: `cycle.sh` uses it to write
  `CYCLE.json`; `pull.sh` uses it to parse release metadata.
- Probe: `python3 --version && rsync --version | head -1 && git --version`

### A2. docker-ce from the official repository — not snap, not `docker.io`

Snap docker confines bind mounts to `/home` (so `/srv/nora` would be
invisible to containers) and auto-refreshes dockerd, killing detached
jobs. Install docker-ce per Docker's Ubuntu instructions (apt repo +
`docker-ce docker-ce-cli containerd.io docker-compose-plugin`).

- Probe (install source): `docker info --format '{{.OperatingSystem}}'`
  says Ubuntu, and `snap list 2>/dev/null | grep -c docker` is 0.
- Probe (bind mount outside /home): 
  `mkdir -p /srv/nora && docker run --rm -v /srv/nora:/x alpine ls /x`
  lists the directory (no permission or "not shared" error).
- Probe: `docker compose version` reports v2.
- Probe (auto-restart of jobs is NOT wanted): `systemctl show docker
  -p Restart` — unattended restarts of dockerd come only from
  `apt upgrade`; pin `docker-ce*` (`apt-mark hold`) and upgrade in
  announced windows.

### A3. Group, directories, permissions

```bash
sudo groupadd -g <fixed-group-id> nora-ops           # pin the numeric id — must match the serving host AND the NAS export
sudo mkdir -p /srv/nora/{builds/nora,builds/sira,serve,env}
sudo chown -R root:nora-ops /srv/nora
sudo chmod 2775 /srv/nora /srv/nora/builds /srv/nora/builds/* /srv/nora/serve   # setgid: group inherits
sudo chmod 2750 /srv/nora/env                                                   # readable by operators, not others
```

- Per operator: `sudo usermod -aG nora-ops <user>`; their login shell
  profile sets `umask 002` and exports `JOB_UID=$(id -u)` /
  `JOB_GID=$(id -g)` (identity lives in the profile, never in shared
  env files).
- Probe (as an operator, fresh login): `id -nG | grep -w nora-ops`,
  `umask` prints `0002`, `touch /srv/nora/builds/nora/.probe && stat -c
  '%U:%G %a' /srv/nora/builds/nora/.probe` shows `<user>:nora-ops 664`;
  remove the probe file.
- Probe (container writes land group-owned): with a wiring env whose
  `JOB_UID`/`JOB_GID` are the operator's, `docker compose --env-file
  <wiring> --profile ingest run --rm -T nora-pipeline sh -c 'touch
  /data/env/.probe'` → the file on the host is `<user>:nora-ops`.

### A4. NAS mounts (requirements/, archive/)

Storage-side prerequisites (ask the NAS admin, in these words): NFS
export of the team volume with **UNIX or mixed security style**;
export restricted to the two machines; on the Windows side the same
volume shared to the team group only, not org-wide (the corpus is
proprietary); consistent user/group id mapping so files written from
Windows are group-readable on Linux (pin the `nora-ops` id in the
mapping if local accounts are used).

```bash
sudo apt install -y nfs-common
sudo mkdir -p /srv/nora/requirements /srv/nora/archive
# /etc/fstab — placeholders:
# nas.example.test:/vol/nora/requirements  /srv/nora/requirements  nfs  ro,vers=3,_netdev,nofail  0 0
# nas.example.test:/vol/nora/archive       /srv/nora/archive       nfs  rw,vers=3,_netdev,nofail  0 0
sudo mount -a
```

`requirements/` is mounted read-only on the build machine on purpose —
documents arrive from the Windows side; jobs only read. Use `vers=4`
if the export offers it; either way the mount options are the NAS
admin's call, the paths are not.

- Probe: `ls /srv/nora/requirements/` shows `<MNO>/<MMMYYYY>/`
  directories and a file dropped from a Windows client within the last
  minute is visible with mode `-r--r-----` or wider for group.
- Probe (docker sees the mount): `docker run --rm -v
  /srv/nora/requirements:/r:ro alpine ls /r`.
- Probe (a job can read it): the first `cycle.sh parse` on a small
  cell.

### A5. Checkout + images

- `git clone <internal-remote> ~/work/nora` per operator (a checkout
  is per-user and stateless — nothing under it is shared state).
- Images: either `./pull.sh images-<sha>` from a published release
  (needs `GHHOST/GHORG/GHREPO/GHTOKEN` in `docker/.env` — the token
  is per-user, keep it out of `/srv/nora/env`), or build from source
  on this host following `docker/README.md` §Build + distribute
  (corporate DPI note applies; `sudo ./debug-egress.sh` if container
  egress misbehaves).
- Set `IMAGE_PREFIX`/`IMAGE_TAG` in the wiring env to the loaded
  images (defaults `local`/`dev` mean "built here").
- Probe: `docker image ls | grep -E 'nora-pipeline|sira-batch'` shows
  the expected tag; `docker compose --env-file /srv/nora/env/.env.builds
  --profile ingest run --rm -T nora-pipeline python -m
  core.src.pipeline.run_cli --help` prints usage.

### A6. Env files under /srv/nora/env

Author from the `docker/*.example` templates; **every path is
absolute** (compose resolves relative `env_file` paths against
`docker/`, which is now per-user):

```
/srv/nora/env/.env.builds            # wiring: all SEVEN path vars; NORA_ENV_DIR/SIRA_DB_ROOT are
                                     #   rewritten by cycle.sh start; REQUIREMENTS_DIR=/srv/nora/requirements
                                     #   PIPELINE_ENV_FILE=/srv/nora/env/.env.nora-pipeline
                                     #   SIRA_BATCH_ENV_FILE=/srv/nora/env/.env.sira-batch
                                     #   SECRETS_ENV_FILE=/srv/nora/env/.env.secrets
/srv/nora/env/.env.nora-pipeline     # NORA_TAXONOMY_OVERVIEW_DIR=/data/env/prompts + LLM routing
/srv/nora/env/.env.sira-batch        # NORA_SIRA_DOC_PROMPT_DIR=/data/env/prompts, NORA_SIRA_TAXONOMY_DIR=/data/env/out/taxonomy,
                                     #   ENRICH LLM URL (WITH /v1) + model, refusal markers, fallback LLM
/srv/nora/env/.env.secrets           # API keys only; 0640 root:nora-ops
```

`JOB_UID`/`JOB_GID` are NOT set in the shared wiring env — compose
reads them from the operator's shell, which is why the profile
exports them (A3).

- Ownership: `sudo chown root:nora-ops /srv/nora/env/.env.* && sudo chmod 640 /srv/nora/env/.env.*`
- Probe: `grep -c '^[A-Z_]*=/' /srv/nora/env/.env.builds` ≥ 7 and
  `grep -E '=\.|=\.\./' /srv/nora/env/.env.*` prints nothing (no
  relative paths). `grep -l JOB_UID /srv/nora/env/.env.*` prints nothing.
- Probe: `docker compose --env-file /srv/nora/env/.env.builds config
  --services` lists the four services without an interpolation error.

### A7. ssh to the serving host

Each operator gets key-based ssh to the serving host **as themselves**
(the push lands owned by the pusher; `serve-push.sh` uses
`BatchMode=yes`, so passphrase-less keys or an agent).

- Probe (as each operator): `ssh -o BatchMode=yes <serving-host> 'id -nG
  | grep -w nora-ops && test -w /srv/nora/serve && echo ok'` prints
  `ok`.
- Probe (end to end, once): `docker/serve-push.sh --dry-run <any local
  label> <serving-host>` prints the remote command plan and a
  transfer summary (not "remote unreachable").

### A8. Hand-over probe (the first real cycle)

Run `docs/ingestion-guide.md` on a small cell with an operator at the
keyboard: `cycle.sh start … parse … prompts … taxonomy … enrich
--cell <small> … verify-enrich … promote --label … --host …`. The
machine is provisioned when the CYC blocks arrive and the label
appears complete on the serving host. Keep that build as
`<YYYY-MM>-probe`; abandon it afterwards.

## B. Serving host (migration in place)

Gated on the running golden-eval sweep campaign finishing — the
detached run references pre-migration paths. Announce a window; the
stacks are down for the duration.

Before touching anything, capture the invariants that must survive:

```bash
for p in <query-port-a> <query-port-b>; do curl -s 127.0.0.1:$p/healthz > ~/pre-migration-healthz-$p.json; done
# + the current GEV baseline block(s) — ${GOLDEN_DIR}/baselines/<stack>.txt, or the last accepted run's block
```

1. **Stacks down**: `docker compose --env-file <stack env> --profile
   serve down` for every stack (containers only; volumes are bind
   mounts and untouched).
2. **Images out**: `docker save -o ~/images-pre-migration.tar
   $(docker image ls --format '{{.Repository}}:{{.Tag}}' | grep -E
   'nora-|sira-')` — the images are the rollback if the daemon swap
   goes wrong; the pre-cycle snapshot tags from the runbook are also
   on the internal release.
3. **Snap out, docker-ce in**: `sudo snap remove docker`, then A2 in
   full (same probes, including the `/srv/nora` bind-mount probe
   BEFORE moving data).
4. **Images in**: `docker load -i ~/images-pre-migration.tar`; probe
   `docker image ls` matches the pre-swap list.
5. **Tree move** (same filesystem — `mv` is instant and keeps the
   promote hardlinks): create `/srv/nora` per A3 (group, setgid,
   modes), then move `requirements/ serve/ web-state-*/ feedback/
   corrections/ models/ eval-golden/` from the old data root into it
   (`eval-golden` → `/srv/nora/eval/golden`). Builds that still exist
   on this host go to `/srv/nora/archive/` (NAS) or to
   `/srv/nora/builds/` if the host will keep building for now.
6. **Env files out of the checkout**: copy every `docker/.env*` to
   `/srv/nora/env/`, rewrite the path vars to the new roots, make
   `*_ENV_FILE` absolute, set `GOLDEN_DIR=/srv/nora/eval/golden`,
   strip any `JOB_UID`/`JOB_GID` lines, then `chown root:nora-ops`,
   `chmod 640`. Probe: `grep -n '/home/' /srv/nora/env/.env.*` prints
   nothing.
7. **Operators**: A3 per operator (group, umask, profile exports).
8. **Recreate** every stack from the new env paths:
   `docker compose --env-file /srv/nora/env/.env.<stack> --profile
   serve up -d`.
9. **Verify identity**: for each stack, `curl -s 127.0.0.1:<port>/healthz`
   vs the captured file — `serve_label`, `data_fingerprint`,
   `code_version`, `sira_prompt_scheme`, cell count must be identical
   (`diff <(python3 -m json.tool ~/pre-…json) <(curl -s … | python3
   -m json.tool)` should show only timestamps/uptime, if anything).
10. **Golden smoke**: a Stage-1 golden run against each stack per
    `docs/golden-eval-guide.md`; the `set=` anchor in its GEV block
    must equal the baseline's (same samples, same digest), and recall
    must match the baseline within run noise.
11. **Fail-loud the old root**: `mv <old-data-root>
    <old-data-root>.MIGRATED-DO-NOT-USE` — no compatibility symlink.
    Anything still pointing at the old path (a shell alias, a cron
    entry, a stale `docker/.env`) breaks visibly instead of silently
    splitting state. Remove `docker/.env*` from the checkout after a
    week of quiet.
12. **PROMOTE_LOG + baselines**: `touch /srv/nora/serve/PROMOTE_LOG`
    (group-writable) and place the accepted GEV blocks at
    `/srv/nora/eval/golden/baselines/<stack>.txt` so the first staged
    promote has something to compare against.

Rollback of the migration itself (before step 11): recreate the stacks
from the old `docker/.env*` files — the old tree is still in place
until step 11 renames it, and the loaded images are the same ones.

## C. Dev PC

No urgency. Adopt the `/srv/nora` layout when convenient so the guides
read true locally; until then `NORA_ROOT` / `NORA_ENV_ROOT` /
`NORA_SERVE_ROOT` environment variables point the scripts at the
existing per-user tree, and `serve-flip.sh` / `cycle.sh` fall back to
`docker/.env.*` when no `/srv/nora/env` exists.
