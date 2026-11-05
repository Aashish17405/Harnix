"""Builds the configured provider. Raises ConfigError with actionable text when it cannot."""

from __future__ import annotations

from collections.abc import Callable

from harnix.config import ConfigError, Settings
from harnix.models.base import ModelProvider


def provider_factory(settings: Settings) -> Callable[[], ModelProvider]:
    problem = settings.provider_problem()
    if settings.provider == "scripted":
        from harnix.models.scripted import ScriptedProvider

        path = settings.scripted_file
        return lambda: ScriptedProvider.from_file(path or "")  # fresh script position per task
    if problem:
        # still return a factory so the API can start and show configuration guidance; start() preflights first
        def _unconfigured() -> ModelProvider:
            raise ConfigError(problem)

        return _unconfigured
    model = settings.model_name
    if settings.provider == "openai":
        from harnix.models.openai_provider import OpenAIProvider, OpenAIResponsesProvider

        key, effort = settings.openai_api_key or "", settings.reasoning_effort
        cls = OpenAIResponsesProvider if settings.openai_api == "responses" else OpenAIProvider
        return lambda: cls(key, model, reasoning_effort=effort)
    from harnix.models.anthropic_provider import AnthropicProvider

    akey = settings.anthropic_api_key or ""
    return lambda: AnthropicProvider(akey, model)
