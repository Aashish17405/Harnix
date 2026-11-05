"""The host terminal (opt-in, FORGE_TERMINAL=host): real processes on this machine, outside the Docker sandbox.

Foreground commands reuse the supervised runner; background processes (a dev server you want to open in a browser)
outlive the task that started them, are listed and stopped through the API/CLI, and die with the backend.
Never a shell: argv only. The working directory is always inside the task's workspace copy (not your repository).
"""

from __future__ import annotations

import itertools
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx

from harnix.execution.sandbox import CommandResult, kill_tree, supervise

_SECRETISH = re.compile(r"(?i)(key|token|secret|passw|credential|auth)")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"}
RING_BYTES = 200_000


def host_env(secrets: list[str] | None = None) -> dict[str, str]:
    """The operator's environment minus anything secret-looking and Forge's own settings (API keys, tokens)."""
    env = {}
    for k, v in os.environ.items():
        if k.startswith("FORGE_") or _SECRETISH.search(k) or (secrets and v in secrets):
            continue
        env[k] = v
    env.update(PYTHONUNBUFFERED="1", PYTHONUTF8="1", NO_COLOR="1", PYTHONDONTWRITEBYTECODE="1")
    return env


def run_foreground(argv: list[str], *, workdir: Path, timeout: float, cancel, max_output: int, secrets: list[str] | None = None) -> CommandResult:  # fmt: skip
    env = host_env(secrets)
    exe = shutil.which(argv[0], path=env.get("PATH"))
    if exe is None:
        return CommandResult(argv, 127, "", 0.0, not_found=True, error=f"Executable not found: {argv[0]}")
    return supervise([exe, *argv[1:]], cwd=workdir, env=env, timeout=timeout, cancel=cancel, max_output=max_output, display_argv=argv)  # fmt: skip


def probe(url: str, timeout: float = 2.0) -> tuple[bool, int | None, str]:
    """Is something answering at this local URL? Only this machine is ever contacted (not an SSRF primitive)."""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or (p.hostname or "") not in {h.strip("[]") for h in LOCAL_HOSTS}:
        return False, None, "only http(s) URLs on localhost / 127.0.0.1 can be checked"
    target = url.replace("0.0.0.0", "127.0.0.1")
    try:
        r = httpx.get(target, timeout=timeout, follow_redirects=False)
        return True, r.status_code, ""
    except httpx.HTTPError as e:
        return False, None, type(e).__name__


@dataclass
class ManagedProcess:
    id: str
    argv: list[str]
    cwd: str
    owner: str  # the session (or task) that started it
    task_id: str
    proc: subprocess.Popen
    started_at: float = field(default_factory=time.time)
    _ring: deque[bytes] = field(default_factory=deque)
    _size: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def feed(self, chunk: bytes) -> None:
        with self._lock:
            self._ring.append(chunk)
            self._size += len(chunk)
            while self._size > RING_BYTES and len(self._ring) > 1:
                self._size -= len(self._ring.popleft())

    def output(self, tail_chars: int) -> str:
        with self._lock:
            text = b"".join(self._ring).decode("utf-8", "replace")
        return text[-tail_chars:]

    @property
    def running(self) -> bool:
        return self.proc.poll() is None

    def info(self) -> dict:
        return {
            "id": self.id, "command": " ".join(self.argv), "cwd": self.cwd, "pid": self.proc.pid,
            "running": self.running, "exit_code": self.proc.poll(), "owner": self.owner, "task_id": self.task_id,
            "started_at": self.started_at,
        }  # fmt: skip


class ProcessManager:
    def __init__(self, max_running: int = 5):
        self.max_running = max_running
        self._procs: dict[str, ManagedProcess] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def start(
        self, argv: list[str], *, cwd: Path, owner: str, task_id: str, secrets: list[str] | None = None
    ) -> ManagedProcess:
        env = host_env(secrets)
        exe = shutil.which(argv[0], path=env.get("PATH"))
        if exe is None:
            raise FileNotFoundError(f"Executable not found: {argv[0]}")
        with self._lock:
            if sum(1 for p in self._procs.values() if p.running) >= self.max_running:
                raise RuntimeError(
                    f"{self.max_running} background processes are already running; stop one first"
                )
            kwargs: dict = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            else:
                kwargs["start_new_session"] = True
            proc = subprocess.Popen(  # noqa: S603 - argv list, no shell; classified and approved before this runs
                [exe, *argv[1:]], cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, **kwargs,
            )  # fmt: skip
            mp = ManagedProcess(f"p{next(self._ids)}", argv, str(cwd), owner, task_id, proc)
            self._procs[mp.id] = mp
        threading.Thread(target=self._pump, args=(mp,), daemon=True, name=f"proc-{mp.id}").start()
        return mp

    @staticmethod
    def _pump(mp: ManagedProcess) -> None:
        assert mp.proc.stdout
        for chunk in iter(lambda: mp.proc.stdout.read1(8192), b""):  # type: ignore[union-attr]
            mp.feed(chunk)

    def get(self, pid: str, owner: str | None = None) -> ManagedProcess | None:
        p = self._procs.get(pid)
        return p if p is not None and (owner is None or p.owner == owner) else None

    def list(self) -> list[dict]:
        return [p.info() for p in self._procs.values()]

    def stop(self, pid: str, owner: str | None = None) -> bool:
        p = self.get(pid, owner)
        if p is None:
            return False
        if p.running:
            kill_tree(p.proc)
            try:
                p.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        return True

    def stop_all(self) -> int:
        n = 0
        for p in list(self._procs.values()):
            if p.running:
                n += 1
                self.stop(p.id)
        return n

    def wait_ready(
        self, mp: ManagedProcess, url: str | None, seconds: float
    ) -> tuple[bool | None, int | None, str]:
        """Wait until the process answers at `url` (or exits, or time is up). (reachable, status, note)."""
        if not url:
            time.sleep(min(seconds, 2.0))
            return None, None, ""
        end = time.monotonic() + seconds
        note = ""
        while time.monotonic() < end:
            if not mp.running:
                return False, None, f"the process exited with code {mp.proc.poll()}"
            ok, status, note = probe(url)
            if ok:
                return True, status, ""
            time.sleep(0.5)
        return False, None, note or "no answer in time"
