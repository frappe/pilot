"""Tests for pilot.integrations.llm - litellm-backed chat completion providers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pilot.integrations.llm import base
from pilot.integrations.llm.anthropic import AnthropicIntegration
from pilot.integrations.llm.base import LLMAuthError, LLMError
from pilot.integrations.llm.openai import OpenAIIntegration


class _FakeAuthError(Exception):
    pass


class _FakeAPIError(Exception):
    pass


def _response(text: str | None):
    message = SimpleNamespace(content=text)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


@pytest.fixture
def fake_litellm(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    stub = SimpleNamespace(
        completion=MagicMock(return_value=_response("hi")),
        models_by_provider={"anthropic": {"claude-opus-4-8"}, "openai": {"gpt-4o", "gpt-4o-mini"}},
        AuthenticationError=_FakeAuthError,
        APIError=_FakeAPIError,
    )
    monkeypatch.setattr(base, "litellm", stub)
    return stub


def test_missing_litellm_raises_at_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base, "litellm", None)
    with pytest.raises(RuntimeError, match="litellm` is not installed"):
        AnthropicIntegration("sk-key")


def test_get_models_lists_provider_catalog(fake_litellm) -> None:
    assert OpenAIIntegration("sk-key").get_models() == ["gpt-4o", "gpt-4o-mini"]
    assert AnthropicIntegration("sk-key").get_models() == ["claude-opus-4-8"]


def test_prompt_routes_model_and_key(fake_litellm) -> None:
    AnthropicIntegration("sk-key").prompt("hello", system_prompt="be terse")

    kwargs = fake_litellm.completion.call_args.kwargs
    assert kwargs["model"] == "anthropic/claude-opus-4-8"
    assert kwargs["api_key"] == "sk-key"
    assert kwargs["max_tokens"] == 4096
    assert kwargs["messages"] == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "hello"},
    ]


def test_prompt_omits_system_when_absent(fake_litellm) -> None:
    OpenAIIntegration("sk-key").prompt("hi", model="gpt-4o-mini")

    kwargs = fake_litellm.completion.call_args.kwargs
    assert kwargs["model"] == "openai/gpt-4o-mini"
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]


def test_prompt_forwards_extra_kwargs(fake_litellm) -> None:
    OpenAIIntegration("sk-key").prompt("hi", temperature=0.2)
    assert fake_litellm.completion.call_args.kwargs["temperature"] == 0.2


def test_get_response_text_reads_first_choice(fake_litellm) -> None:
    text = OpenAIIntegration("sk-key").get_response_text(_response("Hello world"))
    assert text == "Hello world"


def test_get_response_text_handles_empty(fake_litellm) -> None:
    integration = OpenAIIntegration("sk-key")
    assert integration.get_response_text(SimpleNamespace(choices=[])) == ""
    assert integration.get_response_text(_response(None)) == ""


def test_auth_error_maps_to_llm_auth_error(fake_litellm) -> None:
    fake_litellm.completion.side_effect = _FakeAuthError("bad key")
    with pytest.raises(LLMAuthError):
        AnthropicIntegration("sk-key").prompt("hi")


def test_api_error_maps_to_llm_error(fake_litellm) -> None:
    fake_litellm.completion.side_effect = _FakeAPIError("boom")
    with pytest.raises(LLMError):
        AnthropicIntegration("sk-key").prompt("hi")
