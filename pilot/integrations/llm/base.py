"""Provider-agnostic HTTP contract for chat-completion LLM APIs."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod

from pilot.exceptions import BenchError

# LLM calls can be slow; give the model room to respond before timing out.
REQUEST_TIMEOUT = 120


class LLMError(BenchError):
    """An LLM provider API call failed."""


class LLMAuthError(LLMError):
    """The provider rejected the API key (HTTP 401/403)."""


class LLMIntegration(ABC):
    """Base class for a chat-completion provider's HTTP API."""

    name: str = ""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    @property
    @abstractmethod
    def base_url(self) -> str:
        """Return the base URL for the provider API, without a trailing slash."""

    @abstractmethod
    def authentication_headers(self) -> dict[str, str]:
        """Return the headers that authenticate a request to the provider."""

    @abstractmethod
    def get_models(self) -> list[str]:
        """Return the model IDs available to this API key."""

    @abstractmethod
    def prompt(
        self,
        prompt: str,
        *,
        model: str,
        system_prompt: str | None = None,
        max_tokens: int = 4096,
        **kwargs,
    ) -> dict:
        """Send a single-turn prompt and return the raw provider response."""

    @abstractmethod
    def get_response_text(self, response: dict) -> str:
        """Extract the assistant's text from a `prompt` response."""

    def send_request(self, path: str, method: str = "GET", data: dict | None = None) -> dict:
        """Send a request to the provider API and return the parsed JSON body."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = dict(self.authentication_headers())
        body = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        provider = self.name or "LLM provider"
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code in (401, 403):
                raise LLMAuthError(f"{provider} rejected the API key (HTTP {exc.code}).") from exc
            raise LLMError(f"{provider} API error (HTTP {exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"Could not reach {provider}: {exc.reason}.") from exc
