"""Tests for pilot.integrations.llm - OpenAI Chat Completions over raw HTTP."""

from __future__ import annotations

import io
import json
import urllib.error
from contextlib import contextmanager

import pytest

from pilot.integrations.llm.base import LLMAuthError, LLMError
from pilot.integrations.llm.openai import DEFAULT_MODEL, OpenAIIntegration


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@contextmanager
def _capture(monkeypatch: pytest.MonkeyPatch, payload: dict):
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode()) if request.data else None
        return _FakeResponse(json.dumps(payload).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    yield captured


def test_authentication_headers_use_bearer_key() -> None:
    headers = OpenAIIntegration("sk-key").authentication_headers()
    assert headers["Authorization"] == "Bearer sk-key"


def test_get_models_returns_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    with _capture(monkeypatch, {"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]}):
        models = OpenAIIntegration("sk-key").get_models()
    assert models == ["gpt-4o", "gpt-4o-mini"]


def test_prompt_prepends_system_message(monkeypatch: pytest.MonkeyPatch) -> None:
    with _capture(monkeypatch, {"choices": []}) as captured:
        OpenAIIntegration("sk-key").prompt("hello", system_prompt="be terse")

    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["method"] == "POST"
    body = captured["body"]
    assert body["model"] == DEFAULT_MODEL
    assert body["max_tokens"] == 4096
    assert body["messages"] == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "hello"},
    ]


def test_prompt_omits_system_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    with _capture(monkeypatch, {"choices": []}) as captured:
        OpenAIIntegration("sk-key").prompt("hi")
    assert captured["body"]["messages"] == [{"role": "user", "content": "hi"}]


def test_get_response_text_reads_first_choice() -> None:
    response = {"choices": [{"message": {"role": "assistant", "content": "Hello world"}}]}
    assert OpenAIIntegration("sk-key").get_response_text(response) == "Hello world"


def test_get_response_text_empty_choices() -> None:
    assert OpenAIIntegration("sk-key").get_response_text({"choices": []}) == ""


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("url", code, "err", {}, io.BytesIO(b'{"error": "x"}'))


def test_auth_error_maps_to_llm_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_401(request, timeout=None):
        raise _http_error(401)

    monkeypatch.setattr("urllib.request.urlopen", raise_401)
    with pytest.raises(LLMAuthError):
        OpenAIIntegration("sk-key").get_models()


def test_other_http_error_maps_to_llm_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_500(request, timeout=None):
        raise _http_error(500)

    monkeypatch.setattr("urllib.request.urlopen", raise_500)
    with pytest.raises(LLMError):
        OpenAIIntegration("sk-key").get_models()
