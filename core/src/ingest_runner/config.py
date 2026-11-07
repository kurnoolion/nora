"""Runner configuration (`config/ingest_runner.json`).

Paths are host paths; the runner mounts them into the one-shot
containers at fixed in-container paths (`CTR_*`). No secrets: the
containers read their own env files.
"""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass, field
from pathlib import Path

# In-container mount points the runner adds to its one-shot containers.
CTR_SERVE = "/data/serve"          # publish registry root (read-write)
CTR_LABELS = "/data/labels"        # flat promoted labels (converter input)
CTR_WORK = "/data/work"            # converter output
CTR_CORRECTIONS = "/data/corrections"
CTR_ENV = "/data/env"              # the build dir of the batch services

DEFAULT_STORE_URL = "http://nora-store:8040"

# Share mapping in the ingestion wiring env (the `.env.builds` the runner
# already reads): `NORA_SHARE_MAP=\\SERVER\Share=/host/mount;\\SERVER\Other=/other`
# — entries separated by `;`, each `<UNC prefix>=<host path>`. Merged
# with `share_mappings` from the JSON config; the env file wins on a
# duplicate prefix. Keeps host mounts out of any committed file.
ENV_SHARE_MAP = "NORA_SHARE_MAP"


class ConfigError(ValueError):
    pass


@dataclass
class ShareMapping:
    windows: str   # UNC prefix as the web shows it, e.g. \\SERVER\Share
    host: str      # where the host mounts that share


@dataclass
class RunnerConfig:
    jobs_db: str
    docker_dir: str
    builds_wiring: str
    requirements_dir: str
    labels_root: str
    registry_root: str
    work_dir: str
    golden_dir: str
    stack_project: str
    stack_env: str
    staging_project: str
    staging_env: str
    share_mappings: list[ShareMapping] = field(default_factory=list)
    cycle_env: dict[str, str] = field(default_factory=dict)
    runner_name: str = ""
    poll_seconds: float = 5.0
    heartbeat_seconds: float = 15.0
    store_url: str = DEFAULT_STORE_URL
    staging_store_url: str = DEFAULT_STORE_URL
    staging_healthz_url: str = ""
    prompt_scheme: str = ""
    seed_build: str = ""
    eval_tolerance: float = 0.03
    baseline_recall: float | None = None
    verify_n: int = 25
    overlay_db: str = ""
    overlay_root: str = ""
    document_extensions: list[str] = field(default_factory=lambda: [".pdf", ".docx", ".xlsx"])
    abandon_removes_documents: bool = True
    detached_poll_seconds: float = 5.0
    detached_timeout_seconds: float = 6 * 3600
    # Webhooks: a submit may name a callback_url; the runner posts
    # `ingest.<state>` events to it, signed with HMAC-SHA256 over the body
    # (`X-NORA-Signature: sha256=<hex>`) when webhook_secret is set. Only
    # hosts listed here (exact host, or `.suffix`) are posted to; others
    # are logged and skipped.
    webhook_secret: str = ""
    webhook_hosts: list[str] = field(default_factory=list)
    webhook_timeout_seconds: float = 5.0

    REQUIRED = (
        "jobs_db", "docker_dir", "builds_wiring", "requirements_dir",
        "labels_root", "registry_root", "work_dir", "golden_dir",
        "stack_project", "stack_env", "staging_project", "staging_env",
    )

    @classmethod
    def from_dict(cls, data: dict) -> "RunnerConfig":
        missing = [k for k in cls.REQUIRED if not str(data.get(k, "") or "").strip()]
        if missing:
            raise ConfigError(f"ingest_runner config lacks: {', '.join(missing)}")
        mappings = [ShareMapping(windows=m["windows"], host=m["host"])
                    for m in data.get("share_mappings", [])]
        kwargs = {k: data[k] for k in data if k in cls.__dataclass_fields__ and k != "share_mappings"}
        cfg = cls(**kwargs, share_mappings=mappings)
        if not cfg.runner_name:
            cfg.runner_name = socket.gethostname()
        # cycle.sh reads the wiring env from NORA_BUILDS_WIRING; the runner
        # already knows the file, so the layout env need not repeat it.
        cfg.cycle_env = dict(cfg.cycle_env)
        cfg.cycle_env.setdefault("NORA_BUILDS_WIRING", cfg.builds_wiring)
        return cfg

    def template_values(self) -> list[str]:
        """Keys whose value still looks like the example config's
        placeholder (`/path/to/…`, `<…>`): `--check` refuses them."""
        bad: list[str] = []
        for k, v in self.__dict__.items():
            vals = list(v.values()) if isinstance(v, dict) else (v if isinstance(v, list) else [v])
            for x in vals:
                if isinstance(x, str) and ("/path/to" in x or ("<" in x and ">" in x)):
                    bad.append(k)
                    break
        return bad

    @classmethod
    def load(cls, path: str | Path) -> "RunnerConfig":
        """The JSON config, plus the share mapping from the ingestion
        wiring env's `NORA_SHARE_MAP` line when the file has one."""
        p = Path(path)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ConfigError(f"no runner config at {p}")
        except json.JSONDecodeError as exc:
            raise ConfigError(f"runner config {p} is not JSON: {exc}")
        cfg = cls.from_dict(data)
        cfg.merge_env_share_map(read_env_value(cfg.builds_wiring, ENV_SHARE_MAP))
        return cfg

    def merge_env_share_map(self, value: str) -> None:
        """Add the mappings of a `NORA_SHARE_MAP` value; an env entry
        replaces a JSON entry with the same UNC prefix."""
        env = parse_share_map(value)
        if not env:
            return
        keys = {m.windows.replace("/", "\\").rstrip("\\").lower() for m in env}
        kept = [m for m in self.share_mappings
                if m.windows.replace("/", "\\").rstrip("\\").lower() not in keys]
        self.share_mappings = env + kept

    # -- derived paths (mirror cycle.sh's layout rules) ----------------------

    @property
    def nora_builds_root(self) -> Path:
        env = self.cycle_env
        if env.get("NORA_BUILDS_NORA_ROOT"):
            return Path(env["NORA_BUILDS_NORA_ROOT"])
        root = Path(env.get("NORA_BUILDS_ROOT") or (Path(env.get("NORA_ROOT", "/srv/nora")) / "builds"))
        return root / "nora"

    @property
    def active_file(self) -> Path:
        env = self.cycle_env
        if env.get("NORA_BUILDS_NORA_ROOT"):
            return self.nora_builds_root / "ACTIVE"
        root = Path(env.get("NORA_BUILDS_ROOT") or (Path(env.get("NORA_ROOT", "/srv/nora")) / "builds"))
        return root / "ACTIVE"

    def build_dir(self, build: str) -> Path:
        return self.nora_builds_root / build

    def sira_build_dir(self, build: str) -> Path:
        """The build's SIRA cells: `<build>/out/sira/` (the build's last
        stage dir), or `<NORA_BUILDS_SIRA_ROOT>/<build>` when that older
        layout is kept — the same rule as `cycle.sh`."""
        env = self.cycle_env
        if env.get("NORA_BUILDS_SIRA_ROOT"):
            return Path(env["NORA_BUILDS_SIRA_ROOT"]) / build
        return self.build_dir(build) / "out" / "sira"

    def resolve_source(self, source: str) -> Path:
        """Map a submitted share path (UNC, or a host path under a mapped
        share) to the host path. Raises ConfigError when unmapped."""
        s = source.strip()
        if s.startswith("/") and not s.startswith("//"):
            for m in self.share_mappings:
                host = m.host.rstrip("/")
                if s == host or s.startswith(host + "/"):
                    return Path(s)
            raise ConfigError(f"source is not under a mapped share: {s}")
        norm = s.replace("/", "\\")
        best: ShareMapping | None = None
        for m in self.share_mappings:
            prefix = m.windows.replace("/", "\\").rstrip("\\")
            if norm.lower() == prefix.lower() or norm.lower().startswith(prefix.lower() + "\\"):
                if best is None or len(prefix) > len(best.windows):
                    best = m
        if best is None:
            raise ConfigError(f"source is not under a mapped share: {s}")
        rest = norm[len(best.windows.replace("/", "\\").rstrip("\\")):].lstrip("\\")
        return Path(best.host) / rest.replace("\\", "/") if rest else Path(best.host)


def parse_share_map(value: str) -> list[ShareMapping]:
    """`\\\\SERVER\\Share=/host/path;...` → mappings. Raises ConfigError on a
    malformed entry; an empty value is no mappings."""
    out: list[ShareMapping] = []
    for raw in (value or "").split(";"):
        entry = raw.strip()
        if not entry:
            continue
        if "=" not in entry:
            raise ConfigError(f"{ENV_SHARE_MAP}: entry {entry!r} is not <UNC>=<host path>")
        unc, host = entry.split("=", 1)
        unc, host = unc.strip(), host.strip()
        if not unc.startswith(("\\\\", "//")) or not host.startswith("/"):
            raise ConfigError(f"{ENV_SHARE_MAP}: entry {entry!r} is not <UNC>=<host path>")
        out.append(ShareMapping(windows=unc, host=host))
    return out


def read_env_value(env_file: str | Path, key: str) -> str:
    """The value of `KEY=value` in a compose-style env file ('' when the
    file or the key is missing); surrounding quotes are stripped, nothing
    else is interpreted, so backslashes survive."""
    try:
        lines = Path(env_file).read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    value = ""
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == key:
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\'\"":
                v = v[1:-1]
            value = v
    return value


def webhook_host_allowed(url: str, allowed: list[str]) -> bool:
    """`allowed` entries: an exact hostname, or `.example.test` for that
    domain and its subdomains. http and https only."""
    from urllib.parse import urlparse
    try:
        u = urlparse(url)
    except ValueError:
        return False
    if u.scheme not in ("http", "https") or not u.hostname:
        return False
    host = u.hostname.lower()
    for a in allowed:
        a = a.strip().lower()
        if not a:
            continue
        if a.startswith("."):
            if host == a[1:] or host.endswith(a):
                return True
        elif host == a:
            return True
    return False

