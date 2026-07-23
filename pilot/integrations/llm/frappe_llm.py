from __future__ import annotations

from typing import ClassVar

from pilot.integrations.llm.self_hosted import SelfHostedIntegration


class FrappeLLMIntegration(SelfHostedIntegration):
    """Frappe-hosted, OpenAI-compatible LLM at a fixed endpoint and model set."""

    # Keeping the IP hardcoded for now; will move to a domain once available.
    base_api: ClassVar[str] = "http://54.251.169.42/v1"
    requires_api_base: ClassVar[bool] = False
    free_text_model: ClassVar[bool] = False

    @classmethod
    def providers(cls) -> dict[str, str]:
        return {"frappe-llm": "Frappe LLM"}

    @classmethod
    def get_models(cls, provider: str) -> list[str]:
        return ["qwen3.6-27b-fp8", "deepseek-v4-flash"]
