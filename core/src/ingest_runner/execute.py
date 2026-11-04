"""Running host commands with their output streamed line by line."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Result:
    rc: int
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class CommandError(RuntimeError):
    def __init__(self, argv: list[str], result: Result, what: str = "") -> None:
        self.argv = argv
        self.result = result
        tail = " | ".join(result.lines[-3:])
        super().__init__(f"{what or argv[0]} failed rc={result.rc}: {tail}")


class Exec:
    """Run a command; every output line goes to `on_line` as it arrives."""

    def run(self, argv: list[str], *, env: dict[str, str] | None = None,
            cwd: str | None = None, on_line: Callable[[str], None] | None = None,
            stdin: str | None = None) -> Result:
        proc = subprocess.Popen(
            argv, cwd=cwd, env=env, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        lines: list[str] = []
        if stdin is not None and proc.stdin is not None:
            proc.stdin.write(stdin)
            proc.stdin.close()
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            lines.append(line)
            if on_line:
                on_line(line)
        rc = proc.wait()
        return Result(rc=rc, lines=lines)
