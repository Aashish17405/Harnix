"""Repository inspection: project type, package manager, likely commands, repo instructions.

Everything read here comes from the (untrusted) repository and is treated as data.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

INSTRUCTION_FILES = ("HARNIX.md", "AGENTS.md", "CLAUDE.md", ".claude/CLAUDE.md")
MAX_INSTRUCTION_CHARS = 8000
_SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", ".harnix", ".forge"}


@dataclass
class ProjectInfo:
    kinds: list[str] = field(default_factory=list)
    package_managers: list[str] = field(default_factory=list)
    commands: dict[str, str] = field(default_factory=dict)  # test | lint | typecheck | build
    instructions: dict[str, str] = field(default_factory=dict)  # file -> (truncated) text
    notes: list[str] = field(default_factory=list)


def _read(p: Path, limit: int = 200_000) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _has_python_tests(root: Path) -> bool:
    for cur, dirs, files in os.walk(root):
        depth = len(Path(cur).relative_to(root).parts)
        dirs[:] = [d for d in dirs if d not in _SKIP] if depth < 3 else []
        if any(
            (f.startswith("test_") and f.endswith(".py")) or f.endswith("_test.py") or f == "conftest.py"
            for f in files
        ):
            return True
    return False


def inspect_project(root: Path) -> ProjectInfo:
    info = ProjectInfo()
    names = {p.name for p in root.iterdir()} if root.exists() else set()
    pyproject = _read(root / "pyproject.toml") if "pyproject.toml" in names else ""

    if names & {
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "requirements.txt",
        "Pipfile",
    } or _has_python_tests(root):
        info.kinds.append("python")
        pm = (
            "poetry"
            if "poetry.lock" in names
            else "uv"
            if "uv.lock" in names
            else "pipenv"
            if "Pipfile" in names
            else "pip"
        )
        info.package_managers.append(pm)
        if _has_python_tests(root) or "[tool.pytest" in pyproject or "pytest.ini" in names:
            info.commands["test"] = "python -m pytest -q"
        if names & {"ruff.toml", ".ruff.toml"} or "[tool.ruff" in pyproject:
            info.commands["lint"] = "python -m ruff check ."
        if "pyrightconfig.json" in names or "[tool.pyright" in pyproject:
            info.commands["typecheck"] = "python -m pyright"
        elif "mypy.ini" in names or "[tool.mypy" in pyproject:
            info.commands["typecheck"] = "python -m mypy ."

    if "package.json" in names:
        info.kinds.append("node")
        pm = "pnpm" if "pnpm-lock.yaml" in names else "yarn" if "yarn.lock" in names else "npm"
        info.package_managers.append(pm)
        try:
            scripts = json.loads(_read(root / "package.json")).get("scripts", {}) or {}
        except (ValueError, AttributeError):
            scripts = {}
            info.notes.append("package.json could not be parsed")
        test = scripts.get("test", "")
        if test and "no test specified" not in test:
            info.commands.setdefault("test", f"{pm} test")
        for kind, keys in (
            ("lint", ("lint",)),
            ("typecheck", ("typecheck", "type-check")),
            ("build", ("build",)),
        ):
            for k in keys:
                if k in scripts:
                    info.commands.setdefault(kind, f"{pm} run {k}")
                    break

    if "Cargo.toml" in names:
        info.kinds.append("rust")
        info.package_managers.append("cargo")
        info.commands.update(test="cargo test", lint="cargo clippy", build="cargo build")
    if "go.mod" in names:
        info.kinds.append("go")
        info.package_managers.append("go")
        info.commands.update(test="go test ./...", lint="go vet ./...", build="go build ./...")
    if "Makefile" in names:
        info.notes.append("Makefile present (make targets are not auto-run; they require approval)")

    for rel in INSTRUCTION_FILES:
        p = root / rel
        if p.is_file() and not p.is_symlink():
            text = _read(p, MAX_INSTRUCTION_CHARS + 1)
            info.instructions[rel] = text[:MAX_INSTRUCTION_CHARS] + (
                "\n...[truncated]" if len(text) > MAX_INSTRUCTION_CHARS else ""
            )
    if not info.kinds:
        info.notes.append("No known project type detected")
    if "test" not in info.commands:
        info.notes.append("No test command detected")
    return info
