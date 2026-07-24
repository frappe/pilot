from __future__ import annotations

from typing import ClassVar

from pilot.integrations.llm.base import LLMIntegration


class SelfHostedIntegration(LLMIntegration):
    """A self-hosted, OpenAI-compatible server (e.g. vLLM) at a custom api_base."""

    requires_api_base: ClassVar[bool] = True
    free_text_model: ClassVar[bool] = True
    litellm_provider: ClassVar[str] = "hosted_vllm"  # OpenAI-compatible route

    @classmethod
    def providers(cls) -> dict[str, str]:
        return {"self-hosted": "Self-hosted Model"}

    @classmethod
    def get_models(cls, provider: str) -> list[str]:
        return []  # the served model name is entered by hand
