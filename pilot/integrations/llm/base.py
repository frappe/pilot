"""Base contract for an LLM provider integration, backed by litellm.

Each integration is fully self-describing: it declares its identity (`provider`,
`label`), how the settings form should treat it (`requires_api_base`,
`free_text_model`), and its selectable models (`get_models`). The registry relies
only on these class-level APIs, so a new provider is just a new subclass.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import litellm

from pilot.exceptions import BenchError
from pilot.integrations.llm import read_system_prompt


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
    # litellm route prefix. Blank => use the config `provider` slug (litellm-native
    # providers); set it when the UI slug differs from the litellm route.
    litellm_provider: ClassVar[str] = ""

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
    def get_models(cls, provider: str) -> list[str]:
        """Selectable models for a provider slug; empty means free-text entry."""
        return []

    @property
    def _litellm_model(self) -> str:
        """The model string handed to litellm.completion (routing lives here)."""
        return f"{self.litellm_provider or self.provider}/{self.model}"

    def prompt(self, prompt: str, *, bench_root: Path, max_tokens: int = 4096, **kwargs):
        """Send a single-turn prompt and return the litellm response."""
        messages = [
            {"role": "system", "content": read_system_prompt(bench_root)},
            {"role": "user", "content": prompt},
        ]
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
        # Specific subclasses first — NotFoundError etc. subclass APIError, so a
        # bare APIError catch above them would shadow them. Messages stay
        # actionable and never echo the raw provider body (it can be HTML).
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

    def get_response_text(self, response) -> str:
        """Extract the assistant's text from a `prompt` response."""
        if not response.choices:
            return ""
        return response.choices[0].message.content or ""

    def iter_response_text(self, stream):
        """Yield text deltas from a streamed `prompt` response (stream=True)."""
        for chunk in stream:
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            text = getattr(delta, "content", None) if delta else None
            if text:
                yield text
