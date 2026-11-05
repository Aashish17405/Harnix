"""Filesystem tools. Every path goes through WorkspacePaths (defense in depth; policy checks first)."""

from __future__ import annotations

import os
from pathlib import Path

from harnix.execution import git
from harnix.execution.workspace import SKIP_DIRS
from harnix.policies.paths import PathViolation
from harnix.tools.base import ToolContext, ToolResult, truncate
from harnix.tools.schemas import (
    DeleteFileArgs,
    EditFileArgs,
    GitDiffArgs,
    GitStatusArgs,
    ListFilesArgs,
    ReadFileArgs,
    SearchTextArgs,
    WriteFileArgs,
)


def list_files(a: ListFilesArgs, ctx: ToolContext) -> ToolResult:
    base = ctx.paths.resolve(a.path)
    if not base.is_dir():
        return ToolResult(False, error=f"Not a directory: {a.path}")
    entries: list[str] = []
    truncated = False
    for cur, dirs, files in os.walk(base):  # os.walk does not follow symlinked dirs
        cur_p = Path(cur)
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not (cur_p / d).is_symlink())
        files = sorted(
            f for f in files if not (cur_p / f).is_symlink()
        )  # never list links (may point outside)
        for name, is_dir in [*((d, True) for d in dirs), *((f, False) for f in files)]:
            p = cur_p / name
            if ctx.paths.is_secret(p):
                continue
            entries.append(ctx.paths.rel(p) + ("/" if is_dir else ""))
            if len(entries) >= a.max_entries:
                truncated = True
                break
        if truncated or not a.recursive:
            break
    return ToolResult(True, {"path": ctx.paths.rel(base), "entries": entries, "truncated": truncated})


def read_file(a: ReadFileArgs, ctx: ToolContext) -> ToolResult:
    p = ctx.paths.resolve(a.path)
    if ctx.paths.is_secret(p):
        raise PathViolation("Reading secret files is not permitted")
    if not p.is_file():
        return ToolResult(False, error=f"File not found: {a.path}")
    with open(p, "rb") as f:
        raw = f.read(ctx.max_read + 1)
    clipped = len(raw) > ctx.max_read
    raw = raw[: ctx.max_read]
    if b"\x00" in raw[:4096]:
        return ToolResult(False, error="Binary file; not readable as text")
    lines = raw.decode("utf-8", "replace").splitlines()
    chunk = lines[a.start_line - 1 : a.start_line - 1 + a.max_lines]
    return ToolResult(
        True,
        {
            "path": ctx.paths.rel(p), "start_line": a.start_line, "end_line": a.start_line + len(chunk) - 1,
            "total_lines_read": len(lines), "content": "\n".join(chunk),
            "truncated": clipped or a.start_line - 1 + a.max_lines < len(lines),
        },
    )  # fmt: skip


def search_text(a: SearchTextArgs, ctx: ToolContext) -> ToolResult:
    base = ctx.paths.resolve(a.path)
    code, out = git.grep(
        ctx.workspace, a.pattern, path=ctx.paths.rel(base), regex=a.regex, case_sensitive=a.case_sensitive
    )
    if code == 1:
        return ToolResult(True, {"matches": [], "count": 0})
    if code != 0:
        return ToolResult(False, error=f"search failed: {out.strip()[:300]}")
    lines = [ln[:300] for ln in out.splitlines()]
    return ToolResult(
        True,
        {"matches": lines[: a.max_results], "count": len(lines), "truncated": len(lines) > a.max_results},
    )


def git_status(_: GitStatusArgs, ctx: ToolContext) -> ToolResult:
    out = git.status(ctx.workspace)
    return ToolResult(True, {"status": out.strip() or "(no changes since baseline)"})


def git_diff(a: GitDiffArgs, ctx: ToolContext) -> ToolResult:
    rel = ctx.paths.rel(ctx.paths.resolve(a.path)) if a.path else None
    out, trunc = truncate(git.diff(ctx.workspace, rel), ctx.max_output)
    return ToolResult(True, {"diff": out or "(no changes since baseline)", "truncated": trunc})


def _check_writable(p, ctx: ToolContext) -> None:
    if ctx.paths.is_protected(p):
        raise PathViolation("Path is protected (.git/.forge) and cannot be modified")


def write_file(a: WriteFileArgs, ctx: ToolContext) -> ToolResult:
    p = ctx.paths.resolve(a.path)
    _check_writable(p, ctx)
    data = a.content.encode("utf-8")
    if len(data) > ctx.max_write:
        return ToolResult(False, error=f"Content is {len(data)} bytes; limit is {ctx.max_write}")
    if p.is_dir():
        return ToolResult(False, error=f"Is a directory: {a.path}")
    created = not p.exists()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return ToolResult(
        True, {"path": ctx.paths.rel(p), "bytes_written": len(data), "created": created}, mutated=True
    )


def edit_file(a: EditFileArgs, ctx: ToolContext) -> ToolResult:
    p = ctx.paths.resolve(a.path)
    _check_writable(p, ctx)
    if not p.is_file():
        return ToolResult(False, error=f"File not found: {a.path}")
    try:
        text = p.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return ToolResult(False, error="File is not valid UTF-8")
    old, new = a.old_text, a.new_text
    if "\r\n" in text and "\r" not in old:  # preserve CRLF files
        old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
    n = text.count(old)
    if n == 0:
        return ToolResult(False, error="old_text not found (it must match exactly, including whitespace)")
    if n > 1 and not a.replace_all:
        return ToolResult(False, error=f"old_text matches {n} times; add context or set replace_all=true")
    out = text.replace(old, new) if a.replace_all else text.replace(old, new, 1)
    if len(out.encode("utf-8")) > ctx.max_write:
        return ToolResult(False, error=f"Resulting file exceeds {ctx.max_write} bytes")
    p.write_bytes(out.encode("utf-8"))
    return ToolResult(
        True, {"path": ctx.paths.rel(p), "replacements": n if a.replace_all else 1}, mutated=True
    )


def delete_file(a: DeleteFileArgs, ctx: ToolContext) -> ToolResult:
    p = ctx.paths.resolve(a.path)
    _check_writable(p, ctx)
    if not p.is_file():
        return ToolResult(False, error=f"Not a file: {a.path}")
    p.unlink()
    return ToolResult(True, {"path": ctx.paths.rel(p), "deleted": True}, mutated=True)
