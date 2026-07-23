"""litellm provider/model catalog and integration construction for the settings UI."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import litellm

from pilot.integrations.llm.base import LLMIntegration
from pilot.integrations.llm.vllm import VLLMIntegration

if TYPE_CHECKING:
    from pilot.config.llm import LLMConfig

# Self-hosted providers are the only special case: they need an api_base and their
# own integration factory (no `provider` arg — it's fixed by the class).
SELF_HOSTED_INTEGRATIONS: dict[str, Callable[..., LLMIntegration]] = {
    "self-hosted": VLLMIntegration,
}

# Providers surfaced in the settings combobox. Hosted providers route straight
# through litellm by slug; self-hosted slugs are the keys in SELF_HOSTED_INTEGRATIONS.
# Add a provider here to make it pluggable — no other code change is needed.
PROVIDER_LABELS = {
    "self-hosted": "Self-hosted Model",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "openrouter": "OpenRouter",
    "azure": "Azure OpenAI",
    "vertex_ai": "Google Vertex AI",
    "bedrock": "AWS Bedrock",
    "gemini": "Google Gemini",
    "mistral": "Mistral",
    "groq": "Groq",
    "ollama": "Ollama",
    "cohere": "Cohere",
}


def is_self_hosted(provider: str) -> bool:
    return provider in SELF_HOSTED_INTEGRATIONS


def known_providers() -> set[str]:
    """The providers we surface in the UI."""
    return set(PROVIDER_LABELS)


def provider_options() -> list[dict]:
    """Provider list for the settings combobox (searchable in the UI)."""
    return [
        {"value": provider, "label": PROVIDER_LABELS[provider], "self_hosted": is_self_hosted(provider)}
        for provider in sorted(known_providers())
    ]


def models_for(provider: str) -> list[str]:
    """Model ids litellm knows for a provider; self-hosted models come from the server."""
    if is_self_hosted(provider):
        return []
    return sorted(litellm.models_by_provider.get(provider, set()))


def is_configured(llm_config: LLMConfig) -> bool:
    """Whether the bench has a usable AI provider connected."""
    return bool(llm_config.provider and llm_config.api_key and llm_config.model)


def build_integration(llm_config: LLMConfig, *, stream: bool = False) -> LLMIntegration:
    """Construct the integration described by a bench's LLM config."""
    self_hosted = SELF_HOSTED_INTEGRATIONS.get(llm_config.provider)
    if self_hosted is not None:
        return self_hosted(
            llm_config.api_key,
            model=llm_config.model,
            stream=stream,
            api_base=llm_config.api_base,
        )
    return LLMIntegration(
        llm_config.api_key,
        provider=llm_config.provider,
        model=llm_config.model,
        stream=stream,
        api_base=llm_config.api_base,
    )
