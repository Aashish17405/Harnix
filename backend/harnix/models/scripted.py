"""Scripted MOCK provider for tests, the evaluation self-test and demo runs.

It replays a fixed script. It is NOT a model: runs using it prove harness mechanics only and are
labelled `scripted-mock` in the task record, the API and the UI.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from harnix.models.base import Message, ModelResponse, ProviderError, TextBlock, ToolSpec, ToolUseBlock, Usage

Step = dict[str, Any] | Callable[[list[Message]], dict[str, Any]]
# step: {"text": str, "tool_calls": [{"name": str, "input": {...}}], "usage": {"input": n, "output": n}}
#   or  {"error": str, "retryable": bool}


class ScriptedProvider:
    name = "scripted-mock"

    def __init__(self, steps: Sequence[Step], model: str = "scripted"):
        self.steps = list(steps)
        self.model = model
        self.calls = 0
        self.requests: list[dict[str, Any]] = []  # what each call asked for (tests inspect it)
        self._lock = threading.Lock()

    @classmethod
    def from_file(cls, path: str) -> ScriptedProvider:
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def complete(
        self, *, system: str, messages: list[Message], tools: list[ToolSpec], max_tokens: int, timeout: float,
        thinking: str | None = None,
    ) -> ModelResponse:  # fmt: skip
        with self._lock:
            idx = self.calls
            self.calls += 1
            self.requests.append(
                {"thinking": thinking, "tools": [t.name for t in tools], "messages": len(messages)}
            )
        if idx >= len(self.steps):
            raise ProviderError("Scripted provider exhausted its script", kind="script_exhausted")
        step = self.steps[idx]
        if callable(step):
            step = step(messages)
        if "error" in step:
            raise ProviderError(step["error"], retryable=bool(step.get("retryable")), kind="scripted_error")
        calls = [
            ToolUseBlock(f"toolu_{uuid.uuid4().hex[:12]}", c["name"], c.get("input", {}))
            for c in step.get("tool_calls", [])
        ]
        text = step.get("text", "")
        u = step.get("usage", {})
        return ModelResponse(
            text=text,
            tool_calls=calls,
            stop_reason="tool_use" if calls else "end_turn",
            usage=Usage(u.get("input", 100), u.get("output", 20)),
            message=Message("assistant", [TextBlock(text), *calls]),
            thinking={"requested": thinking, "applied": thinking, "reason": "scripted mock"}
            if thinking
            else None,
        )
