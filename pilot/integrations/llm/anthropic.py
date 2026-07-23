from __future__ import annotations

from pilot.integrations.llm.base import LLMIntegration

DEFAULT_MODEL = "claude-opus-4-8"
ANTHROPIC_VERSION = "2023-06-01"


class AnthropicIntegration(LLMIntegration):
    """Anthropic Messages API over raw HTTP."""

    name = "Anthropic"

    @property
    def base_url(self) -> str:
        return "https://api.anthropic.com"

    def authentication_headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }

    def get_models(self) -> list[str]:
        payload = self.send_request("v1/models")
        return [model["id"] for model in payload.get("data", [])]

    def prompt(
        self,
        prompt: str,
        *,
        model: str = DEFAULT_MODEL,
        system_prompt: str | None = None,
        max_tokens: int = 4096,
        **kwargs,
    ) -> dict:
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            **kwargs,
        }
        if system_prompt:
            body["system"] = system_prompt
        return self.send_request("v1/messages", method="POST", data=body)

    def get_response_text(self, response: dict) -> str:
        blocks = response.get("content", [])
        return "".join(block.get("text", "") for block in blocks if block.get("type") == "text")
