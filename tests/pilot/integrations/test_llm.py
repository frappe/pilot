"""Tests for pilot.integrations.llm - generic litellm-backed integration."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pilot.integrations.llm import base, read_system_prompt, registry
from pilot.integrations.llm.base import LLMAuthError, LLMError, LLMIntegration
from pilot.integrations.llm.vllm import VLLMIntegration


class _FakeAuthError(Exception):
    pass


class _FakeAPIError(Exception):
    pass


class _FakeNotFoundError(Exception):
    pass


class _FakeRateLimitError(Exception):
    pass


class _FakeAPIConnectionError(Exception):
    pass


class _FakeTimeout(Exception):
    pass


def _response(text: str | None):
    message = SimpleNamespace(content=text)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


_MODELS_BY_PROVIDER = {
    "openai": {"gpt-4o", "gpt-4o-mini"},
    "anthropic": {"claude-opus-4-8"},
}


@pytest.fixture
def fake_litellm(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    stub = SimpleNamespace(
        completion=MagicMock(return_value=_response("hi")),
        models_by_provider=_MODELS_BY_PROVIDER,
        AuthenticationError=_FakeAuthError,
        APIError=_FakeAPIError,
        NotFoundError=_FakeNotFoundError,
        RateLimitError=_FakeRateLimitError,
        APIConnectionError=_FakeAPIConnectionError,
        Timeout=_FakeTimeout,
    )
    monkeypatch.setattr(base, "litellm", stub)
    monkeypatch.setattr(registry, "litellm", stub)
    return stub


def _integration(provider="anthropic", model="claude-opus-4-8", **kwargs):
    return LLMIntegration("sk-key", provider=provider, model=model, **kwargs)


def test_prompt_routes_provider_model_and_key(fake_litellm) -> None:
    _integration(provider="anthropic", model="claude-opus-4-8").prompt(
        "hello", bench_root=Path("/tmp/bench")
    )
    kwargs = fake_litellm.completion.call_args.kwargs
    assert kwargs["model"] == "anthropic/claude-opus-4-8"
    assert kwargs["api_key"] == "sk-key"
    assert kwargs["messages"] == [
        {"role": "system", "content": read_system_prompt(Path("/tmp/bench"))},
        {"role": "user", "content": "hello"},
    ]


def test_prompt_forwards_api_base(fake_litellm) -> None:
    _integration(api_base="http://vllm:8000/v1").prompt("hi", bench_root=Path("/tmp/bench"))
    assert fake_litellm.completion.call_args.kwargs["api_base"] == "http://vllm:8000/v1"


def test_get_response_text(fake_litellm) -> None:
    assert _integration().get_response_text(_response("Hello world")) == "Hello world"
    assert _integration().get_response_text(SimpleNamespace(choices=[])) == ""
    assert _integration().get_response_text(_response(None)) == ""


def test_auth_error_maps(fake_litellm) -> None:
    fake_litellm.completion.side_effect = _FakeAuthError("bad key")
    with pytest.raises(LLMAuthError):
        _integration().prompt("hi", bench_root=Path("/tmp/bench"))


def test_not_found_error_maps_to_llm_error(fake_litellm) -> None:
    fake_litellm.completion.side_effect = _FakeNotFoundError("<html>404</html>")
    with pytest.raises(LLMError, match="not found"):
        _integration().prompt("hi", bench_root=Path("/tmp/bench"))


def test_api_error_maps_to_llm_error(fake_litellm) -> None:
    fake_litellm.completion.side_effect = _FakeAPIError("boom")
    with pytest.raises(LLMError):
        _integration().prompt("hi", bench_root=Path("/tmp/bench"))


# -- registry ---------------------------------------------------------------


def test_provider_options_include_self_hosted(fake_litellm) -> None:
    options = {o["value"]: o for o in registry.provider_options()}
    assert "openai" in options and "anthropic" in options
    assert options["self-hosted"]["self_hosted"] is True
    assert options["openai"]["self_hosted"] is False


def test_models_for_provider(fake_litellm) -> None:
    assert registry.models_for("openai") == ["gpt-4o", "gpt-4o-mini"]
    assert registry.models_for("self-hosted") == []  # self-hosted: from the server


def test_is_configured_requires_provider_key_and_model() -> None:
    assert registry.is_configured(SimpleNamespace(provider="openai", api_key="k", model="gpt-4o"))
    assert not registry.is_configured(SimpleNamespace(provider="openai", api_key="k", model=""))
    assert not registry.is_configured(SimpleNamespace(provider="", api_key="k", model="gpt-4o"))


def test_build_integration_picks_class(fake_litellm) -> None:
    hosted = registry.build_integration(
        SimpleNamespace(provider="self-hosted", api_key="k", model="m", api_base="http://h/v1")
    )
    assert isinstance(hosted, VLLMIntegration)
    assert hosted.provider == "hosted_vllm"

    generic = registry.build_integration(
        SimpleNamespace(provider="openai", api_key="k", model="gpt-4o", api_base="")
    )
    assert type(generic) is LLMIntegration
    assert generic.provider == "openai" and generic.model == "gpt-4o"
