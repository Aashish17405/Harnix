"""Workspace path containment. Every filesystem tool resolves paths through here."""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path


class PathViolation(Exception):
    pass


SECRET_GLOBS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa*",
    "id_ed25519*",
    "*.pfx",
    ".npmrc",
    ".pypirc",
    "credentials",
)
SECRET_DIRS = {".ssh", ".aws", ".gnupg", ".kube"}
PROTECTED_DIRS = {".git", ".forge"}  # never writable by the agent
PLACEHOLDER_FILES = {".env.example", ".env.sample", ".env.template"}


def _norm(p: Path | str) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


ANY_ROOT = Path("*")  # the operator's explicit "any folder" setting, see Settings.repo_roots


def authorize_repo(raw: str, roots: list[Path]) -> Path:
    """A task may only target a directory inside one of the operator-authorized repository roots."""
    if not roots:
        raise PathViolation(
            "No repository roots are authorized. Set FORGE_ALLOWED_REPO_ROOTS to a directory."
        )
    p = Path(raw).expanduser().resolve()
    if not p.is_dir():
        raise PathViolation(f"Repository path is not a directory: {raw}")
    if ANY_ROOT in roots:  # FORGE_ALLOWED_REPO_ROOTS=*: any folder, but never a whole drive
        if p.parent == p:
            raise PathViolation(f"Refusing a drive root as a repository: {raw}")
        return p
    if not any(_norm(p) == _norm(r) or _norm(p).startswith(_norm(r) + os.sep) for r in roots):
        raise PathViolation("Repository is outside the authorized roots (FORGE_ALLOWED_REPO_ROOTS)")
    return p


class WorkspacePaths:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def resolve(self, raw: str) -> Path:
        """Resolve `raw` (relative to the workspace, or absolute inside it) and prove it stays inside.

        Symlinks are followed before the containment check, so links pointing outside are rejected.
        """
        if "\x00" in raw:
            raise PathViolation("NUL byte in path")
        candidate = Path(raw)
        full = candidate if candidate.is_absolute() else self.root / candidate
        resolved = full.resolve()  # non-strict: resolves existing prefix, incl. symlinks
        root_n, res_n = _norm(self.root), _norm(resolved)
        if res_n != root_n and not res_n.startswith(root_n + os.sep):
            raise PathViolation(f"Path escapes the workspace: {raw!r}")
        return resolved

    def rel(self, p: Path) -> str:
        r = p.relative_to(self.root).as_posix()
        return r or "."

    def is_protected(self, p: Path) -> bool:
        return any(part in PROTECTED_DIRS for part in p.relative_to(self.root).parts)

    def is_secret(self, p: Path) -> bool:
        parts = p.relative_to(self.root).parts
        if not parts:
            return False
        if any(x in SECRET_DIRS for x in parts):
            return True
        name = parts[-1].lower()
        return name not in PLACEHOLDER_FILES and any(fnmatch.fnmatch(name, g) for g in SECRET_GLOBS)


def repo_key(repo: Path) -> str:
    """Stable id of a repository: a hash of its resolved path (namespaces the graph and project memory)."""
    import hashlib

    return hashlib.sha256(os.path.normcase(str(repo.resolve())).encode()).hexdigest()[:16]
