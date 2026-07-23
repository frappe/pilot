from __future__ import annotations

from dataclasses import dataclass

from pilot.exceptions import ConfigError


@dataclass
class LLMConfig:
    """Credentials and defaults for the bench's LLM integration.

    Only structured scalars live here (and in bench.toml). The system prompt is
    free text kept in a sidecar file — see `read_system_prompt`.
    """

    provider: str = ""
    api_key: str = ""
    model: str = ""  # blank falls back to the integration's default model
    max_tokens: int = 4096  # hard cap on generated tokens per request
    api_base: str = ""  # endpoint URL for self-hosted providers

    def validate(self) -> None:
        if self.max_tokens <= 0:
            raise ConfigError("llm.max_tokens must be a positive integer.")
