"""OpenAI adapters (official SDK). All OpenAI-specific behaviour lives here.

Two APIs:
* Chat Completions (`OpenAIProvider`): the original adapter. Some models (the Luna tier) accept function tools here
  only with `reasoning_effort=none`, so a requested thinking level is downgraded, and the response says so.
* Responses (`OpenAIResponsesProvider`, FORGE_OPENAI_API=responses): reasoning works together with function tools.
  Reasoning is carried between turns as encrypted items (`store=False` + `include=["reasoning.encrypted_content"]`),
  replayed verbatim, so nothing is stored at OpenAI and the model keeps its chain of thought across tool calls.
"""

from __future__ import annotations

import json
from typing import Any

import openai

from harnix.models.base import (
    INVALID_JSON_KEY,
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

# Forge thinking level -> OpenAI reasoning effort
EFFORT = {"low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh"}
_DOWNGRADE = {"xhigh": "high"}  # when a model rejects a level, try the next one down once


def _parse_arguments(raw: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {INVALID_JSON_KEY: (raw or "")[:200]}
    return parsed if isinstance(parsed, dict) else {INVALID_JSON_KEY: str(raw)[:200]}


def _is_overflow(e: openai.APIStatusError) -> bool:
    text = f"{getattr(e, 'code', '') or ''} {e.message}".lower()
    return "context_length_exceeded" in text or "maximum context length" in text or "too many tokens" in text


def _unsupported_effort(e: openai.BadRequestError) -> bool:
    m = e.message.lower()
    return ("reasoning" in m or "effort" in m) and (
        "unsupported" in m or "not supported" in m or "invalid" in m
    )


def _map_error(e: Exception, model: str) -> ProviderError:
    """One mapping for both APIs: retryable vs fatal, with an actionable message."""
    if isinstance(e, openai.RateLimitError):
        if getattr(e, "code", None) == "insufficient_quota":
            return ProviderError(
                "OpenAI quota/budget exhausted for this API key (insufficient_quota). Add credit or use another key.",
                kind="quota",
            )
        return ProviderError(
            f"Rate limited by the OpenAI API: {e.message}", retryable=True, kind="rate_limit"
        )
    if isinstance(e, openai.APITimeoutError):
        return ProviderError("Model request timed out", retryable=True, kind="timeout")
    if isinstance(e, openai.APIConnectionError):
        return ProviderError(f"Network error contacting the OpenAI API: {e}", retryable=True, kind="network")
    if isinstance(e, openai.AuthenticationError):
        return ProviderError("The OpenAI API rejected the API key (check OPENAI_API_KEY)", kind="auth")
    if isinstance(e, openai.PermissionDeniedError):
        return ProviderError(
            f"OpenAI API key lacks permission for this model: {e.message}", kind="permission"
        )
    if isinstance(e, openai.NotFoundError):
        return ProviderError(
            f"Model not found or not available to this key (check FORGE_MODEL={model}): {e.message}",
            kind="not_found",
        )
    if isinstance(e, openai.BadRequestError):
        if _is_overflow(e):
            return ProviderError(f"The request is too long for {model}: {e.message}", kind="context_overflow")
        return ProviderError(f"The OpenAI API rejected the request: {e.message}", kind="bad_request")
    if isinstance(e, openai.APIStatusError):
        if _is_overflow(e):
            return ProviderError(f"The request is too long for {model}: {e.message}", kind="context_overflow")
        return ProviderError(
            f"OpenAI API error {e.status_code}: {e.message}", retryable=e.status_code >= 500, kind="api_error"
        )
    raise e


# ---- Chat Completions -----------------------------------------------------------------------------------


def _to_api_messages(system: str, messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        if m.role == "assistant":
            text = "".join(b.text for b in m.content if isinstance(b, TextBlock))
            calls = [b for b in m.content if isinstance(b, ToolUseBlock)]
            msg: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": json.dumps(c.input)},
                    }
                    for c in calls
                ]
            out.append(msg)
            continue
        # user turn: tool results become `tool` messages (must directly follow the assistant tool_calls), text a user message
        for b in m.content:
            if isinstance(b, ToolResultBlock):
                out.append({"role": "tool", "tool_call_id": b.tool_use_id, "content": b.content})
        text = "\n\n".join(b.text for b in m.content if isinstance(b, TextBlock) and b.text.strip())
        images = [b for b in m.content if isinstance(b, ImageBlock)]
        if images:
            parts: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
            parts += [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{i.media_type};base64,{i.data}", "detail": i.detail},
                }
                for i in images
            ]
            out.append({"role": "user", "content": parts})
        elif text:
            out.append({"role": "user", "content": text})
    return out


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        reasoning_effort: str | None = None,
        client: openai.OpenAI | None = None,
    ):
        self.model = model
        self.reasoning_effort = (
            reasoning_effort  # operator pin (FORGE_REASONING_EFFORT): wins over task levels
        )
        self.client = client or openai.OpenAI(api_key=api_key, max_retries=2)
        self._no_reasoning_with_tools = False  # learned from the API: e.g. Luna models on Chat Completions
        self._downgrade: dict[str, str] = {}

    def _effort(self, thinking: str | None) -> tuple[str | None, dict[str, Any] | None]:
        if self.reasoning_effort:
            info = {
                "requested": thinking,
                "applied": self.reasoning_effort,
                "reason": "pinned by FORGE_REASONING_EFFORT",
            }
            return self.reasoning_effort, info if thinking else None
        if thinking is None:
            return None, None
        if self._no_reasoning_with_tools:
            return "none", {
                "requested": thinking, "applied": "none",
                "reason": f"{self.model} accepts function tools on Chat Completions only without reasoning; "
                "use FORGE_OPENAI_API=responses for reasoning with tools",
            }  # fmt: skip
        effort = self._downgrade.get(EFFORT[thinking], EFFORT[thinking])
        info = {"requested": thinking, "applied": effort}
        if effort != EFFORT[thinking]:
            info["reason"] = f"{self.model} does not accept effort {EFFORT[thinking]}"
        return effort, info

    def complete(
        self, *, system: str, messages: list[Message], tools: list[ToolSpec], max_tokens: int, timeout: float,
        thinking: str | None = None,
    ) -> ModelResponse:  # fmt: skip
        params: dict[str, Any] = {
            "model": self.model,
            "messages": _to_api_messages(system, messages),
            "max_completion_tokens": max_tokens,  # includes reasoning tokens; `max_tokens` is rejected by these models
        }
        if tools:
            params["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.input_schema}}
                for t in tools
            ]  # fmt: skip
        for _ in range(3):  # at most: the request, one downgrade, one "no reasoning with tools"
            effort, info = self._effort(thinking)
            if effort:
                params["reasoning_effort"] = effort
            else:
                params.pop("reasoning_effort", None)
            try:
                resp = self.client.with_options(timeout=timeout).chat.completions.create(**params)
                break
            except openai.BadRequestError as e:
                if "reasoning_effort" in e.message and "function tools" in e.message.lower():
                    if self.reasoning_effort or self._no_reasoning_with_tools:
                        raise ProviderError(
                            f"{self.model} cannot use tools on the Chat Completions API with reasoning enabled. "
                            "Set FORGE_REASONING_EFFORT=none, or use FORGE_OPENAI_API=responses.",
                            kind="config",
                        ) from e
                    self._no_reasoning_with_tools = True
                    continue
                if effort in _DOWNGRADE and _unsupported_effort(e) and effort not in self._downgrade:
                    self._downgrade[effort] = _DOWNGRADE[effort]
                    continue
                raise _map_error(e, self.model) from e
            except openai.OpenAIError as e:
                raise _map_error(e, self.model) from e
        else:  # pragma: no cover - the loop always breaks or raises
            raise ProviderError("could not build an accepted request", kind="bad_request")

        choice = resp.choices[0]
        msg = choice.message
        if getattr(msg, "refusal", None) or choice.finish_reason == "content_filter":
            raise ProviderError("Model declined the request (refusal / content filter)", kind="refusal")
        text = msg.content or ""
        calls = [
            ToolUseBlock(tc.id, tc.function.name, _parse_arguments(tc.function.arguments))
            for tc in (msg.tool_calls or [])
            if tc.type == "function"
        ]
        stop = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens"}.get(
            choice.finish_reason or "", choice.finish_reason or ""
        )
        u = resp.usage
        details = getattr(u, "completion_tokens_details", None)
        usage = Usage(
            getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0,
            getattr(details, "reasoning_tokens", 0) or 0,
        )  # fmt: skip
        return ModelResponse(
            text=text, tool_calls=calls, stop_reason=stop, usage=usage,
            message=Message("assistant", [TextBlock(text), *calls]), thinking=info,
        )  # fmt: skip


# ---- Responses ----------------------------------------------------------------------------------------


def _item(obj: Any) -> dict[str, Any]:
    d = obj.model_dump(exclude_none=True) if hasattr(obj, "model_dump") else dict(obj)
    if d.get("type") in ("message", "function_call"):
        d.pop("id", None)  # with store=False these ids do not exist server-side; replaying them would 404
        d.pop("status", None)
    return d


def _to_input(messages: list[Message]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "assistant":
            if m.raw is not None:  # verbatim replay keeps the encrypted reasoning attached to its turn
                items.extend(m.raw)
                continue
            text = "".join(b.text for b in m.content if isinstance(b, TextBlock))
            if text:
                items.append({"role": "assistant", "content": text})
            items += [
                {"type": "function_call", "call_id": b.id, "name": b.name, "arguments": json.dumps(b.input)}
                for b in m.content
                if isinstance(b, ToolUseBlock)
            ]
            continue
        for b in m.content:
            if isinstance(b, ToolResultBlock):
                items.append({"type": "function_call_output", "call_id": b.tool_use_id, "output": b.content})
        text = "\n\n".join(b.text for b in m.content if isinstance(b, TextBlock) and b.text.strip())
        images = [b for b in m.content if isinstance(b, ImageBlock)]
        if images:
            parts: list[dict[str, Any]] = [{"type": "input_text", "text": text}] if text else []
            parts += [
                {
                    "type": "input_image",
                    "image_url": f"data:{i.media_type};base64,{i.data}",
                    "detail": i.detail,
                }
                for i in images
            ]
            items.append({"role": "user", "content": parts})
        elif text:
            items.append({"role": "user", "content": text})
    return items


class OpenAIResponsesProvider:
    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        reasoning_effort: str | None = None,
        client: openai.OpenAI | None = None,
    ):
        self.model = model
        self.reasoning_effort = reasoning_effort if reasoning_effort != "none" else None
        self.client = client or openai.OpenAI(api_key=api_key, max_retries=2)
        self._downgrade: dict[str, str] = {}
        self._no_reasoning = False  # learned: the model is not a reasoning model

    def complete(
        self, *, system: str, messages: list[Message], tools: list[ToolSpec], max_tokens: int, timeout: float,
        thinking: str | None = None,
    ) -> ModelResponse:  # fmt: skip
        params: dict[str, Any] = {
            "model": self.model, "instructions": system, "input": _to_input(messages),
            "max_output_tokens": max_tokens, "store": False,
        }  # fmt: skip
        if tools:
            params["tools"] = [
                {"type": "function", "name": t.name, "description": t.description, "parameters": t.input_schema, "strict": False}
                for t in tools
            ]  # fmt: skip
        requested = thinking
        for _ in range(3):
            info: dict[str, Any] | None = None
            effort: str | None = None
            if self._no_reasoning:
                info = (
                    {
                        "requested": requested,
                        "applied": "none",
                        "reason": f"{self.model} is not a reasoning model",
                    }
                    if requested
                    else None
                )
            elif self.reasoning_effort and not requested:
                effort = self.reasoning_effort
            elif requested:
                effort = self._downgrade.get(EFFORT[requested], EFFORT[requested])
                info = {"requested": requested, "applied": effort}
                if effort != EFFORT[requested]:
                    info["reason"] = f"{self.model} does not accept effort {EFFORT[requested]}"
            if effort:
                params["reasoning"] = {"effort": effort}
                params["include"] = ["reasoning.encrypted_content"]
            else:
                params.pop("reasoning", None)
                params.pop("include", None)
            try:
                resp = self.client.with_options(timeout=timeout).responses.create(**params)
                break
            except openai.BadRequestError as e:
                m = e.message.lower()
                if (
                    effort
                    and effort in _DOWNGRADE
                    and effort not in self._downgrade
                    and _unsupported_effort(e)
                ):
                    self._downgrade[effort] = _DOWNGRADE[effort]
                    continue
                if (
                    effort
                    and not self._no_reasoning
                    and "reasoning" in m
                    and ("not supported" in m or "unsupported" in m)
                ):
                    self._no_reasoning = True
                    continue
                raise _map_error(e, self.model) from e
            except openai.OpenAIError as e:
                raise _map_error(e, self.model) from e
        else:  # pragma: no cover
            raise ProviderError("could not build an accepted request", kind="bad_request")

        raw: list[dict[str, Any]] = []
        text_parts: list[str] = []
        calls: list[ToolUseBlock] = []
        for item in resp.output or []:
            raw.append(_item(item))
            kind = getattr(item, "type", "")
            if kind == "message":
                for part in getattr(item, "content", []) or []:
                    if getattr(part, "type", "") == "refusal":
                        raise ProviderError("Model declined the request (refusal)", kind="refusal")
                    if getattr(part, "type", "") == "output_text":
                        text_parts.append(part.text)
            elif kind == "function_call":
                calls.append(ToolUseBlock(item.call_id, item.name, _parse_arguments(item.arguments)))
        text = "".join(text_parts)
        incomplete = getattr(getattr(resp, "incomplete_details", None), "reason", None)
        if getattr(resp, "status", "") == "incomplete" and incomplete == "content_filter":
            raise ProviderError("Model declined the request (content filter)", kind="refusal")
        stop = "max_tokens" if incomplete == "max_output_tokens" else ("tool_use" if calls else "end_turn")
        u = resp.usage
        details = getattr(u, "output_tokens_details", None)
        usage = Usage(
            getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0,
            getattr(details, "reasoning_tokens", 0) or 0,
        )  # fmt: skip
        return ModelResponse(
            text=text, tool_calls=calls, stop_reason=stop, usage=usage,
            message=Message("assistant", [TextBlock(text), *calls], raw=raw), thinking=info,
        )  # fmt: skip
