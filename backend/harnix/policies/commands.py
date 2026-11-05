"""Command classification. Commands are never run through a shell; argv is classified here first."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from typing import Literal

DecisionKind = Literal["allow", "deny", "approve"]


@dataclass(frozen=True)
class Decision:
    kind: DecisionKind
    reason: str


class CommandSyntaxError(Exception):
    pass


SHELL_META = set(";|&<>`")
HARD_DENY = {
    "sudo",
    "su",
    "runas",
    "shutdown",
    "reboot",
    "halt",
    "mkfs",
    "format",
    "diskpart",
    "passwd",
    "chown",
    "chmod",
}
GIT_READONLY = {"status", "diff", "log", "show", "rev-parse", "ls-files", "blame"}
GIT_RISKY_FLAGS = ("-c", "--output", "--exec-path", "--upload-pack", "--ext-diff", "--textconv")
PY_MODULES = {"pytest", "unittest", "ruff", "mypy", "pyright", "flake8", "black", "isort"}
PY_PIP_READONLY = {"list", "show", "check", "freeze"}
DIRECT_TOOLS = {"pytest", "ruff", "mypy", "pyright", "flake8", "tsc", "eslint", "prettier"}
NODE_PMS = {"npm", "pnpm", "yarn"}
NODE_SCRIPTS = {"test", "lint", "build", "typecheck", "check", "type-check"}
CARGO_OK = {"test", "check", "build", "clippy", "fmt"}
GO_OK = {"test", "vet", "build", "fmt"}
DESTRUCTIVE = {"rm", "del", "rmdir", "rd", "erase", "mv", "move", "rename", "ren", "truncate", "shred"}


def _unquoted_meta(command: str) -> str | None:
    """First shell metacharacter that appears outside quotes, if any."""
    quote = ""
    for i, ch in enumerate(command):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch in SHELL_META or (ch == "$" and command[i + 1 : i + 2] == "("):
            return ch
    return None


def parse_command(command: str) -> list[str]:
    meta = _unquoted_meta(command)
    if meta:
        raise CommandSyntaxError(
            f"Shell syntax ({meta!r}) is not supported; commands run without a shell. Run one command per call."
        )
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as e:
        raise CommandSyntaxError(f"Cannot parse command: {e}") from e
    if not argv:
        raise CommandSyntaxError("Empty command")
    return argv


def exe_name(argv0: str) -> str:
    base = os.path.basename(argv0.replace("\\", "/")).lower()
    for ext in (".exe", ".cmd", ".bat"):
        if base.endswith(ext):
            base = base[: -len(ext)]
    return base


def classify_command(argv: list[str]) -> Decision:
    a0 = argv[0]
    exe, args = exe_name(a0), argv[1:]
    if exe in HARD_DENY:
        return Decision("deny", f"'{exe}' is never permitted")
    if "/" in a0 or "\\" in a0:
        return Decision("approve", "Executing a path-qualified program requires approval")
    if exe in DESTRUCTIVE:
        return Decision("approve", f"'{exe}' is a destructive operation")
    if exe == "git":
        if not args or args[0].startswith("-"):
            return Decision("approve", "git with global options requires approval")
        sub = args[0]
        if sub == "push":
            return Decision("approve", "git push sends changes to a remote")
        if sub in GIT_READONLY and not any(
            f == x or f.startswith(x + "=") for f in args for x in GIT_RISKY_FLAGS
        ):
            return Decision("allow", "read-only git command")
        return Decision("approve", f"git {sub} may modify repository state")
    if exe in ("python", "python3", "py"):
        if args == ["--version"]:
            return Decision("allow", "version query")
        if len(args) >= 2 and args[0] == "-m":
            if args[1] in PY_MODULES:
                return Decision("allow", f"python -m {args[1]}")
            if args[1] == "pip" and len(args) > 2 and args[2] in PY_PIP_READONLY:
                return Decision("allow", "read-only pip command")
        return Decision("approve", "Arbitrary python execution requires approval")
    if exe in DIRECT_TOOLS:
        return Decision("allow", f"{exe} is a permitted validation tool")
    if exe in NODE_PMS:
        if args[:1] in (["test"], ["t"]) or (args[:1] == ["run"] and args[1:2] and args[1] in NODE_SCRIPTS):
            return Decision("allow", f"{exe} {' '.join(args[:2])}")
        if args in (["--version"], ["ls"]):
            return Decision("allow", "read-only package manager query")
        return Decision("approve", f"{exe} {' '.join(args[:1])} may install or execute arbitrary scripts")
    if exe == "node" and args == ["--version"]:
        return Decision("allow", "version query")
    if exe == "cargo" and args[:1] and args[0] in CARGO_OK:
        return Decision("allow", f"cargo {args[0]}")
    if exe == "go" and args[:1] and args[0] in GO_OK:
        return Decision("allow", f"go {args[0]}")
    return Decision("approve", f"'{exe}' is not on the permitted command list")
