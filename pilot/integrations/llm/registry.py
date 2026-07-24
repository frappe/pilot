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
    for cls in INTEGRATIONS:
        if provider in cls.providers():
            return cls
    raise ValueError(f"Unknown LLM provider: {provider!r}")


def known_providers() -> set[str]:
    return {slug for cls in INTEGRATIONS for slug in cls.providers()}


def provider_options() -> list[dict]:
    """Provider list for the settings combobox."""
    return [
        {
            "value": slug,
            "label": label,
            "requires_api_base": cls.requires_api_base,
            "free_text_model": cls.free_text_model,
        }
        for cls in INTEGRATIONS
        for slug, label in cls.providers().items()
    ]


def models_for(provider: str) -> list[str]:
    """Selectable models for a provider (empty means free-text entry)."""
    return _integration_for(provider).get_models(provider)


def requires_api_base(provider: str) -> bool:
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
