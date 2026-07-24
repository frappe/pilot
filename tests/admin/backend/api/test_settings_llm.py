"""Tests for editing the [llm] config via the admin Settings API."""

from __future__ import annotations

from pathlib import Path

from flask import Flask

from admin.backend.api.v1.settings import (
    ConfigPatcher,
    build_settings_response,
    settings_bp,
)
from pilot.config import BenchConfig
from pilot.integrations.llm import DEFAULT_SYSTEM_PROMPT, system_prompt_path


def _config() -> BenchConfig:
    return BenchConfig._from_dict(
        {
            "bench": {"name": "test-bench", "python": "3.14"},
            "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "develop"}],
            "mariadb": {"root_password": "root"},
        }
    )


def _client(bench_root: Path):
    bench_root.mkdir()
    bench_root.joinpath("bench.toml").write_text(
        BenchConfig.from_flat(bench_root.name, {"admin_password": "secret"}).dumps()
    )
    app = Flask(__name__)
    app.config["BENCH_ROOT"] = bench_root
    app.register_blueprint(settings_bp, url_prefix="/api/v1/settings")
    return app.test_client()


def test_patcher_updates_provider_key_and_model() -> None:
    config = _config()

    error = ConfigPatcher(
        config,
        {"llm": {"provider": "anthropic", "api_key": "sk-key", "model": "claude-opus-4-8", "max_tokens": 2048}},
    ).apply()

    assert error is None
    assert config.llm.provider == "anthropic"
    assert config.llm.api_key == "sk-key"
    assert config.llm.model == "claude-opus-4-8"
    assert config.llm.max_tokens == 2048


def test_patcher_sets_key_only_when_provided() -> None:
    config = _config()
    config.llm.api_key = "original"

    ConfigPatcher(config, {"llm": {"provider": "openai", "api_key": ""}}).apply()
    assert config.llm.api_key == "original"
    assert config.llm.provider == "openai"


def test_patcher_rejects_unknown_provider() -> None:
    config = _config()
    error = ConfigPatcher(config, {"llm": {"provider": "nope", "api_key": "k"}}).apply()
    assert error is not None
    assert "llm.provider" in error


def test_patcher_requires_api_base_for_self_hosted() -> None:
    config = _config()
    error = ConfigPatcher(config, {"llm": {"provider": "self-hosted", "api_key": "k", "model": "m"}}).apply()
    assert error is not None
    assert "api_base" in error


def test_patcher_accepts_self_hosted_with_api_base() -> None:
    config = _config()
    error = ConfigPatcher(
        config,
        {"llm": {"provider": "self-hosted", "api_key": "k", "model": "m", "api_base": "http://vllm:8000/v1"}},
    ).apply()
    assert error is None
    assert config.llm.api_base == "http://vllm:8000/v1"


def test_patcher_disconnect_resets_llm() -> None:
    config = _config()
    config.llm.provider = "openai"
    config.llm.api_key = "sk-key"

    ConfigPatcher(config, {"llm": {"disconnect": True}}).apply()
    assert config.llm == type(config.llm)()


def test_response_exposes_llm_without_secret() -> None:
    config = _config()
    config.llm.provider = "anthropic"
    config.llm.api_key = "sk-secret"

    payload = build_settings_response(config)

    assert payload["llm"]["provider"] == "anthropic"
    assert payload["llm"]["api_key_set"] is True
    assert "api_key" not in payload["llm"]
    options = {p["value"]: p for p in payload["llm_providers"]}
    assert {"anthropic", "openai", "self-hosted", "frappe-llm"} <= options.keys()
    assert options["self-hosted"]["requires_api_base"] is True
    assert options["anthropic"]["requires_api_base"] is False


def test_system_prompt_persists_to_sidecar_not_toml(tmp_path: Path) -> None:
    bench_root = tmp_path / "test-bench"
    client = _client(bench_root)

    response = client.patch(
        "/api/v1/settings",
        json={"llm": {"provider": "openai", "api_key": "sk-key", "model": "gpt-4o", "system_prompt": "Be terse."}},
    )
    assert response.status_code == 200

    # Prompt lands in the sidecar file, never in bench.toml.
    assert system_prompt_path(bench_root).read_text() == "Be terse."
    assert "Be terse." not in bench_root.joinpath("bench.toml").read_text()
    assert "system_prompt" not in bench_root.joinpath("bench.toml").read_text()

    # And it round-trips back through GET.
    settings = client.get("/api/v1/settings").get_json()
    assert settings["llm"]["system_prompt"] == "Be terse."
    assert settings["llm"]["provider"] == "openai"


def test_system_prompt_defaults_when_unset(tmp_path: Path) -> None:
    client = _client(tmp_path / "test-bench")
    settings = client.get("/api/v1/settings").get_json()
    assert settings["llm"]["system_prompt"] == DEFAULT_SYSTEM_PROMPT


def test_disconnect_clears_sidecar_prompt(tmp_path: Path) -> None:
    bench_root = tmp_path / "test-bench"
    client = _client(bench_root)
    client.patch(
        "/api/v1/settings",
        json={"llm": {"provider": "openai", "api_key": "k", "model": "gpt-4o", "system_prompt": "x"}},
    )
    assert system_prompt_path(bench_root).exists()

    client.patch("/api/v1/settings", json={"llm": {"disconnect": True}})
    assert not system_prompt_path(bench_root).exists()
