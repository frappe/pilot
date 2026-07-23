from __future__ import annotations

from typing import ClassVar

import litellm

from pilot.integrations.llm.base import LLMIntegration


class LiteLLMIntegration(LLMIntegration):
    """Major providers from litellm's catalog, routed as ``provider/model``."""

    # Curated major providers; each is listed only if litellm has a model catalog
    # for it. Routing and model listing come straight from litellm.
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
        return {
            slug: label
            for slug, label in cls._SUPPORTED_LITELLM_PROVIDERS.items()
            if slug in litellm.models_by_provider
        }

    @classmethod
    def get_models(cls, provider: str) -> list[str]:
        return sorted(litellm.models_by_provider.get(provider, set()))
