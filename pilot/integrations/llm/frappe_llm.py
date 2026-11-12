from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import ClassVar

from pilot.integrations.llm.self_hosted import SelfHostedIntegration


def _failure_reason(exc: Exception) -> str:
    """The OS-level cause (DNS failure, refused connection, timeout), not the wrapper."""
    reason = getattr(exc, "reason", None) or exc
    return str(reason) or exc.__class__.__name__


class FrappeLLMIntegration(SelfHostedIntegration):
    """Frappe-hosted, OpenAI-compatible LLM. The endpoint is supplied per bench like
    any self-hosted server; unlike them it has no static catalog, so models are listed
    from the live `/models` endpoint and that call needs the user's API key."""

    requires_api_base: ClassVar[bool] = True
    free_text_model: ClassVar[bool] = False
    models_need_api_key: ClassVar[bool] = True

    @classmethod
    def providers(cls) -> dict[str, str]:
        """The providers this integration serves."""
        return {"frappe-llm": "Frappe LLM"}

    @classmethod
    def get_models(cls, provider: str, api_key: str = "", api_base: str = "") -> list[str]:
        """The models of the provider; raise without an API key."""
        if not api_key:
            raise ValueError("Frappe LLM needs an API key to list models.")

        endpoint = api_base.strip().rstrip("/")
        if not endpoint:
            raise ValueError("Frappe LLM needs an API base URL to list models.")

        url = f"{endpoint}/models"
        request = urllib.request.Request(
            url=url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise ValueError(f"{url} rejected this API key.") from exc
            raise ValueError(f"{url} returned HTTP {exc.code} listing models.") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ValueError(f"Could not reach {url}: {_failure_reason(exc)}.") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"{url} returned a model list that is not JSON.") from exc

        models = [model.get("id", "") for model in payload.get("data", [])]
        models = [model for model in models if model]
        if not models:
            raise ValueError(f"Frappe LLM - {url} returned no models for this API key.")
        return sorted(models)
