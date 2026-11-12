"""Base contract for an LLM provider integration, backed by litellm.

Each integration is fully self-describing: it declares its identity (`provider`,
`label`), how the settings form should treat it (`requires_api_base`,
`free_text_model`), and its selectable models (`get_models`). The registry relies
only on these class-level APIs, so a new provider is just a new subclass.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from pilot.exceptions import BenchError
from pilot.integrations.llm import read_system_prompt

# Answers are small; the cap only stops a long-lived process growing without bound.
_CACHE_LIMIT = 32


class LLMError(BenchError):
    """An LLM provider call failed."""


class LLMAuthError(LLMError):
    """The provider rejected the API key."""


class LLMIntegration:
    """One kind of LLM integration. Subclasses declare the provider slugs they
    handle (`providers`), their models (`get_models`), and the class attributes
    below. The registry relies only on these class APIs."""

    base_api: ClassVar[str] = ""  # fixed endpoint baked into the provider
    requires_api_base: ClassVar[bool] = False  # user must supply an api_base
    free_text_model: ClassVar[bool] = False  # user types the model, not a picklist
    models_need_api_key: ClassVar[bool] = False  # `get_models` calls the provider with the key
    # litellm route prefix. Blank => use the config `provider` slug (litellm-native
    # providers); set it when the UI slug differs from the litellm route.
    litellm_provider: ClassVar[str] = ""
    _cached_responses: ClassVar[dict[str, str]] = {}

    def __init__(
        self, api_key: str, *, provider: str, model: str, stream: bool = False, api_base: str = ""
    ) -> None:
        self.api_key = api_key
        self.provider = provider
        self.model = model
        self.stream = stream
        self.api_base = api_base or self.base_api

    @classmethod
    def providers(cls) -> dict[str, str]:
        """Provider slug -> display label handled by this integration."""
        return {}

    @classmethod
    def get_models(cls, provider: str, api_key: str = "", api_base: str = "") -> list[str]:
        """Selectable models for a provider slug; empty means free-text entry,
        `api_key` is only used by providers with `models_need_api_key`."""
        return []

    @property
    def _litellm_model(self) -> str:
        """The model string handed to litellm.completion (routing lives here)."""
        return f"{self.litellm_provider or self.provider}/{self.model}"

    def _cache_key(self, system_prompt: str, prompt: str) -> str:
        """The cache key of a prompt."""
        return f"{system_prompt}-{prompt}-{self._litellm_model}"

    def get_cached_response(self, cache_key: str) -> str | None:
        """The answer already produced for this exact system prompt, prompt and model."""
        return self._cached_responses.get(cache_key)

    def _remember_response(self, cache_key: str, text: str) -> None:
        """Only called once an answer is complete - a half-read stream is not an answer."""
        if not text:
            return
        self._cached_responses[cache_key] = text
        while len(self._cached_responses) > _CACHE_LIMIT:
            self._cached_responses.pop(next(iter(self._cached_responses)))

    def prompt(
        self,
        prompt: str,
        *,
        bench_root: Path,
        max_tokens: int = 4096,
        refresh: bool = False,
        **kwargs,
    ) -> str | Iterator[str]:
        """The answer to a single-turn prompt: text, or text deltas when streaming.
        Repeating a prompt replays the cached answer; `refresh` asks the provider
        again and replaces what was cached."""
        system_prompt = read_system_prompt(bench_root)
        cache_key = self._cache_key(system_prompt, prompt)

        cached = None if refresh else self.get_cached_response(cache_key)
        if cached is not None:
            return iter([cached]) if self.stream else cached

        raw = self._complete(system_prompt, prompt, max_tokens, **kwargs)
        if self.stream:
            return self._iter_answer(raw, cache_key)
        return self._answer_text(raw, cache_key)

    def _complete(self, system_prompt: str, prompt: str, max_tokens: int, **kwargs):
        """Call litellm, mapping its errors onto ours. Imported here because the
        import costs seconds, and every settings page load would pay it."""
        import litellm

        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}]
        try:
            return litellm.completion(
                model=self._litellm_model,
                messages=messages,
                api_key=self.api_key,
                api_base=self.api_base or None,
                max_tokens=max_tokens,
                stream=self.stream,
                **kwargs,
            )
        except litellm.AuthenticationError as exc:
            raise LLMAuthError("The API key was rejected. Check the provider key in Settings.") from exc
        except litellm.NotFoundError as exc:
            raise LLMError("Model or endpoint not found. Check the model name and API base URL.") from exc
        except litellm.RateLimitError as exc:
            raise LLMError("The AI provider is rate limiting requests. Try again shortly.") from exc
        except (litellm.APIConnectionError, litellm.Timeout) as exc:
            raise LLMError(
                "Could not reach the AI provider. Check the API base URL and that the server is running."
            ) from exc
        except litellm.APIError as exc:
            raise LLMError("The AI provider returned an error. Check the model and endpoint.") from exc

    def _answer_text(self, response, cache_key: str) -> str:
        """The assistant's text from a non-streamed litellm response, cached."""
        if not response.choices:
            return ""
        text = response.choices[0].message.content or ""
        self._remember_response(cache_key, text)
        return text

    def _iter_answer(self, stream, cache_key: str) -> Iterator[str]:
        """Yield deltas, caching the answer once the stream has run to completion."""
        deltas: list[str] = []
        for chunk in stream:
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            text = getattr(delta, "content", None) if delta else None
            if text:
                deltas.append(text)
                yield text

        # Unreached when the caller abandons the stream or the provider fails, so a
        # partial answer is never cached.
        self._remember_response(cache_key, "".join(deltas))
