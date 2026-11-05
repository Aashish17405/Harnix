"""Task workspaces: an isolated copy of the source repository. The source tree is never modified."""

from __future__ import annotations

import fnmatch
import os
import shutil
from pathlib import Path

from harnix.execution import git
from harnix.policies.paths import PLACEHOLDER_FILES, SECRET_DIRS, SECRET_GLOBS

SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".forge",
}


class WorkspaceError(Exception):
    pass


def _skip_file(name: str) -> bool:
    if name in PLACEHOLDER_FILES:
        return False
    return name.endswith(".pyc") or any(fnmatch.fnmatch(name.lower(), g) for g in SECRET_GLOBS)


class WorkspaceManager:
    def __init__(self, root: Path, max_copy_mb: int = 500):
        self.root = root
        self.max_bytes = max_copy_mb * 1024 * 1024

    def path_for(self, task_id: str) -> Path:
        return self.root / task_id

    def create(self, task_id: str, repo: Path) -> Path:
        """Copy `repo` (working tree incl. uncommitted changes, minus secrets/symlinks/caches) and baseline it."""
        dest = self.path_for(task_id)
        if dest.exists():
            raise WorkspaceError(f"Workspace already exists: {dest}")
        self.root.mkdir(parents=True, exist_ok=True)
        total = 0
        try:
            dest.mkdir()
            for cur, dirs, files in os.walk(repo, followlinks=False):
                dirs[:] = [
                    d
                    for d in dirs
                    if d not in SKIP_DIRS
                    and d not in SECRET_DIRS
                    and not os.path.islink(os.path.join(cur, d))
                ]
                target = dest / Path(cur).relative_to(repo)
                target.mkdir(parents=True, exist_ok=True)
                for f in files:
                    src = Path(cur, f)
                    if src.is_symlink() or _skip_file(f):
                        continue
                    total += src.stat().st_size
                    if total > self.max_bytes:
                        raise WorkspaceError(
                            f"Repository exceeds the {self.max_bytes // 2**20} MB copy limit (FORGE_MAX_REPO_COPY_MB)"
                        )
                    shutil.copy2(src, target / f)
            git.init_baseline(dest)
        except Exception:
            shutil.rmtree(dest, ignore_errors=True)
            raise
        return dest
