"""Saving a conversation at the end of a turn and restoring it for the next one (sessions).

What is saved is what the model saw, as plain JSON. Two things are deliberately not saved:
* provider payloads (`Message.raw`: encrypted reasoning, thinking blocks): they are only valid inside the turn that
  produced them, and every adapter can rebuild a valid request from the blocks alone;
* image bytes: an image is stored once, as the task's attachment, and the message keeps a reference to it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from harnix.models.base import ImageBlock, Message, TextBlock, ToolResultBlock, ToolUseBlock

MAX_SAVED_BYTES = (
    8_000_000  # a saved conversation larger than this is not stored (the next turn starts a fresh context)
)


def dump_messages(history: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in history:
        blocks: list[dict[str, Any]] = []
        for b in m.content:
            if isinstance(b, TextBlock):
                blocks.append({"t": "text", "text": b.text})
            elif isinstance(b, ToolUseBlock):
                blocks.append({"t": "use", "id": b.id, "name": b.name, "input": b.input})
            elif isinstance(b, ToolResultBlock):
                blocks.append({"t": "result", "id": b.tool_use_id, "content": b.content, "error": b.is_error})
            elif isinstance(b, ImageBlock):
                if b.ref:
                    blocks.append({"t": "image", "ref": b.ref, "name": b.name, "detail": b.detail})
                else:
                    blocks.append({"t": "text", "text": f"[image {b.name or ''} was attached here]"})
        if blocks:
            out.append({"role": m.role, "content": blocks})
    return out


def load_messages(
    data: list[dict[str, Any]], load_image: Callable[[str], ImageBlock | None]
) -> list[Message]:
    msgs: list[Message] = []
    for m in data or []:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        blocks: list[Any] = []
        for b in m.get("content", []):
            kind = b.get("t")
            if kind == "text":
                blocks.append(TextBlock(str(b.get("text", ""))))
            elif kind == "use":
                blocks.append(ToolUseBlock(str(b["id"]), str(b["name"]), dict(b.get("input") or {})))
            elif kind == "result":
                blocks.append(ToolResultBlock(str(b["id"]), str(b.get("content", "")), bool(b.get("error"))))
            elif kind == "image":
                img = load_image(str(b.get("ref", "")))
                blocks.append(
                    img
                    if img is not None
                    else TextBlock(f"[image {b.get('name', '')} is no longer available]")
                )
        if blocks:
            msgs.append(Message(role, blocks))  # type: ignore[arg-type]
    return msgs


def summary_of_unsaved_turn(
    turn: int, instructions: str, status: str, reason: str | None, files: list[str]
) -> list[Message]:
    """A turn that ended early (cancelled, failed, out of steps) has no clean conversation to save: the next turn
    is told what was asked and how it ended, so it neither repeats nor ignores that work."""
    changed = f" Files changed so far: {', '.join(files[:40])}." if files else " No files were changed."
    return [
        Message("user", [TextBlock(instructions)]),
        Message(
            "assistant",
            [
                TextBlock(
                    f"[Turn {turn} ended early ({reason or status}) before I finished.{changed} My work in the workspace was kept.]"
                )
            ],
        ),
    ]
