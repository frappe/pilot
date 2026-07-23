"""Tests for pilot.integrations.llm - Anthropic Messages API over raw HTTP."""

from __future__ import annotations

import io
import json
import urllib.error
from contextlib import contextmanager

import pytest

from pilot.integrations.llm.anthropic import (
    ANTHROPIC_VERSION,
    DEFAULT_MODEL,
    AnthropicIntegration,
)
from pilot.integrations.llm.base import LLMAuthError, LLMError


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@contextmanager
def _capture(monkeypatch: pytest.MonkeyPatch, payload: dict):
    """Patch urlopen; record the outgoing request, return `payload` as the body."""
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode()) if request.data else None
        captured["timeout"] = timeout
        return _FakeResponse(json.dumps(payload).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    yield captured


def test_authentication_headers_use_raw_key_and_version() -> None:
    headers = AnthropicIntegration("sk-key").authentication_headers()
    assert headers["x-api-key"] == "sk-key"  # no "Bearer" prefix
    assert headers["anthropic-version"] == ANTHROPIC_VERSION


def test_get_models_returns_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    with _capture(monkeypatch, {"data": [{"id": "claude-opus-4-8"}, {"id": "claude-haiku-4-5"}]}):
        models = AnthropicIntegration("sk-key").get_models()
    assert models == ["claude-opus-4-8", "claude-haiku-4-5"]


def test_prompt_builds_messages_body(monkeypatch: pytest.MonkeyPatch) -> None:
    with _capture(monkeypatch, {"content": []}) as captured:
        AnthropicIntegration("sk-key").prompt("hello", system_prompt="be terse")

    assert captured["url"] == "https://api.anthropic.com/v1/messages"  # single slash
    assert captured["method"] == "POST"
    body = captured["body"]
    assert body["model"] == DEFAULT_MODEL
    assert body["max_tokens"] == 4096
    assert body["system"] == "be terse"
    assert body["messages"] == [{"role": "user", "content": "hello"}]


def test_prompt_omits_system_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    with _capture(monkeypatch, {"content": []}) as captured:
        AnthropicIntegration("sk-key").prompt("hi")
    assert "system" not in captured["body"]


def test_get_response_text_joins_text_blocks() -> None:
    response = {
        "content": [
            {"type": "text", "text": "Hello "},
            {"type": "thinking", "thinking": "ignored"},
            {"type": "text", "text": "world"},
        ]
    }
    assert AnthropicIntegration("sk-key").get_response_text(response) == "Hello world"


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("url", code, "err", {}, io.BytesIO(b'{"error": "x"}'))


def test_auth_error_maps_to_llm_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_401(request, timeout=None):
        raise _http_error(401)

    monkeypatch.setattr("urllib.request.urlopen", raise_401)
    with pytest.raises(LLMAuthError):
        AnthropicIntegration("sk-key").get_models()


def test_other_http_error_maps_to_llm_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_500(request, timeout=None):
        raise _http_error(500)

    monkeypatch.setattr("urllib.request.urlopen", raise_500)
    with pytest.raises(LLMError):
        AnthropicIntegration("sk-key").get_models()
