from __future__ import annotations

import json
import urllib.error
import urllib.request

from pilot.integrations.llm.base import LLMError, LLMIntegration

# litellm's route for a self-hosted, OpenAI-compatible vLLM server. The model
# name after the slash is your vLLM --served-model-name, reached at api_base.
PROVIDER = "hosted_vllm"


class VLLMIntegration(LLMIntegration):
    """Self-hosted vLLM (OpenAI-compatible) served at `api_base`, via litellm."""

    provider = PROVIDER
    default_model = ""  # set per deployment; there is no shared catalog

    def get_models(self) -> list[str]:
        """Query the vLLM server's OpenAI-compatible /models endpoint."""
        if not self.api_base:
            return []
        request = urllib.request.Request(
            f"{self.api_base.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            raise LLMError(f"Could not list models from vLLM at {self.api_base}: {exc}") from exc
        return sorted(model["id"] for model in payload.get("data", []))
