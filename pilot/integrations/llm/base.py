"""Provider-agnostic chat-completion contract backed by litellm."""

from __future__ import annotations

try:
    import litellm
except ImportError:
    litellm = None

from pilot.exceptions import BenchError
from pilot.integrations.llm import read_system_prompt


class LLMError(BenchError):
    """An LLM provider call failed."""


class LLMAuthError(LLMError):
    """The provider rejected the API key."""


class LLMIntegration:
    """Base class for a chat-completion provider, routed through litellm."""

    provider: str = ""
    default_model: str = ""

    def __init__(self, api_key: str, stream: bool = False, api_base: str = "") -> None:
        if litellm is None:
            raise RuntimeError(
                "Required dependency `litellm` is not installed. Please install it to use LLMIntegration. "
                "Update admin dependencies to install the required packages",
            )
        self.api_key = api_key
        self.stream = stream
        # Endpoint URL for self-hosted providers (e.g., vLLM).
        self.api_base = api_base

    def get_models(self) -> list[str]:
        """Return the model IDs litellm knows for this provider."""
        return sorted(litellm.models_by_provider.get(self.provider, set()))

    def prompt(
        self,
        prompt: str,
        *,
        bench_root: str,
        model: str | None = None,
        max_tokens: int = 4096,
        **kwargs,
    ):
        """Send a single-turn prompt and return the litellm response."""
        messages = []
        messages.append({"role": "system", "content": read_system_prompt(bench_root)})
        messages.append({"role": "user", "content": prompt})
        try:
            return litellm.completion(
                model=f"{self.provider}/{model or self.default_model}",
                messages=messages,
                api_key=self.api_key,
                api_base=self.api_base or None,
                max_tokens=max_tokens,
                stream=self.stream,
                **kwargs,
            )
        except litellm.AuthenticationError as exc:
            raise LLMAuthError(f"{self.provider} rejected the API key.") from exc
        except litellm.APIError as exc:
            raise LLMError(f"{self.provider} API error: {exc}") from exc
        except litellm.exceptions.NotFoundError as exc:
            raise LLMError(f"{self.provider} model not found: {exc}") from exc
        except litellm.exceptions.BadGatewayError as exc:
            raise LLMError(f"{self.provider} bad gateway: {exc}") from exc

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
