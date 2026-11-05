"""Anthropic Messages API adapter (official SDK). All Anthropic-specific behaviour lives here."""

from __future__ import annotations

from typing import Any

import anthropic

from harnix.models.base import (
    ImageBlock,
    Message,
    ModelResponse,
    ProviderError,
    TextBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
    Usage,
)


def _to_api_message(m: Message) -> dict[str, Any]:
    if m.role == "assistant" and m.raw is not None:
        return {"role": "assistant", "content": m.raw}  # verbatim replay keeps thinking blocks valid
    blocks: list[dict[str, Any]] = []
    for b in m.content:
        if isinstance(b, ImageBlock):
            blocks.append(
                {"type": "image", "source": {"type": "base64", "media_type": b.media_type, "data": b.data}}
            )
        elif isinstance(b, TextBlock):
            if b.text.strip():
                blocks.append({"type": "text", "text": b.text})
        elif isinstance(b, ToolUseBlock):
            blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
        elif isinstance(b, ToolResultBlock):
            blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": b.tool_use_id,
                    "content": b.content,
                    "is_error": b.is_error,
                }
            )
    return {"role": m.role, "content": blocks}


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str, *, client: anthropic.Anthropic | None = None):
        self.model = model
        self.client = client or anthropic.Anthropic(api_key=api_key, max_retries=2)

    def complete(
        self, *, system: str, messages: list[Message], tools: list[ToolSpec], max_tokens: int, timeout: float,
        thinking: str | None = None,
    ) -> ModelResponse:  # fmt: skip
        extra: dict[str, Any] = {}
        if tools:
            extra["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools
            ]
        if thinking:
            # Current Claude models: adaptive thinking, depth set by effort (low | medium | high | xhigh | max).
            # Fixed `budget_tokens` budgets are rejected by the newest models, so they are not used.
            extra["thinking"] = {"type": "adaptive"}
            extra["output_config"] = {"effort": thinking}
        try:
            resp = self.client.with_options(timeout=timeout).messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[_to_api_message(m) for m in messages],  # type: ignore[arg-type]
                **extra,
            )
        except anthropic.RateLimitError as e:
            raise ProviderError(
                f"Rate limited by Anthropic API: {e}", retryable=True, kind="rate_limit"
            ) from e
        except anthropic.APITimeoutError as e:
            raise ProviderError("Model request timed out", retryable=True, kind="timeout") from e
        except anthropic.APIConnectionError as e:
            raise ProviderError(
                f"Network error contacting Anthropic API: {e}", retryable=True, kind="network"
            ) from e
        except anthropic.AuthenticationError as e:
            raise ProviderError(
                "Anthropic API rejected the API key (check ANTHROPIC_API_KEY)", kind="auth"
            ) from e
        except anthropic.PermissionDeniedError as e:
            raise ProviderError(f"Anthropic API key lacks permission: {e.message}", kind="permission") from e
        except anthropic.NotFoundError as e:
            raise ProviderError(
                f"Model or endpoint not found (check FORGE_MODEL={self.model}): {e.message}", kind="not_found"
            ) from e
        except anthropic.BadRequestError as e:
            m = e.message.lower()
            if "prompt is too long" in m or "context window" in m or "too many tokens" in m:
                raise ProviderError(
                    f"The request is too long for {self.model}: {e.message}", kind="context_overflow"
                ) from e
            raise ProviderError(f"Anthropic API rejected the request: {e.message}", kind="bad_request") from e
        except anthropic.APIStatusError as e:
            raise ProviderError(
                f"Anthropic API error {e.status_code}: {e.message}",
                retryable=e.status_code >= 500,
                kind="api_error",
            ) from e

        if resp.stop_reason == "refusal":
            cat = getattr(getattr(resp, "stop_details", None), "category", None)
            raise ProviderError(f"Model declined the request (refusal, category={cat})", kind="refusal")

        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [ToolUseBlock(b.id, b.name, dict(b.input)) for b in resp.content if b.type == "tool_use"]  # type: ignore[arg-type]
        u = resp.usage
        usage = Usage(
            (u.input_tokens or 0)
            + (getattr(u, "cache_read_input_tokens", 0) or 0)
            + (getattr(u, "cache_creation_input_tokens", 0) or 0),
            u.output_tokens or 0,
        )
        msg = Message("assistant", [TextBlock(text), *calls], raw=list(resp.content))
        info = {"requested": thinking, "applied": thinking} if thinking else None
        return ModelResponse(
            text=text,
            tool_calls=calls,
            stop_reason=resp.stop_reason or "",
            usage=usage,
            message=msg,
            thinking=info,
        )
