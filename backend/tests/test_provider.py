"""Anthropic adapter tests: the real SDK talks to an in-process mock HTTP transport (no network, no API key).

These prove request/response handling and error mapping. They say nothing about model quality.
"""

from __future__ import annotations

import json

import anthropic
import httpx2 as httpx
import pytest

from harnix.models.anthropic_provider import AnthropicProvider
from harnix.models.base import Message, ProviderError, TextBlock, ToolResultBlock, ToolSpec, ToolUseBlock

TOOLS = [ToolSpec("read_file", "read", {"type": "object", "properties": {"path": {"type": "string"}}})]


def message_json(content, stop="end_turn", usage=None):
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-5",
        "content": content,
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": usage or {"input_tokens": 10, "output_tokens": 5},
    }


def provider(handler) -> tuple[AnthropicProvider, list[dict]]:
    seen: list[dict] = []

    def wrapped(req: httpx.Request) -> httpx.Response:
        seen.append({"body": json.loads(req.content or b"{}"), "headers": dict(req.headers)})
        return handler(req)

    client = anthropic.Anthropic(
        api_key="test-key", http_client=httpx.Client(transport=httpx.MockTransport(wrapped)), max_retries=0
    )
    return AnthropicProvider("test-key", "claude-sonnet-5-5", client=client), seen


def complete(p, messages=None):
    return p.complete(
        system="sys",
        messages=messages or [Message("user", [TextBlock("hi")])],
        tools=TOOLS,
        max_tokens=100,
        timeout=5,
    )


def test_request_shape_and_text_response():
    p, seen = provider(lambda r: httpx.Response(200, json=message_json([{"type": "text", "text": "hello"}])))
    resp = complete(p)
    body = seen[0]["body"]
    assert body["model"] == "claude-sonnet-5-5" and body["system"] == "sys" and body["max_tokens"] == 100
    assert body["tools"][0]["name"] == "read_file" and "input_schema" in body["tools"][0]
    assert "temperature" not in body and "tool_choice" not in body  # removed/unsupported on current models
    assert body["messages"] == [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    assert seen[0]["headers"]["x-api-key"] == "test-key"
    assert resp.text == "hello" and resp.tool_calls == [] and resp.stop_reason == "end_turn"
    assert (resp.usage.input_tokens, resp.usage.output_tokens) == (10, 5)


def test_tool_use_response_and_tool_result_roundtrip():
    content = [
        {"type": "text", "text": "reading"},
        {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {"path": "a.py"}},
    ]
    p, seen = provider(lambda r: httpx.Response(200, json=message_json(content, "tool_use")))
    resp = complete(p)
    assert [(c.id, c.name, c.input) for c in resp.tool_calls] == [("toolu_1", "read_file", {"path": "a.py"})]
    history = [
        Message("user", [TextBlock("go")]),
        resp.message,
        Message("user", [ToolResultBlock("toolu_1", '{"ok": true}', is_error=False)]),
    ]
    complete(p, history)
    sent = seen[1]["body"]["messages"]
    assert sent[1]["role"] == "assistant" and [b["type"] for b in sent[1]["content"]] == ["text", "tool_use"]
    assert sent[2]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "content": '{"ok": true}',
        "is_error": False,
    }


def test_thinking_blocks_replayed_verbatim():
    content = [
        {"type": "thinking", "thinking": "", "signature": "sig-abc"},
        {"type": "tool_use", "id": "toolu_2", "name": "read_file", "input": {"path": "b.py"}},
    ]
    p, seen = provider(lambda r: httpx.Response(200, json=message_json(content, "tool_use")))
    resp = complete(p)
    complete(p, [Message("user", [TextBlock("go")]), resp.message])
    replay = seen[1]["body"]["messages"][1]["content"]
    assert replay[0]["type"] == "thinking" and replay[0]["signature"] == "sig-abc"  # opaque state preserved


def test_empty_text_blocks_are_not_sent():
    p, seen = provider(lambda r: httpx.Response(200, json=message_json([{"type": "text", "text": "x"}])))
    complete(
        p,
        [
            Message("user", [TextBlock("go")]),
            Message("assistant", [TextBlock("  ")]),
            Message("user", [TextBlock("again")]),
        ],
    )
    assert [
        b for m in seen[0]["body"]["messages"] for b in m["content"] if b.get("text", "x").strip() == ""
    ] == []


def test_cache_tokens_counted_in_usage():
    usage = {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_input_tokens": 100,
        "cache_creation_input_tokens": 20,
    }
    p, _ = provider(
        lambda r: httpx.Response(200, json=message_json([{"type": "text", "text": "x"}], usage=usage))
    )
    assert complete(p).usage.input_tokens == 130


@pytest.mark.parametrize(
    "status,retryable,kind",
    [(429, True, "rate_limit"), (500, True, "api_error"), (503, True, "api_error"), (529, True, "api_error"),
     (401, False, "auth"), (403, False, "permission"), (404, False, "not_found"), (400, False, "bad_request")],
)  # fmt: skip
def test_http_errors_map_to_provider_errors(status, retryable, kind):
    err = {"type": "error", "error": {"type": "api_error", "message": "boom"}}
    p, _ = provider(lambda r: httpx.Response(status, json=err))
    with pytest.raises(ProviderError) as e:
        complete(p)
    assert e.value.retryable is retryable and e.value.kind == kind


def test_network_failure_is_retryable():
    def boom(req):
        raise httpx.ConnectError("no route", request=req)

    p, _ = provider(boom)
    with pytest.raises(ProviderError) as e:
        complete(p)
    assert e.value.retryable and e.value.kind == "network"


def test_timeout_is_retryable():
    def slow(req):
        raise httpx.ReadTimeout("slow", request=req)

    p, _ = provider(slow)
    with pytest.raises(ProviderError) as e:
        complete(p)
    assert e.value.retryable and e.value.kind == "timeout"


def test_refusal_is_a_distinct_failure_not_a_final_answer():
    body = message_json([], "refusal")
    body["stop_details"] = {"type": "refusal", "category": "cyber", "explanation": "no"}
    p, _ = provider(lambda r: httpx.Response(200, json=body))
    with pytest.raises(ProviderError, match="refusal") as e:
        complete(p)
    assert not e.value.retryable and e.value.kind == "refusal"


def test_auth_error_message_never_contains_the_key():
    p, _ = provider(
        lambda r: httpx.Response(
            401,
            json={"type": "error", "error": {"type": "authentication_error", "message": "bad key test-key"}},
        )
    )
    with pytest.raises(ProviderError) as e:
        complete(p)
    assert "test-key" not in str(e.value)


def test_blocks_roundtrip_types():
    assert ToolUseBlock("i", "n", {}).name == "n"
