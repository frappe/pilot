"""LLM provider registry — a thin aggregator over self-describing integrations.

Each integration class declares the provider slugs it handles (`providers`), how
to list their models (`get_models`), and how the form should treat them. The
registry maps each slug to its owning class; adding a provider is a class change,
not a registry change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pilot.integrations.llm.base import LLMIntegration
from pilot.integrations.llm.frappe_llm import FrappeLLMIntegration
from pilot.integrations.llm.lite import LiteLLMIntegration
from pilot.integrations.llm.self_hosted import SelfHostedIntegration

if TYPE_CHECKING:
    from pilot.config.llm import LLMConfig

INTEGRATIONS: tuple[type[LLMIntegration], ...] = (
    FrappeLLMIntegration,
    LiteLLMIntegration,
    SelfHostedIntegration,
)


def _integration_for(provider: str) -> type[LLMIntegration]:
    """The integration that serves the provider; raise for an unknown one."""
    for cls in INTEGRATIONS:
        if provider in cls.providers():
            return cls
    raise ValueError(f"Unknown LLM provider: {provider!r}")


def known_providers() -> set[str]:
    """Every known provider."""
    return {slug for cls in INTEGRATIONS for slug in cls.providers()}


def provider_options() -> list[dict]:
    """Provider list for the settings combobox."""
    return [
        {
            "value": slug,
            "label": label,
            "requires_api_base": cls.requires_api_base,
            "free_text_model": cls.free_text_model,
            "models_need_api_key": cls.models_need_api_key,
        }
        for cls in INTEGRATIONS
        for slug, label in cls.providers().items()
    ]


def models_for(provider: str, api_key: str = "", api_base: str = "") -> list[str]:
    """Selectable models for a provider (empty means free-text entry). Providers
    with `models_need_api_key` list models from their own API, so they need the key,
    and an `api_base` overrides wherever that provider defaults to."""
    return _integration_for(provider).get_models(provider, api_key, api_base)


def models_need_api_key(provider: str) -> bool:
    """Whether listing models needs an API key."""
    return _integration_for(provider).models_need_api_key


def requires_api_base(provider: str) -> bool:
    """Whether the provider needs an API base URL."""
    return _integration_for(provider).requires_api_base


def is_configured(llm_config: LLMConfig) -> bool:
    """Whether the bench has a usable AI provider connected."""
    return bool(llm_config.provider and llm_config.api_key and llm_config.model)


def build_integration(llm_config: LLMConfig, *, stream: bool = False) -> LLMIntegration:
    """Construct the integration described by a bench's LLM config."""
    return _integration_for(llm_config.provider)(
        llm_config.api_key,
        provider=llm_config.provider,
        model=llm_config.model,
        stream=stream,
        api_base=llm_config.api_base,
    )
