"""Provider-neutral message/tool types and the ModelProvider interface. No provider SDK imports here."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

INVALID_JSON_KEY = (
    "__invalid_json__"  # adapters put the raw text here when tool arguments were not valid JSON
)


@dataclass
class TextBlock:
    text: str


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False


@dataclass
class ImageBlock:
    """A user-supplied image (raster, already validated). `data` is base64 without any data: prefix."""

    media_type: str
    data: str
    name: str = ""
    detail: str = "high"  # how closely the model looks (OpenAI `detail`); high = its most careful setting
    ref: str = ""  # "<task id>:<n>": which stored attachment this is (lets a saved conversation point at it)


Block = TextBlock | ToolUseBlock | ToolResultBlock | ImageBlock


@dataclass
class Message:
    role: Literal["user", "assistant"]
    content: list[Block]
    raw: Any = None  # opaque provider payload (e.g. thinking blocks) replayed verbatim by the same adapter


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0  # part of output_tokens spent thinking, when the provider reports it


# Reasoning depth requested for a model call. "xhigh" is the "extra high" level.
THINKING_LEVELS = ("low", "medium", "high", "xhigh")


@dataclass
class ModelResponse:
    text: str
    tool_calls: list[ToolUseBlock]
    stop_reason: str
    usage: Usage = field(default_factory=Usage)
    message: Message | None = None  # assistant turn to append to the history
    # what the adapter actually sent for the requested thinking level, e.g. {"requested": "high",
    # "applied": "high"} or {"requested": "high", "applied": "none", "reason": "..."}; None = not requested
    thinking: dict[str, Any] | None = None


class ProviderError(Exception):
    """Provider failure. `retryable` marks transient errors (rate limit, 5xx, network).

    kind `context_overflow` means the request was too long for the model: the loop compacts and retries once.
    """

    def __init__(self, message: str, *, retryable: bool = False, kind: str = "provider_error"):
        super().__init__(message)
        self.retryable = retryable
        self.kind = kind


class ModelProvider(Protocol):
    name: str
    model: str

    def complete(
        self, *, system: str, messages: list[Message], tools: list[ToolSpec], max_tokens: int, timeout: float,
        thinking: str | None = None,
    ) -> ModelResponse: ...  # fmt: skip
