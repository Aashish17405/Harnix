"""Apply a finished task's changes from its isolated workspace to the user's real repository.

This is the only place Forge writes to the source repository, so it is deliberately strict:

* it works from the *raw* workspace files (never the redacted diff shown in the API/UI);
* a file is only written if the repository copy is still byte-identical to what the task started from,
  so work done in the repository since then is never overwritten;
* everything is validated before the first write; if anything conflicts, nothing is written;
* secret-looking files, `.git`, path traversal, symlinks and escapes through links are refused;
* if a write fails midway, the files already written are restored.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from harnix.execution import git
from harnix.policies.paths import (
    PLACEHOLDER_FILES,
    PROTECTED_DIRS,
    SECRET_DIRS,
    SECRET_GLOBS,
    PathViolation,
    WorkspacePaths,
)


class ApplyConflict(Exception):
    """Nothing was written. `conflicts` lists every reason, one line each."""

    def __init__(self, conflicts: list[str]):
        super().__init__("; ".join(conflicts))
        self.conflicts = conflicts


@dataclass(frozen=True)
class Change:
    path: str
    status: str  # A (added) | M (modified) | D (deleted)


def unsafe_reason(rel: str) -> str | None:
    p, w = PurePosixPath(rel), PureWindowsPath(rel)
    if p.is_absolute() or w.drive or w.root or ".." in p.parts or ".." in w.parts:
        return "path escapes the repository"
    parts = [x for x in p.parts if x != "."]
    if not parts:
        return "empty path"
    if any(x in PROTECTED_DIRS for x in parts):
        return "inside a protected directory (.git/.forge)"
    name = parts[-1].lower()
    if any(x in SECRET_DIRS for x in parts) or (
        name not in PLACEHOLDER_FILES and any(fnmatch.fnmatch(name, g) for g in SECRET_GLOBS)
    ):
        return "looks like a secret/credential file"
    return None


def _repo_file(paths: WorkspacePaths, rel: str) -> tuple[Path | None, str | None]:
    """Resolve inside the repository; (None, reason) if it would escape (e.g. via a symlink in the repo)."""
    try:
        return paths.resolve(rel), None
    except PathViolation:
        return None, "resolves outside the repository (symlink?)"


def apply_workspace_to_repo(
    ws: Path, repo: Path, *, dry_run: bool = False, from_ref: str = "HEAD", to_ref: str | None = None
) -> list[Change]:
    """Copy the changes from snapshot `from_ref` (what the repository is expected to contain; default: the
    session baseline) to snapshot `to_ref` (default: the workspace as it is now) into `repo`.
    Raises ApplyConflict (nothing written) on any problem."""
    paths = WorkspacePaths(repo)
    changes = [Change(path, status) for status, path in git.changed_paths_raw(ws, from_ref, to_ref)]
    special = git.special_modes(ws, from_ref, to_ref) if to_ref else {}
    conflicts: list[str] = []
    writes: list[tuple[Change, Path]] = []

    for c in changes:
        if (why := unsafe_reason(c.path)) is not None:
            conflicts.append(f"{c.path}: refused, {why}")
            continue
        dest, why = _repo_file(paths, c.path)
        if dest is None:
            conflicts.append(f"{c.path}: refused, {why}")
            continue
        src = ws / c.path
        if to_ref:
            if c.path in special and c.status in ("A", "M"):
                conflicts.append(f"{c.path}: refused, not a regular file in the workspace")
                continue
        elif c.status in ("A", "M") and (src.is_symlink() or not src.is_file()):
            conflicts.append(f"{c.path}: refused, not a regular file in the workspace")
            continue
        if dest.is_symlink():
            conflicts.append(f"{c.path}: refused, it is a symlink in your repository")
            continue
        exists = dest.exists()
        if c.status == "A":
            if exists:
                conflicts.append(f"{c.path}: already exists in your repository")
                continue
        else:
            base = git.baseline_bytes(ws, c.path, from_ref)
            if not exists:
                if c.status == "M":
                    conflicts.append(f"{c.path}: no longer exists in your repository")
                    continue
            elif not dest.is_file() or dest.read_bytes() != base:
                conflicts.append(f"{c.path}: changed in your repository since the task started")
                continue
        writes.append((c, dest))

    if conflicts:
        raise ApplyConflict(conflicts)
    if dry_run:
        return changes

    undo: list[tuple[Path, bytes | None]] = []  # (path, previous bytes or None if it did not exist)
    try:
        for c, dest in writes:
            if c.status == "D":
                if dest.exists():
                    undo.append((dest, dest.read_bytes()))
                    dest.unlink()
                continue
            undo.append((dest, dest.read_bytes() if dest.exists() else None))
            dest.parent.mkdir(parents=True, exist_ok=True)
            if to_ref:
                new = git.blob(ws, c.path, to_ref)
                if new is None:
                    raise ApplyConflict([f"{c.path}: missing from the snapshot being applied"])
                dest.write_bytes(new)
            else:
                dest.write_bytes((ws / c.path).read_bytes())
                if os.name != "nt":
                    dest.chmod((ws / c.path).stat().st_mode & 0o777)
    except Exception as e:
        for path, old in reversed(undo):
            try:
                if old is None:
                    path.unlink(missing_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(old)
            except OSError:
                pass
        raise ApplyConflict(
            [f"write failed ({type(e).__name__}: {e}); earlier writes were rolled back"]
        ) from e
    return changes


def revert_applied(
    ws: Path,
    repo: Path,
    files: list[tuple[str, str]],
    *,
    dry_run: bool = False,
    from_ref: str = "HEAD",
    to_ref: str | None = None,
) -> list[Change]:
    """Undo an earlier apply: put the repository back to what it was before the task's changes were applied.

    `files` is (status, path) of what was applied. The same strictness as applying: a file is only touched if the
    repository copy is still byte-identical to what the apply wrote (so work done since is never lost), everything
    is checked before the first write, and a failed write restores what was already changed.
    Original contents come from the task workspace's baseline commit, which Forge keeps.
    """
    paths = WorkspacePaths(repo)
    conflicts: list[str] = []
    plan: list[
        tuple[Change, Path, bytes | None]
    ] = []  # (change, repo file, bytes to put back; None = delete)

    for status, rel in files:
        c = Change(rel, status)
        if (why := unsafe_reason(rel)) is not None:
            conflicts.append(f"{rel}: refused, {why}")
            continue
        dest, why = _repo_file(paths, rel)
        if dest is None:
            conflicts.append(f"{rel}: refused, {why}")
            continue
        if dest.is_symlink():
            conflicts.append(f"{rel}: refused, it is a symlink in your repository")
            continue
        base = git.baseline_bytes(ws, rel, from_ref)
        src = ws / rel
        applied = git.blob(ws, rel, to_ref) if to_ref else None  # what the apply wrote
        if status == "D":  # the apply deleted it: bring it back, unless something new took its place
            if base is None:
                conflicts.append(f"{rel}: the original content is not available in the task workspace")
            elif dest.exists():
                conflicts.append(f"{rel}: exists again in your repository (not overwritten)")
            else:
                plan.append((c, dest, base))
            continue
        if to_ref:
            if applied is None:
                conflicts.append(f"{rel}: the version that was applied is not available in the workspace")
                continue
        elif src.is_symlink() or not src.is_file():
            conflicts.append(f"{rel}: the task's version is not available in the workspace")
            continue
        if (
            not dest.exists()
        ):  # deleted since: nothing to undo for an added file, nothing to restore over otherwise
            if status == "A":
                continue
            conflicts.append(f"{rel}: no longer exists in your repository")
            continue
        if not dest.is_file() or dest.read_bytes() != (applied if to_ref else src.read_bytes()):
            conflicts.append(f"{rel}: changed in your repository since the task was applied")
            continue
        if status == "A":
            plan.append((c, dest, None))
        elif base is None:
            conflicts.append(f"{rel}: the original content is not available in the task workspace")
        else:
            plan.append((c, dest, base))

    if conflicts:
        raise ApplyConflict(conflicts)
    if dry_run:
        return [c for c, _, _ in plan]

    undo: list[tuple[Path, bytes | None]] = []
    try:
        for _c, dest, content in plan:
            undo.append((dest, dest.read_bytes() if dest.exists() else None))
            if content is None:
                dest.unlink()
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(content)
    except Exception as e:
        for path, old in reversed(undo):
            try:
                if old is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(old)
            except OSError:
                pass
        raise ApplyConflict(
            [f"write failed ({type(e).__name__}: {e}); earlier writes were rolled back"]
        ) from e
    return [c for c, _, _ in plan]
