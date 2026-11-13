from __future__ import annotations

from typing import ClassVar

from pilot.integrations.llm.base import LLMIntegration


class LiteLLMIntegration(LLMIntegration):
    """Major providers from litellm's catalog, routed as ``provider/model``."""

    # Curated major providers. Routing and model listing come straight from litellm,
    # which only get_models imports - listing them must not cost that import.
    _SUPPORTED_LITELLM_PROVIDERS: ClassVar[dict[str, str]] = {
        "openai": "OpenAI",
        "anthropic": "Anthropic",
        "gemini": "Google Gemini",
        "vertex_ai": "Google Vertex AI",
        "azure": "Azure OpenAI",
        "bedrock": "AWS Bedrock",
        "openrouter": "OpenRouter",
        "mistral": "Mistral",
        "groq": "Groq",
        "cohere": "Cohere",
        "deepseek": "DeepSeek",
        "xai": "xAI",
        "ollama": "Ollama",
    }

    @classmethod
    def providers(cls) -> dict[str, str]:
        """The providers this integration serves."""
        return dict(cls._SUPPORTED_LITELLM_PROVIDERS)

    @classmethod
    def get_models(cls, provider: str, api_key: str = "", api_base: str = "") -> list[str]:
        """The models LiteLLM knows for the provider."""
        import litellm

        return sorted(litellm.models_by_provider.get(provider, set()))
