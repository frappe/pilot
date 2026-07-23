from __future__ import annotations

from pilot.integrations.llm.base import LLMIntegration

DEFAULT_MODEL = "gpt-4o"


class OpenAIIntegration(LLMIntegration):
    """OpenAI Chat Completions API over raw HTTP."""

    name = "OpenAI"

    @property
    def base_url(self) -> str:
        return "https://api.openai.com"

    def authentication_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

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
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
            **kwargs,
        }
        return self.send_request("v1/chat/completions", method="POST", data=body)

    def get_response_text(self, response: dict) -> str:
        choices = response.get("choices", [])
        if not choices:
            return ""
        return choices[0].get("message", {}).get("content") or ""
