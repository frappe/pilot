"""Tests for pilot.integrations.llm - class-based provider integrations."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pilot.integrations.llm import base, lite, read_system_prompt, registry
from pilot.integrations.llm.base import LLMAuthError, LLMError
from pilot.integrations.llm.frappe_llm import FrappeLLMIntegration
from pilot.integrations.llm.lite import LiteLLMIntegration
from pilot.integrations.llm.self_hosted import SelfHostedIntegration


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
    monkeypatch.setattr(lite, "litellm", stub)
    return stub


# -- routing ----------------------------------------------------------------


def test_litellm_routes_as_provider_slash_model(fake_litellm) -> None:
    LiteLLMIntegration("sk-key", provider="openai", model="gpt-4o").prompt(
        "hello", bench_root=Path("/tmp/bench")
    )
    kwargs = fake_litellm.completion.call_args.kwargs
    assert kwargs["model"] == "openai/gpt-4o"
    assert kwargs["api_key"] == "sk-key"
    assert kwargs["messages"] == [
        {"role": "system", "content": read_system_prompt(Path("/tmp/bench"))},
        {"role": "user", "content": "hello"},
    ]


def test_self_hosted_routes_through_hosted_vllm(fake_litellm) -> None:
    SelfHostedIntegration(
        "sk-key", provider="self-hosted", model="my-model", api_base="http://h:8000/v1"
    ).prompt("hi", bench_root=Path("/tmp/bench"))
    kwargs = fake_litellm.completion.call_args.kwargs
    assert kwargs["model"] == "hosted_vllm/my-model"
    assert kwargs["api_base"] == "http://h:8000/v1"


def test_frappe_llm_uses_fixed_base_api(fake_litellm) -> None:
    integration = FrappeLLMIntegration("sk-key", provider="frappe-llm", model="qwen3.6-27b-fp8")
    integration.prompt("hi", bench_root=Path("/tmp/bench"))
    kwargs = fake_litellm.completion.call_args.kwargs
    assert kwargs["model"] == "hosted_vllm/qwen3.6-27b-fp8"
    assert kwargs["api_base"] == "http://x.x.x.x/v1"


# -- responses / errors -----------------------------------------------------


def _lite():
    return LiteLLMIntegration("sk-key", provider="openai", model="gpt-4o")


def test_get_response_text(fake_litellm) -> None:
    assert _lite().get_response_text(_response("Hello world")) == "Hello world"
    assert _lite().get_response_text(SimpleNamespace(choices=[])) == ""
    assert _lite().get_response_text(_response(None)) == ""


def test_auth_error_maps(fake_litellm) -> None:
    fake_litellm.completion.side_effect = _FakeAuthError("bad key")
    with pytest.raises(LLMAuthError):
        _lite().prompt("hi", bench_root=Path("/tmp/bench"))


def test_not_found_error_maps(fake_litellm) -> None:
    fake_litellm.completion.side_effect = _FakeNotFoundError("<html>404</html>")
    with pytest.raises(LLMError, match="not found"):
        _lite().prompt("hi", bench_root=Path("/tmp/bench"))


def test_api_error_maps(fake_litellm) -> None:
    fake_litellm.completion.side_effect = _FakeAPIError("boom")
    with pytest.raises(LLMError):
        _lite().prompt("hi", bench_root=Path("/tmp/bench"))


# -- registry ---------------------------------------------------------------


def test_provider_options_aggregate_across_integrations(fake_litellm) -> None:
    options = {o["value"]: o for o in registry.provider_options()}
    # litellm providers present in the (fake) catalog
    assert "openai" in options and "anthropic" in options
    assert options["openai"]["requires_api_base"] is False
    assert options["openai"]["free_text_model"] is False
    # the two special integrations
    assert options["frappe-llm"]["requires_api_base"] is False
    assert options["frappe-llm"]["free_text_model"] is False
    assert options["self-hosted"]["requires_api_base"] is True
    assert options["self-hosted"]["free_text_model"] is True


def test_litellm_providers_filtered_to_catalogued(fake_litellm) -> None:
    # gemini is curated but absent from the fake catalog, so it's not offered.
    assert "gemini" not in {o["value"] for o in registry.provider_options()}


def test_models_for(fake_litellm) -> None:
    assert registry.models_for("openai") == ["gpt-4o", "gpt-4o-mini"]
    assert registry.models_for("frappe-llm") == ["qwen3.6-27b-fp8", "deepseek-v4-flash"]
    assert registry.models_for("self-hosted") == []


def test_requires_api_base(fake_litellm) -> None:
    assert registry.requires_api_base("self-hosted") is True
    assert registry.requires_api_base("openai") is False


def test_is_configured_requires_provider_key_and_model() -> None:
    assert registry.is_configured(SimpleNamespace(provider="openai", api_key="k", model="gpt-4o"))
    assert not registry.is_configured(SimpleNamespace(provider="openai", api_key="k", model=""))
    assert not registry.is_configured(SimpleNamespace(provider="", api_key="k", model="gpt-4o"))


def test_build_integration_picks_owning_class(fake_litellm) -> None:
    lite_i = registry.build_integration(
        SimpleNamespace(provider="openai", api_key="k", model="gpt-4o", api_base="")
    )
    assert type(lite_i) is LiteLLMIntegration
    assert lite_i.provider == "openai"

    hosted = registry.build_integration(
        SimpleNamespace(provider="self-hosted", api_key="k", model="m", api_base="http://h/v1")
    )
    assert isinstance(hosted, SelfHostedIntegration)

    frappe = registry.build_integration(
        SimpleNamespace(provider="frappe-llm", api_key="k", model="qwen3.6-27b-fp8", api_base="")
    )
    assert isinstance(frappe, FrappeLLMIntegration)
    assert frappe.api_base == "http://x.x.x.x/v1"


def test_unknown_provider_raises(fake_litellm) -> None:
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        registry.models_for("nope")
