"""Command execution backends. A Sandbox runs one argv (never a shell) with timeout/output limits.

LocalSandbox runs on the host: it scrubs the environment and kills the whole process tree on
timeout/cancel, but it is NOT a security boundary. DockerSandbox adds container isolation
(no network by default, dropped capabilities, resource limits, non-root user, only the workspace mounted).
"""

from __future__ import annotations

import collections
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class CommandResult:
    argv: list[str]
    exit_code: int | None
    output: str
    duration: float
    timed_out: bool = False
    cancelled: bool = False
    truncated: bool = False
    not_found: bool = False
    error: str | None = None


class Sandbox(Protocol):
    name: str

    def available(self) -> tuple[bool, str]: ...

    def run(
        self, argv: list[str], *, workspace: Path, workdir: Path, timeout: float,
        cancel: threading.Event | None, max_output: int,
    ) -> CommandResult: ...  # fmt: skip


class _Collector:
    """Keeps the head and the tail of the stream (test failures are usually at the end)."""

    def __init__(self, limit: int):
        self.half = max(limit // 2, 1)
        self.head = bytearray()
        self.tail: collections.deque[bytes] = collections.deque()
        self.tail_size = 0
        self.truncated = False

    def feed(self, chunk: bytes) -> None:
        room = self.half - len(self.head)
        if room > 0:
            self.head += chunk[:room]
            chunk = chunk[room:]
        if chunk:
            self.tail.append(chunk)
            self.tail_size += len(chunk)
            while self.tail_size - len(self.tail[0]) >= self.half:
                self.tail_size -= len(self.tail.popleft())
                self.truncated = True
            if self.tail_size > self.half:  # trim front of oldest chunk
                over = self.tail_size - self.half
                self.tail[0] = self.tail[0][over:]
                self.tail_size -= over
                self.truncated = True

    def text(self) -> str:
        body = bytes(self.head)
        if self.truncated:
            body += b"\n...[output truncated]...\n"
        body += b"".join(self.tail)
        return body.decode("utf-8", "replace")


def kill_tree(proc: subprocess.Popen) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=15, check=False
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def supervise(
    cmd: list[str], *, cwd: Path | None, env: dict[str, str] | None, timeout: float,
    cancel: threading.Event | None, max_output: int, on_kill=None, display_argv: list[str] | None = None,
) -> CommandResult:  # fmt: skip
    argv = display_argv or cmd
    start = time.monotonic()
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(
            cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, **kwargs,
        )  # fmt: skip
    except (FileNotFoundError, NotADirectoryError) as e:
        return CommandResult(argv, 127, "", time.monotonic() - start, not_found=True, error=str(e))
    except OSError as e:
        return CommandResult(argv, None, "", time.monotonic() - start, error=f"Could not start process: {e}")

    col = _Collector(max_output)

    def pump() -> None:
        assert proc.stdout
        for chunk in iter(lambda: proc.stdout.read1(8192), b""):  # type: ignore[union-attr]
            col.feed(chunk)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    timed_out = cancelled = False
    while True:
        try:
            proc.wait(timeout=0.1)
            break
        except subprocess.TimeoutExpired:
            pass
        if cancel is not None and cancel.is_set():
            cancelled = True
        elif time.monotonic() - start > timeout:
            timed_out = True
        if cancelled or timed_out:
            if on_kill:
                on_kill()
            kill_tree(proc)
            proc.wait()
            break
    reader.join(timeout=5)  # a leaked grandchild holding the pipe must not hang us
    return CommandResult(
        argv, None if (timed_out or cancelled) else proc.returncode, col.text(),
        time.monotonic() - start, timed_out=timed_out, cancelled=cancelled, truncated=col.truncated,
    )  # fmt: skip


class LocalSandbox:
    name = "local"

    def available(self) -> tuple[bool, str]:
        return True, "local execution on the host (not a security boundary)"

    def _env(self, workspace: Path) -> dict[str, str]:
        home = workspace.parent / (workspace.name + ".home")
        for sub in ("tmp", "appdata", "localappdata"):
            (home / sub).mkdir(parents=True, exist_ok=True)
        keep = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "SYSTEMDRIVE", "LANG", "LC_ALL")
        env = {k: v for k, v in os.environ.items() if k.upper() in keep}
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
        env.update(
            HOME=str(home), USERPROFILE=str(home), APPDATA=str(home / "appdata"),
            LOCALAPPDATA=str(home / "localappdata"), TEMP=str(home / "tmp"), TMP=str(home / "tmp"),
            TMPDIR=str(home / "tmp"), PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", CI="1", NO_COLOR="1",
        )  # fmt: skip
        return env

    def run(self, argv, *, workspace, workdir, timeout, cancel, max_output) -> CommandResult:
        env = self._env(workspace)
        exe = shutil.which(argv[0], path=env["PATH"])
        if exe is None:
            return CommandResult(argv, 127, "", 0.0, not_found=True, error=f"Executable not found: {argv[0]}")
        return supervise(
            [exe, *argv[1:]], cwd=workdir, env=env, timeout=timeout, cancel=cancel,
            max_output=max_output, display_argv=argv,
        )  # fmt: skip


class DockerSandbox:
    name = "docker"

    def __init__(self, image: str, network: str = "none", memory: str = "1g", cpus: str = "2"):
        self.image, self.network, self.memory, self.cpus = image, network, memory, cpus

    def available(self) -> tuple[bool, str]:
        try:
            info = subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, timeout=20
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return (
                False,
                "Docker CLI not found or not responding. Install Docker or set FORGE_EXECUTION_MODE=local.",
            )
        if info.returncode != 0:
            return (
                False,
                "Docker daemon is not running. Start Docker or set FORGE_EXECUTION_MODE=local (host execution).",
            )
        img = subprocess.run(["docker", "image", "inspect", self.image], capture_output=True, timeout=20)
        if img.returncode != 0:
            return False, (
                f"Docker image '{self.image}' not found. Build it: "
                "docker build -f docker/Dockerfile.sandbox -t forge-sandbox:latest docker/"
            )
        return True, f"docker image {self.image}, network={self.network}"

    def build_command(self, argv: list[str], workspace: Path, workdir: Path, name: str) -> list[str]:
        rel = workdir.relative_to(workspace).as_posix()
        getuid, getgid = getattr(os, "getuid", None), getattr(os, "getgid", None)
        user = f"{getuid()}:{getgid()}" if getuid and getgid else "1000:1000"  # never root
        return [
            "docker", "run", "--rm", "--name", name, "--network", self.network, "--memory", self.memory,
            "--cpus", self.cpus, "--pids-limit", "256", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--read-only", "--tmpfs", "/tmp:rw,size=256m",
            "--user", user, "--mount", f"type=bind,src={workspace},dst=/workspace",
            "-w", "/workspace" if rel == "." else f"/workspace/{rel}",
            "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "CI=1", "-e", "NO_COLOR=1",
            self.image, *argv,
        ]  # fmt: skip

    def run(self, argv, *, workspace, workdir, timeout, cancel, max_output) -> CommandResult:
        name = f"forge-{uuid.uuid4().hex[:12]}"
        cmd = self.build_command(argv, workspace, workdir, name)

        def kill_container() -> None:
            subprocess.run(["docker", "kill", name], capture_output=True, timeout=20, check=False)

        res = supervise(
            cmd, cwd=None, env=None, timeout=timeout, cancel=cancel, max_output=max_output,
            on_kill=kill_container, display_argv=argv,
        )  # fmt: skip
        if res.exit_code in (126, 127) and (
            "executable file not found" in res.output or "not found" in res.output
        ):
            res.not_found = True
        return res


def make_sandbox(settings) -> Sandbox:
    if settings.execution_mode == "docker":
        return DockerSandbox(
            settings.docker_image, settings.docker_network, settings.docker_memory, settings.docker_cpus
        )
    return LocalSandbox()
