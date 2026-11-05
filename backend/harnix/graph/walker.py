"""Enumerate the files worth indexing in a workspace, cheaply and safely.

Same exclusions as the workspace copy: no symlinks, no cache/vendor directories, no secret-looking files or
directories. Only files with a recognised text/source extension are considered.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from harnix.execution.workspace import SKIP_DIRS
from harnix.graph.extract import language_of
from harnix.policies.paths import PLACEHOLDER_FILES, SECRET_DIRS, SECRET_GLOBS


@dataclass(frozen=True)
class Candidate:
    path: str  # posix, relative to the workspace
    size: int
    mtime_ns: int


@dataclass
class WalkResult:
    candidates: list[Candidate]
    truncated: bool  # more indexable files existed than `max_files`


def secret_name(name: str) -> bool:
    n = name.lower()
    return n not in PLACEHOLDER_FILES and any(fnmatch.fnmatch(n, g) for g in SECRET_GLOBS)


def _iter(root: Path) -> Iterator[Candidate]:
    for cur, dirs, files in os.walk(root, followlinks=False):
        cur_p = Path(cur)
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in SKIP_DIRS and d not in SECRET_DIRS and not d.endswith(".egg-info")
            and not (cur_p / d).is_symlink()
        )  # fmt: skip
        for name in sorted(files):
            if secret_name(name) or language_of(name) is None:
                continue
            p = cur_p / name
            try:
                if p.is_symlink():
                    continue
                st = p.stat()
            except OSError:
                continue
            yield Candidate(p.relative_to(root).as_posix(), st.st_size, st.st_mtime_ns)


def walk(root: Path, max_files: int) -> WalkResult:
    out: list[Candidate] = []
    for c in _iter(root):
        if len(out) >= max_files:
            return WalkResult(out, True)
        out.append(c)
    return WalkResult(out, False)
