"""Tests for pilot.integrations.llm - class-based provider integrations."""

from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pilot.integrations.llm import base, frappe_llm, read_system_prompt, registry
from pilot.integrations.llm.base import LLMAuthError, LLMError
from pilot.integrations.llm.frappe_llm import FrappeLLMIntegration
from pilot.integrations.llm.lite import LiteLLMIntegration
from pilot.integrations.llm.self_hosted import SelfHostedIntegration

_FRAPPE_BASE = "http://frappe-llm.example/v1"
_BENCH = Path("/tmp/bench")


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
    # base and lite import litellm inside their functions, so patch the module itself.
    monkeypatch.setitem(sys.modules, "litellm", stub)
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


def test_frappe_llm_calls_the_configured_endpoint(fake_litellm) -> None:
    """Frappe LLM is served per bench, so it routes to the configured api_base."""
    integration = FrappeLLMIntegration(
        "sk-key", provider="frappe-llm", model="qwen3.6-27b-fp8", api_base=_FRAPPE_BASE
    )
    integration.prompt("hi", bench_root=Path("/tmp/bench"))
    kwargs = fake_litellm.completion.call_args.kwargs
    assert kwargs["model"] == "hosted_vllm/qwen3.6-27b-fp8"
    assert kwargs["api_base"] == _FRAPPE_BASE


# -- responses / errors -----------------------------------------------------


def _lite():
    return LiteLLMIntegration("sk-key", provider="openai", model="gpt-4o")


def test_answer_text_handles_an_empty_response(fake_litellm) -> None:
    assert _lite()._answer_text(_response("Hello world"), "key") == "Hello world"
    assert _lite()._answer_text(SimpleNamespace(choices=[]), "key") == ""
    assert _lite()._answer_text(_response(None), "key") == ""


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
    assert "openai" in options and "anthropic" in options
    assert options["openai"]["requires_api_base"] is False
    assert options["openai"]["free_text_model"] is False
    # the two special integrations
    assert options["frappe-llm"]["requires_api_base"] is True
    assert options["frappe-llm"]["free_text_model"] is False
    assert options["frappe-llm"]["models_need_api_key"] is True
    assert options["openai"]["models_need_api_key"] is False
    assert options["self-hosted"]["requires_api_base"] is True
    assert options["self-hosted"]["free_text_model"] is True


def test_provider_options_need_no_litellm() -> None:
    # No fake_litellm: listing providers must not reach for the slow import.
    assert "gemini" in {o["value"] for o in registry.provider_options()}


def test_models_for(fake_litellm) -> None:
    assert registry.models_for("openai") == ["gpt-4o", "gpt-4o-mini"]
    assert registry.models_for("self-hosted") == []


def test_models_need_api_key(fake_litellm) -> None:
    assert registry.models_need_api_key("frappe-llm") is True
    assert registry.models_need_api_key("openai") is False


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
        SimpleNamespace(
            provider="frappe-llm", api_key="k", model="qwen3.6-27b-fp8", api_base=_FRAPPE_BASE
        )
    )
    assert isinstance(frappe, FrappeLLMIntegration)
    assert frappe.api_base == _FRAPPE_BASE


def test_unknown_provider_raises(fake_litellm) -> None:
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        registry.models_for("nope")


# -- frappe llm model listing ------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, result):
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(request)
        if isinstance(result, Exception):
            raise result
        return _FakeResponse(result)

    monkeypatch.setattr(frappe_llm.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_frappe_models_need_a_key() -> None:
    with pytest.raises(ValueError, match="needs an API key"):
        FrappeLLMIntegration.get_models("frappe-llm", "", _FRAPPE_BASE)


def test_frappe_models_need_an_endpoint() -> None:
    """There is no default server to fall back to, so say that rather than
    failing later with a connection error against a placeholder host."""
    with pytest.raises(ValueError, match="needs an API base URL"):
        FrappeLLMIntegration.get_models("frappe-llm", "sk-key")


def test_frappe_models_sorted_and_authorized(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps({"data": [{"id": "qwen3.6-27b-fp8"}, {"id": "gpt-oss-120b"}, {}]}).encode()
    calls = _patch_urlopen(monkeypatch, payload)

    models = FrappeLLMIntegration.get_models("frappe-llm", "sk-key", _FRAPPE_BASE)

    assert models == ["gpt-oss-120b", "qwen3.6-27b-fp8"]
    assert calls[0].full_url == f"{_FRAPPE_BASE}/models"
    assert calls[0].get_header("Authorization") == "Bearer sk-key"


def test_frappe_models_reject_bad_key(monkeypatch: pytest.MonkeyPatch) -> None:
    error = urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)
    _patch_urlopen(monkeypatch, error)

    with pytest.raises(ValueError, match="rejected this API key"):
        FrappeLLMIntegration.get_models("frappe-llm", "sk-bad", _FRAPPE_BASE)


def test_frappe_models_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_urlopen(monkeypatch, urllib.error.URLError("no route"))

    with pytest.raises(ValueError, match=r"Could not reach .*/models: no route"):
        FrappeLLMIntegration.get_models("frappe-llm", "sk-key", _FRAPPE_BASE)


def test_frappe_models_empty_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_urlopen(monkeypatch, json.dumps({"data": []}).encode())

    with pytest.raises(ValueError, match="no models"):
        FrappeLLMIntegration.get_models("frappe-llm", "sk-key", _FRAPPE_BASE)


def test_registry_passes_key_through(monkeypatch: pytest.MonkeyPatch, fake_litellm) -> None:
    _patch_urlopen(monkeypatch, json.dumps({"data": [{"id": "qwen3.6-27b-fp8"}]}).encode())

    assert registry.models_for("frappe-llm", "sk-key", _FRAPPE_BASE) == ["qwen3.6-27b-fp8"]


# -- response caching --------------------------------------------------------


def _delta(text: str):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])


@pytest.fixture
def empty_response_cache():
    base.LLMIntegration._cached_responses.clear()
    yield base.LLMIntegration._cached_responses
    base.LLMIntegration._cached_responses.clear()


def _streaming_integration(fake_litellm, chunks: list[str]) -> base.LLMIntegration:
    fake_litellm.completion = MagicMock(return_value=iter([_delta(c) for c in chunks]))
    return base.LLMIntegration("k", provider="openai", model="gpt-4o", stream=True)


def test_streamed_answer_is_replayed_from_cache(fake_litellm, empty_response_cache) -> None:
    """A stream cannot be rewound, so the assembled text is cached and replayed."""
    first = _streaming_integration(fake_litellm, ["Hel", "lo ", "world"])
    assert "".join(first.prompt("why?", bench_root=_BENCH)) == "Hello world"

    # A fresh mock, so a call here would mean the provider was hit again.
    second = _streaming_integration(fake_litellm, ["should not be used"])

    assert "".join(second.prompt("why?", bench_root=_BENCH)) == "Hello world"
    assert not fake_litellm.completion.called


def test_a_different_prompt_is_not_served_from_cache(fake_litellm, empty_response_cache) -> None:
    first = _streaming_integration(fake_litellm, ["one"])
    list(first.prompt("first", bench_root=_BENCH))

    second = _streaming_integration(fake_litellm, ["two"])
    assert "".join(second.prompt("second", bench_root=_BENCH)) == "two"


def test_a_half_read_stream_is_not_cached(fake_litellm, empty_response_cache) -> None:
    """Closing the dialog mid-answer must not persist a truncated explanation."""
    integration = _streaming_integration(fake_litellm, ["partial ", "rest"])
    stream = integration.prompt("why?", bench_root=_BENCH)
    next(stream)
    stream.close()

    assert empty_response_cache == {}


def test_a_failed_stream_is_not_cached(fake_litellm, empty_response_cache) -> None:
    def dies_midway():
        yield _delta("partial ")
        raise RuntimeError("provider died")

    fake_litellm.completion = MagicMock(return_value=dies_midway())
    integration = base.LLMIntegration("k", provider="openai", model="gpt-4o", stream=True)

    with pytest.raises(RuntimeError):
        list(integration.prompt("why?", bench_root=_BENCH))

    assert empty_response_cache == {}


def test_non_streamed_answers_are_cached_too(fake_litellm, empty_response_cache) -> None:
    """Turning streaming off returns the text directly, and caches it the same way."""
    integration = base.LLMIntegration("k", provider="openai", model="gpt-4o")
    assert integration.prompt("why?", bench_root=_BENCH) == "hi"
    assert fake_litellm.completion.call_count == 1

    again = base.LLMIntegration("k", provider="openai", model="gpt-4o")

    assert again.prompt("why?", bench_root=_BENCH) == "hi"
    assert fake_litellm.completion.call_count == 1


def test_cache_does_not_grow_without_bound(fake_litellm, empty_response_cache) -> None:
    for index in range(base.LLMIntegration.cache_limit + 5):
        integration = _streaming_integration(fake_litellm, [f"answer {index}"])
        list(integration.prompt(f"q{index}", bench_root=_BENCH))

    assert len(empty_response_cache) == base.LLMIntegration.cache_limit


def test_refresh_replaces_the_cached_answer(fake_litellm, empty_response_cache) -> None:
    """The Regenerate button must reach the provider, and what it gets back becomes
    the new cached answer - otherwise the next open would show the stale one."""
    first = _streaming_integration(fake_litellm, ["first"])
    assert "".join(first.prompt("why?", bench_root=_BENCH)) == "first"

    regenerated = _streaming_integration(fake_litellm, ["second"])
    assert "".join(regenerated.prompt("why?", bench_root=_BENCH, refresh=True)) == "second"
    assert fake_litellm.completion.called

    reopened = _streaming_integration(fake_litellm, ["should not be used"])
    assert "".join(reopened.prompt("why?", bench_root=_BENCH)) == "second"
    assert not fake_litellm.completion.called
