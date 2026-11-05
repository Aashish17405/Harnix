"""Hardened git access to a task workspace.

The workspace gets a *fresh* repository (never the source repo's .git), so no untrusted git config,
hooks or remotes exist at creation. Commands below additionally disable hooks/fsmonitor/external diff
in case a command run inside the workspace tampers with .git.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

EXCLUDES = "__pycache__/\n*.pyc\n.pytest_cache/\n.ruff_cache/\n.mypy_cache/\nnode_modules/\n.venv/\n.forge/\n"


class GitError(Exception):
    pass


def _env() -> dict[str, str]:
    keep = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP")
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    return env


def _run(ws: Path, args: tuple[str, ...], timeout: int) -> subprocess.CompletedProcess[bytes]:
    cmd = [
        "git", "-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}", "-c", "core.autocrlf=false",
        "-c", "core.safecrlf=false", "-c", "core.quotepath=off", "-c", "core.pager=cat",
        "-c", "user.name=forge", "-c", "user.email=forge@localhost", "-c", "commit.gpgsign=false", *args,
    ]  # fmt: skip
    try:
        return subprocess.run(
            cmd, cwd=ws, env=_env(), capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL
        )
    except FileNotFoundError as e:
        raise GitError("git executable not found on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise GitError(f"git {args[0]} timed out") from e


def run_git(ws: Path, *args: str, timeout: int = 60, check: bool = True) -> tuple[int, str]:
    p = _run(ws, args, timeout)
    out = (p.stdout + p.stderr).decode("utf-8", "replace")
    if check and p.returncode != 0:
        raise GitError(f"git {args[0]} failed ({p.returncode}): {out.strip()[:500]}")
    return p.returncode, out


def baseline_bytes(ws: Path, rel: str, ref: str = "HEAD") -> bytes | None:
    """Exact bytes of `rel` at `ref` (default: the baseline commit), or None if it did not exist then."""
    p = _run(ws, ("cat-file", "blob", f"{ref}:{rel}"), 60)
    return p.stdout if p.returncode == 0 else None


blob = baseline_bytes  # same thing under a clearer name when `ref` is a snapshot


def changed_paths_raw(ws: Path, base: str = "HEAD", target: str | None = None) -> list[tuple[str, str]]:
    """(status, path) per changed file between `base` and `target` (default: the working tree): A/M/D only
    (renames reported as D + A), NUL-safe."""
    if target is None:
        stage(ws)
        args = ("diff", "--cached", "--no-ext-diff", "--no-renames", "--name-status", "-z", base)
    else:
        args = ("diff", "--no-ext-diff", "--no-renames", "--name-status", "-z", base, target)
    p = _run(ws, args, 60)
    if p.returncode != 0:
        raise GitError(f"git diff failed: {p.stderr.decode('utf-8', 'replace').strip()[:300]}")
    parts = p.stdout.decode("utf-8", "surrogateescape").split("\0")
    return [(parts[i][0], parts[i + 1]) for i in range(0, len(parts) - 1, 2) if parts[i]]


def special_modes(ws: Path, base: str, target: str) -> dict[str, str]:
    """path -> new file mode for entries that are not plain files (symlinks 120000, submodules 160000)."""
    p = _run(ws, ("diff", "--no-ext-diff", "--no-renames", "--raw", "-z", base, target), 60)
    if p.returncode != 0:
        raise GitError(f"git diff failed: {p.stderr.decode('utf-8', 'replace').strip()[:300]}")
    parts = p.stdout.decode("utf-8", "surrogateescape").split("\0")
    out: dict[str, str] = {}
    for i in range(0, len(parts) - 1, 2):
        meta = parts[i].lstrip(":").split()
        if len(meta) >= 2 and meta[1] in ("120000", "160000"):
            out[parts[i + 1]] = meta[1]
    return out


def head(ws: Path) -> str:
    """The baseline commit (the repository state when the session started)."""
    return run_git(ws, "rev-parse", "HEAD")[1].strip()


def snapshot(ws: Path, label: str, parent: str | None = None) -> str:
    """Record the working tree as a commit WITHOUT moving HEAD (HEAD stays the session baseline).
    The parent defaults to the baseline; chaining snapshots (parent = the previous one) keeps each turn's history."""
    stage(ws)
    tree = run_git(ws, "write-tree")[1].strip()
    return run_git(ws, "commit-tree", tree, "-p", parent or "HEAD", "-m", label[:200])[1].strip()


def restore(ws: Path, ref: str) -> None:
    """Put the working tree back exactly as it was at snapshot `ref` (files added later are removed)."""
    stage(ws)
    run_git(ws, "read-tree", "--reset", "-u", ref, timeout=120)
    run_git(ws, "clean", "-fdq", timeout=120)


def init_baseline(ws: Path) -> None:
    """Snapshot the copied tree (including the user's uncommitted work) as the diff baseline."""
    run_git(ws, "init", "-q")
    (ws / ".git" / "info").mkdir(exist_ok=True)
    (ws / ".git" / "info" / "exclude").write_text(EXCLUDES)
    # highest-precedence attributes: store and diff the exact bytes (no EOL conversion, no filter drivers from the repo)
    (ws / ".git" / "info" / "attributes").write_text("* -text -eol -filter -ident\n")
    run_git(ws, "add", "-A")
    run_git(ws, "commit", "-q", "--allow-empty", "-m", "forge baseline")


def stage(ws: Path) -> None:
    run_git(ws, "add", "-A")


def status(ws: Path) -> str:
    stage(ws)
    return run_git(ws, "status", "--short")[1]


def diff(ws: Path, path: str | None = None, base: str = "HEAD") -> str:
    stage(ws)
    args = ["diff", "--cached", "--no-ext-diff", "--no-textconv", base]
    if path:
        args += ["--", path]
    return run_git(ws, *args)[1]


def changed_files(ws: Path, base: str = "HEAD") -> list[dict]:
    stage(ws)
    _, names = run_git(ws, "diff", "--cached", "--no-ext-diff", "--name-status", base)
    _, nums = run_git(ws, "diff", "--cached", "--no-ext-diff", "--numstat", base)
    stats = {}
    for line in nums.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            a, d, p = parts
            stats[p] = (int(a) if a.isdigit() else 0, int(d) if d.isdigit() else 0)
    files = []
    for line in names.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            path = parts[-1]
            add, dele = stats.get(path, (0, 0))
            files.append({"path": path, "status": parts[0][0], "additions": add, "deletions": dele})
    return files


def grep(ws: Path, pattern: str, *, path: str, regex: bool, case_sensitive: bool) -> tuple[int, str]:
    stage(ws)
    args = ["grep", "-n", "-I", "--no-color", "--untracked"]
    args.append("-E" if regex else "-F")
    if not case_sensitive:
        args.append("-i")
    args += ["-e", pattern, "--", path]
    return run_git(ws, *args, check=False)
