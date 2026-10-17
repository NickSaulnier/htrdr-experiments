"""Run htrdr (or anything with the same calling convention) as a subprocess.

Renders can take minutes, so a run has a timeout, can be cancelled, and
keeps only the tail of the process's output. On timeout or cancellation
the whole process group is stopped (SIGTERM, then SIGKILL), which matters
once htrdr runs under mpirun.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import signal
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .argv import command_line

TAIL_LINES = 40
KILL_GRACE_S = 5.0
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_PROGRESS = re.compile(r"Rendering:\s*(\d+(?:\.\d+)?)\s*%")


def progress_percent(line: str) -> float | None:
    """The percentage in one of htrdr's verbose "Rendering:  42%" lines, or None."""
    m = _PROGRESS.search(line)
    return float(m.group(1)) if m else None

Status = Literal["ok", "failed", "timeout", "cancelled"]


@dataclass(frozen=True)
class HtrdrInstall:
    """Where htrdr lives. Defaults to the build script's prefix, ~/.local."""

    prefix: Path = field(default_factory=lambda: Path(os.environ.get("HTRDR_PREFIX", Path.home() / ".local")))

    @property
    def binary(self) -> Path:
        return self.prefix / "bin" / "htrdr-atmosphere"

    @property
    def htpp(self) -> Path:
        return self.prefix / "bin" / "htpp"

    def env(self) -> dict[str, str]:
        """The process environment, with htrdr's libraries on the loader path."""
        env = dict(os.environ)
        lib = str(self.prefix / "lib")
        env["LD_LIBRARY_PATH"] = lib + (os.pathsep + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
        return env

    def available(self) -> bool:
        return self.binary.is_file() and os.access(self.binary, os.X_OK)

    def info(self) -> dict:
        """What's installed, for run logs. htrdr has no version option, so this reads the
        manifest scripts/build-htrdr.sh writes, and hashes the binary."""
        import json
        import subprocess

        out: dict = {"prefix": str(self.prefix), "htrdr": str(self.binary)}
        if self.binary.is_file():
            out["htrdr_sha256"] = file_sha256(self.binary)
        manifest = self.prefix / "share" / "htrdr-build" / "manifest.json"
        if manifest.is_file():
            data = json.loads(manifest.read_text())
            out["built_at"] = data.get("built_at")
            out["components"] = data.get("components", {})
            out["htrdr_version"] = out["components"].get("htrdr", {}).get("tag")
        if self.htpp.is_file():
            try:
                r = subprocess.run([str(self.htpp), "-V"], capture_output=True, text=True, timeout=10, env=self.env())
                out["htpp_version"] = (r.stdout or r.stderr).strip()
            except (OSError, subprocess.TimeoutExpired):
                pass
        return out


_HASHES: dict[tuple[str, int, int], str] = {}


def file_sha256(path: Path | str) -> str:
    """SHA-256 of a file, cached by path, size and modification time (input files are large)."""
    import hashlib

    p = Path(path)
    st = p.stat()
    key = (str(p.resolve()), st.st_size, st.st_mtime_ns)
    if key not in _HASHES:
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _HASHES[key] = h.hexdigest()
    return _HASHES[key]


@dataclass(frozen=True)
class RunResult:
    argv: list[str]
    status: Status
    returncode: int | None
    wall_s: float
    stdout_tail: list[str]
    stderr_tail: list[str]

    @property
    def command_line(self) -> str:
        return command_line(self.argv)

    @property
    def ok(self) -> bool:
        return self.status == "ok"


async def _pump(stream: asyncio.StreamReader, tail: deque[str], on_line: Callable[[str], None] | None) -> None:
    # htrdr redraws progress with carriage returns and colours its output
    # with ANSI escapes; keep plain lines, minus its bare ">" prompts.
    def emit(raw: bytes) -> None:
        line = _ANSI.sub("", raw.decode(errors="replace")).strip()
        if line in ("", ">"):
            return
        tail.append(line)
        if on_line:
            on_line(line)

    pending = b""
    while chunk := await stream.read(4096):
        pending += chunk.replace(b"\r", b"\n")
        *lines, pending = pending.split(b"\n")
        for raw in lines:
            emit(raw)
    emit(pending)


def _signal_group(proc: asyncio.subprocess.Process, sig: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, sig)


async def _stop(proc: asyncio.subprocess.Process) -> None:
    """SIGTERM the process group, then SIGKILL it if it hasn't exited after a grace period."""
    _signal_group(proc, signal.SIGTERM)
    try:
        await asyncio.wait_for(asyncio.shield(proc.wait()), KILL_GRACE_S)
    except TimeoutError:
        _signal_group(proc, signal.SIGKILL)
        await proc.wait()


async def run(
    argv: list[str],
    *,
    timeout_s: float | None = None,
    env: dict[str, str] | None = None,
    cwd: Path | str | None = None,
    cancel: asyncio.Event | None = None,
    on_line: Callable[[str], None] | None = None,
) -> RunResult:
    """Run `argv` to completion, timeout or cancellation.

    `on_line` sees every output line (stdout and stderr) as it arrives,
    for progress reporting.
    """
    started = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=cwd,
        start_new_session=True,  # its own process group, so it can be stopped as a whole
    )
    assert proc.stdout is not None and proc.stderr is not None
    out: deque[str] = deque(maxlen=TAIL_LINES)
    err: deque[str] = deque(maxlen=TAIL_LINES)
    pumps = asyncio.gather(_pump(proc.stdout, out, on_line), _pump(proc.stderr, err, on_line))

    waiters: set[asyncio.Future[object]] = {asyncio.ensure_future(proc.wait())}
    cancel_task = asyncio.ensure_future(cancel.wait()) if cancel else None
    if cancel_task:
        waiters.add(cancel_task)
    try:
        done, _ = await asyncio.wait(waiters, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        # The task running us was cancelled (say, an MCP client aborted the
        # call): never leave htrdr running on every core with nobody waiting.
        await _stop(proc)
        pumps.cancel()
        if cancel_task:
            cancel_task.cancel()
        raise

    status: Status
    if proc.returncode is not None:
        status = "ok" if proc.returncode == 0 else "failed"
    else:
        status = "cancelled" if cancel_task in done else "timeout"
        await _stop(proc)
    if cancel_task and not cancel_task.done():
        cancel_task.cancel()
    await pumps
    return RunResult(
        argv=list(argv),
        status=status,
        returncode=proc.returncode,
        wall_s=time.monotonic() - started,
        stdout_tail=list(out),
        stderr_tail=list(err),
    )


def run_sync(argv: list[str], **kwargs) -> RunResult:
    """`run` for synchronous callers."""
    return asyncio.run(run(argv, **kwargs))
