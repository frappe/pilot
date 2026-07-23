"""Provider-agnostic chat-completion contract backed by litellm."""

from __future__ import annotations

from pathlib import Path

import litellm

from pilot.exceptions import BenchError
from pilot.integrations.llm import read_system_prompt


class LLMError(BenchError):
    """An LLM provider call failed."""


class LLMAuthError(LLMError):
    """The provider rejected the API key."""


class LLMIntegration:
    """Any litellm-supported chat provider, addressed as ``provider/model``."""

    def __init__(
        self,
        api_key: str,
        *,
        provider: str,
        model: str,
        stream: bool = False,
        api_base: str = "",
    ) -> None:
        self.api_key = api_key
        self.provider = provider
        self.model = model
        self.stream = stream
        # Endpoint URL for self-hosted providers (e.g., vLLM).
        self.api_base = api_base

    def get_models(self) -> list[str]:
        """Return the model IDs litellm knows for this provider."""
        return sorted(litellm.models_by_provider.get(self.provider, set()))

    def prompt(self, prompt: str, *, bench_root: Path, max_tokens: int = 4096, **kwargs):
        """Send a single-turn prompt and return the litellm response."""
        messages = [
            {"role": "system", "content": read_system_prompt(bench_root)},
            {"role": "user", "content": prompt},
        ]
        try:
            return litellm.completion(
                model=f"{self.provider}/{self.model}",
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
