"""OpenAI adapter and provider configuration. The real SDK talks to an in-process mock HTTP transport
(no network, no key): this proves request shape, parsing and error mapping, not model behaviour."""

from __future__ import annotations

import json

import httpx2 as httpx
import openai
import pytest

from harnix.config import ENV_FILES, Settings
from harnix.models.base import (
    INVALID_JSON_KEY,
    Message,
    ProviderError,
    TextBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
)
from harnix.models.factory import provider_factory
from harnix.models.openai_provider import OpenAIProvider

TOOLS = [ToolSpec("read_file", "read", {"type": "object", "properties": {"path": {"type": "string"}}})]


def completion(message=None, finish="stop", usage=None):
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-5.4-nano",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "hello", **(message or {})},
                "finish_reason": finish,
            }
        ],
        "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def tool_call(id_="call_1", name="read_file", args='{"path": "a.py"}'):
    return {"id": id_, "type": "function", "function": {"name": name, "arguments": args}}


def provider(handler, **kw) -> tuple[OpenAIProvider, list[dict]]:
    seen: list[dict] = []

    def wrapped(req: httpx.Request) -> httpx.Response:
        seen.append(
            {"body": json.loads(req.content or b"{}"), "headers": dict(req.headers), "url": str(req.url)}
        )
        return handler(req)

    client = openai.OpenAI(
        api_key="test-key", http_client=httpx.Client(transport=httpx.MockTransport(wrapped)), max_retries=0
    )
    return OpenAIProvider("test-key", "gpt-5.4-nano", client=client, **kw), seen


def complete(p, messages=None):
    return p.complete(
        system="sys",
        messages=messages or [Message("user", [TextBlock("hi")])],
        tools=TOOLS,
        max_tokens=100,
        timeout=5,
    )


def err(status, code=None, type_="api_error"):
    return httpx.Response(
        status, json={"error": {"message": "boom", "type": type_, "param": None, "code": code}}
    )


# ---- request / response ---------------------------------------------------------------------


def test_request_shape_and_text_response():
    p, seen = provider(lambda r: httpx.Response(200, json=completion()))
    resp = complete(p)
    body = seen[0]["body"]
    assert (
        seen[0]["url"].endswith("/chat/completions")
        and seen[0]["headers"]["authorization"] == "Bearer test-key"
    )
    assert body["model"] == "gpt-5.4-nano" and body["max_completion_tokens"] == 100
    assert "max_tokens" not in body and "temperature" not in body and "reasoning_effort" not in body
    assert body["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    assert body["tools"] == [
        {
            "type": "function",
            "function": {"name": "read_file", "description": "read", "parameters": TOOLS[0].input_schema},
        }
    ]
    assert resp.text == "hello" and resp.tool_calls == [] and resp.stop_reason == "end_turn"
    assert (resp.usage.input_tokens, resp.usage.output_tokens) == (10, 5)


def test_reasoning_effort_only_when_configured():
    p, seen = provider(lambda r: httpx.Response(200, json=completion()), reasoning_effort="low")
    complete(p)
    assert seen[0]["body"]["reasoning_effort"] == "low"


def test_tool_calls_roundtrip_message_mapping():
    p, seen = provider(
        lambda r: httpx.Response(
            200,
            json=completion(
                {"content": None, "tool_calls": [tool_call(), tool_call("call_2", args='{"path": "b.py"}')]},
                "tool_calls",
            ),
        )
    )
    resp = complete(p)
    assert resp.stop_reason == "tool_use"
    assert [(c.id, c.name, c.input) for c in resp.tool_calls] == [
        ("call_1", "read_file", {"path": "a.py"}),
        ("call_2", "read_file", {"path": "b.py"}),
    ]
    history = [
        Message("user", [TextBlock("go")]),
        resp.message,
        Message(
            "user",
            [
                ToolResultBlock("call_1", '{"ok": true}'),
                ToolResultBlock("call_2", '{"ok": false}', is_error=True),
            ],
        ),
        Message("user", [TextBlock("Harness notice: try something else")]),
    ]
    complete(p, history)
    sent = seen[1]["body"]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "tool", "tool", "user"]
    assert sent[2]["content"] is None
    assert [(t["id"], json.loads(t["function"]["arguments"])) for t in sent[2]["tool_calls"]] == [
        ("call_1", {"path": "a.py"}),
        ("call_2", {"path": "b.py"}),
    ]
    assert [(m["tool_call_id"], m["content"]) for m in sent[3:5]] == [
        ("call_1", '{"ok": true}'),
        ("call_2", '{"ok": false}'),
    ]
    assert sent[5] == {"role": "user", "content": "Harness notice: try something else"}


def test_tool_results_and_text_in_one_turn_keep_tool_messages_first():
    p, seen = provider(lambda r: httpx.Response(200, json=completion()))
    asst = Message("assistant", [TextBlock(""), ToolUseBlock("c1", "read_file", {"path": "x"})])
    complete(
        p,
        [
            Message("user", [TextBlock("go")]),
            asst,
            Message("user", [TextBlock("extra"), ToolResultBlock("c1", "r")]),
        ],
    )
    roles = [m["role"] for m in seen[0]["body"]["messages"]]
    assert roles == [
        "system",
        "user",
        "assistant",
        "tool",
        "user",
    ]  # tool message directly follows tool_calls


def test_finish_reason_length_maps_to_max_tokens():
    p, _ = provider(lambda r: httpx.Response(200, json=completion({"content": "cut"}, "length")))
    assert complete(p).stop_reason == "max_tokens"


@pytest.mark.parametrize("args", ["{not json", "[1, 2]", ""])
def test_invalid_tool_arguments_are_marked_not_crashed(args):
    p, _ = provider(
        lambda r: httpx.Response(200, json=completion({"tool_calls": [tool_call(args=args)]}, "tool_calls"))
    )
    resp = complete(p)
    if args == "":  # an empty argument string is a valid empty object
        assert resp.tool_calls[0].input == {}
    else:
        assert INVALID_JSON_KEY in resp.tool_calls[0].input


def test_refusal_is_a_distinct_failure():
    p, _ = provider(
        lambda r: httpx.Response(200, json=completion({"content": None, "refusal": "I can't help with that"}))
    )
    with pytest.raises(ProviderError) as e:
        complete(p)
    assert e.value.kind == "refusal" and not e.value.retryable


# ---- errors ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,code,retryable,kind",
    [
        (429, "rate_limit_exceeded", True, "rate_limit"),
        (429, "insufficient_quota", False, "quota"),  # budget exhausted: retrying is pointless
        (500, None, True, "api_error"),
        (503, None, True, "api_error"),
        (401, "invalid_api_key", False, "auth"),
        (403, None, False, "permission"),
        (404, "model_not_found", False, "not_found"),
        (400, None, False, "bad_request"),
    ],
)
def test_http_errors_map_to_provider_errors(status, code, retryable, kind):
    p, _ = provider(lambda r: err(status, code))
    with pytest.raises(ProviderError) as e:
        complete(p)
    assert e.value.retryable is retryable and e.value.kind == kind


def test_reasoning_vs_function_tools_conflict_gets_an_actionable_message():
    msg = (
        "Function tools with reasoning_effort are not supported for gpt-6-luna in /v1/chat/completions. "
        "To use function tools, use /v1/responses or set reasoning_effort to 'none'."
    )
    p, _ = provider(
        lambda r: httpx.Response(
            400,
            json={"error": {"message": msg, "type": "invalid_request_error", "param": None, "code": None}},
        )
    )
    with pytest.raises(ProviderError) as e:
        complete(p)
    assert e.value.kind == "config" and not e.value.retryable
    assert "FORGE_REASONING_EFFORT=none" in str(e.value)
    # an unrelated 400 keeps its own message
    p2, _ = provider(lambda r: err(400))
    with pytest.raises(ProviderError) as e2:
        complete(p2)
    assert e2.value.kind == "bad_request"


def test_network_and_timeout_errors_are_retryable():
    def boom(req):
        raise httpx.ConnectError("no route", request=req)

    def slow(req):
        raise httpx.ReadTimeout("slow", request=req)

    for handler, kind in ((boom, "network"), (slow, "timeout")):
        p, _ = provider(handler)
        with pytest.raises(ProviderError) as e:
            complete(p)
        assert e.value.retryable and e.value.kind == kind


def test_auth_error_never_echoes_the_key():
    p, _ = provider(lambda r: err(401, "invalid_api_key"))
    with pytest.raises(ProviderError) as e:
        complete(p)
    assert "test-key" not in str(e.value)


# ---- configuration --------------------------------------------------------------------------


def cfg(**kw) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def test_default_model_depends_on_provider():
    assert cfg(provider="openai").model_name == "gpt-5.4-nano"
    assert cfg(provider="anthropic").model_name == "claude-sonnet-5-5"
    assert cfg(provider="openai", model="gpt-5.4-mini").model_name == "gpt-5.4-mini"


def test_openai_key_required_and_placeholders_count_as_unset():
    assert "OPENAI_API_KEY is not set" in (cfg(provider="openai").provider_problem() or "")
    assert "OPENAI_API_KEY" in (
        cfg(provider="openai", openai_api_key="your-openai-api-key-here").provider_problem() or ""
    )
    assert cfg(provider="openai", openai_api_key="sk-real").provider_problem() is None
    assert "ANTHROPIC_API_KEY" in (
        cfg(provider="anthropic", anthropic_api_key="your-anthropic-api-key-here").provider_problem() or ""
    )


def test_provider_model_mismatch_is_reported_with_the_fix():
    p = cfg(provider="openai", openai_api_key="sk-real", model="claude-sonnet-5-5").provider_problem()
    assert p and "Anthropic model" in p and "gpt-5.4-nano" in p
    p = cfg(provider="anthropic", anthropic_api_key="sk-ant-real", model="gpt-5.4-nano").provider_problem()
    assert p and "OpenAI model" in p


def test_key_loaded_from_env_file_by_alias_and_secrets_registered(tmp_path):
    f = tmp_path / ".env"
    f.write_text(
        "OPENAI_API_KEY=sk-from-file-123456\nFORGE_PROVIDER=openai\nFORGE_MODEL=gpt-5.4-mini\nUNRELATED=1\n"
    )
    s = Settings(_env_file=str(f))  # type: ignore[call-arg]
    assert (
        s.provider == "openai"
        and s.model_name == "gpt-5.4-mini"
        and s.openai_api_key == "sk-from-file-123456"
    )
    assert "sk-from-file-123456" in s.secret_values()  # so the redactor scrubs it everywhere


def test_env_is_discovered_in_repo_root_regardless_of_cwd():
    from pathlib import Path

    root_env = Path(__file__).resolve().parents[2] / ".env"
    assert str(root_env) in ENV_FILES


def test_factory_builds_the_right_adapter():
    p = provider_factory(cfg(provider="openai", openai_api_key="sk-real", reasoning_effort="low"))()
    assert isinstance(p, OpenAIProvider) and p.model == "gpt-5.4-nano" and p.reasoning_effort == "low"
    with pytest.raises(Exception, match="OPENAI_API_KEY"):
        provider_factory(cfg(provider="openai"))()
