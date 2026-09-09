"""Tests for editing the [resource_limits] config via the admin Settings API."""

from __future__ import annotations

import json
import smtplib
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from admin.backend.api.v1.settings import ConfigPatcher, build_settings_response, settings_bp
from pilot.config import BenchConfig
from pilot.config.common import CommonConfig
from pilot.config.mail import MailConfig


def _config() -> BenchConfig:
    return BenchConfig._from_dict({"bench": {"name": "test-bench", "python": "3.14"}})


def _accepting_server():
    """Saving mail settings opens a session against the server; these tests are
    about what gets stored, not about reaching a relay."""
    return patch("admin.backend.api.v1.settings.config.check_mail_credentials")


def _client(bench_root: Path):
    bench_root.mkdir(parents=True)
    sites = bench_root / "sites"
    sites.mkdir()
    sites.joinpath("common_site_config.json").write_text("{}")
    bench_root.joinpath("bench.toml").write_text(
        BenchConfig.from_flat(bench_root.name, {"admin_password": "secret"}).dumps()
    )
    app = Flask(__name__)
    app.config["BENCH_ROOT"] = bench_root
    app.register_blueprint(settings_bp, url_prefix="/api/v1/settings")
    return app.test_client()


def test_patcher_updates_each_limit() -> None:
    config = _config()

    error = ConfigPatcher(
        config,
        {"resource_limits": {"cpu_usage_limit": 85, "memory_usage_limit": 75, "disk_space_limit": "90"}},
    ).apply()

    assert error is None
    assert config.resource_limits.cpu_usage_limit == 85
    assert config.resource_limits.memory_usage_limit == 75
    assert config.resource_limits.disk_space_limit == 90


def test_patcher_leaves_untouched_limits_alone() -> None:
    config = _config()
    config.resource_limits.memory_usage_limit = 70

    ConfigPatcher(config, {"resource_limits": {"cpu_usage_limit": 85}}).apply()

    assert config.resource_limits.memory_usage_limit == 70


def test_patcher_rejects_out_of_range_percentage() -> None:
    error = ConfigPatcher(_config(), {"resource_limits": {"cpu_usage_limit": 120}}).apply()

    assert error == "resource_limits.cpu_usage_limit must be a percentage between 0 and 100."


def test_patcher_rejects_non_numeric_limit() -> None:
    error = ConfigPatcher(_config(), {"resource_limits": {"disk_space_limit": "abc"}}).apply()

    assert error == "resource_limits.disk_space_limit must be a whole number."


def test_settings_response_exposes_limits() -> None:
    config = _config()
    config.resource_limits.cpu_usage_limit = 85

    assert build_settings_response(config)["resource_limits"] == {
        "cpu_usage_limit": 85,
        "memory_usage_limit": 0,
        "disk_space_limit": 0,
        "site_uptime": True,
        "webhook_endpoints": [],
        "email_recipients": [],
    }


def test_settings_response_never_returns_webhook_tokens() -> None:
    config = _config()
    config.resource_limits.webhook_endpoints = {"https://alerts.example.com/pilot": "tok-123"}

    response = build_settings_response(config)["resource_limits"]

    assert response["webhook_endpoints"] == [
        {"url": "https://alerts.example.com/pilot", "token_set": True}
    ]
    assert "tok-123" not in str(response)


def test_patch_persists_limits_to_common_config(tmp_path: Path) -> None:
    """Alert limits describe the host, so they belong to every bench on it."""
    benches_root = tmp_path / "benches"
    bench_root = benches_root / "current"
    client = _client(bench_root)

    response = client.patch("/api/v1/settings", json={"resource_limits": {"cpu_usage_limit": 85}})

    assert response.status_code == 200
    assert CommonConfig.read(benches_root).resource_limits.cpu_usage_limit == 85
    assert "resource_limits" not in bench_root.joinpath("bench.toml").read_text()
    assert client.get("/api/v1/settings").get_json()["resource_limits"]["cpu_usage_limit"] == 85


def test_patch_persists_webhooks_with_every_limit_off(tmp_path: Path) -> None:
    """Webhooks and a disabled uptime alert are the whole configuration on a
    host that only wants delivery changed, so the table still has to be written."""
    benches_root = tmp_path / "benches"
    client = _client(benches_root / "current")

    response = client.patch(
        "/api/v1/settings",
        json={
            "resource_limits": {
                "site_uptime": False,
                "webhook_endpoints": [
                    {"url": "https://alerts.example.com/pilot", "token": "tok-123"}
                ],
            }
        },
    )

    assert response.status_code == 200
    limits = CommonConfig.read(benches_root).resource_limits
    assert limits.webhook_endpoints == {"https://alerts.example.com/pilot": "tok-123"}
    assert limits.site_uptime is False


def test_patch_keeps_stored_token_when_blank(tmp_path: Path) -> None:
    benches_root = tmp_path / "benches"
    client = _client(benches_root / "current")
    endpoint = {"url": "https://alerts.example.com/pilot", "token": "tok-123"}
    client.patch("/api/v1/settings", json={"resource_limits": {"webhook_endpoints": [endpoint]}})

    client.patch(
        "/api/v1/settings",
        json={"resource_limits": {"webhook_endpoints": [{"url": endpoint["url"], "token": ""}]}},
    )

    stored = CommonConfig.read(benches_root).resource_limits.webhook_endpoints
    assert stored == {"https://alerts.example.com/pilot": "tok-123"}


def test_patch_persists_mail_settings_to_common_site_config(tmp_path: Path) -> None:
    benches_root = tmp_path / "benches"
    sites = benches_root / "current" / "sites"
    client = _client(benches_root / "current")
    mail = {
        "server": "smtp.example.com",
        "email": "alerts@example.com",
        "password": "secret",
        "use_ssl": True,
    }

    with _accepting_server():
        response = client.patch("/api/v1/settings", json={"mail": mail})

    assert response.status_code == 200
    stored = json.loads((sites / "common_site_config.json").read_text())
    assert stored["mail_server"] == "smtp.example.com"
    assert stored["mail_port"] == 465
    assert stored["auto_email_id"] == "alerts@example.com"
    assert stored["mail_login"] == "alerts@example.com"
    assert stored["mail_password"] == "secret"
    assert stored["use_tls"] == 0
    read_back = client.get("/api/v1/settings").get_json()["mail"]
    assert read_back["password_set"] is True
    assert "secret" not in str(read_back)


def test_patch_keeps_stored_mail_password_when_blank(tmp_path: Path) -> None:
    benches_root = tmp_path / "benches"
    sites = benches_root / "current" / "sites"
    client = _client(benches_root / "current")
    mail = {"server": "smtp.example.com", "email": "alerts@example.com"}
    with _accepting_server():
        client.patch("/api/v1/settings", json={"mail": {**mail, "password": "secret"}})

        client.patch(
            "/api/v1/settings",
            json={"mail": {**mail, "port": 2525, "password": ""}},
        )

    stored = MailConfig.read(sites)
    assert (stored.server, stored.port, stored.password) == ("smtp.example.com", 2525, "secret")


def test_patch_asks_for_the_password_again_when_the_server_changes(tmp_path: Path) -> None:
    """The password belongs to the server it was saved for. Naming a different host
    must neither disclose the stored one to that host nor quietly save a mailbox
    with no authentication at all, so the save is refused until it is retyped."""
    benches_root = tmp_path / "benches"
    sites = benches_root / "current" / "sites"
    client = _client(benches_root / "current")
    mail = {"server": "smtp.example.com", "email": "alerts@example.com"}
    with _accepting_server() as check:
        client.patch("/api/v1/settings", json={"mail": {**mail, "password": "secret"}})

        response = client.patch(
            "/api/v1/settings",
            json={"mail": {**mail, "server": "smtp2.example.com", "password": ""}},
        )

    assert response.status_code == 422
    assert not any(
        call.args[0].server == "smtp2.example.com" for call in check.call_args_list
    )
    stored = MailConfig.read(sites)
    assert (stored.server, stored.password) == ("smtp.example.com", "secret")


def test_patch_moves_to_a_new_server_when_the_password_comes_with_it(tmp_path: Path) -> None:
    benches_root = tmp_path / "benches"
    sites = benches_root / "current" / "sites"
    client = _client(benches_root / "current")
    mail = {"server": "smtp.example.com", "email": "alerts@example.com"}
    with _accepting_server():
        client.patch("/api/v1/settings", json={"mail": {**mail, "password": "secret"}})

        client.patch(
            "/api/v1/settings",
            json={"mail": {**mail, "server": "smtp2.example.com", "password": "fresh"}},
        )

    stored = MailConfig.read(sites)
    assert (stored.server, stored.password) == ("smtp2.example.com", "fresh")


def test_patch_clears_the_password_with_the_server(tmp_path: Path) -> None:
    """Blank means "keep" for the password, so clearing the server is the way out
    for a credential that has been rotated away."""
    benches_root = tmp_path / "benches"
    sites = benches_root / "current" / "sites"
    client = _client(benches_root / "current")
    with _accepting_server():
        client.patch(
            "/api/v1/settings",
            json={
                "mail": {
                    "server": "smtp.example.com",
                    "email": "alerts@example.com",
                    "password": "secret",
                }
            },
        )

        client.patch("/api/v1/settings", json={"mail": {"server": ""}})

    stored = MailConfig.read(sites)
    assert (stored.server, stored.password) == ("", "")
    assert "mail_server" not in json.loads((sites / "common_site_config.json").read_text())


def test_patch_rejects_an_unusable_recipient(tmp_path: Path) -> None:
    benches_root = tmp_path / "benches"
    client = _client(benches_root / "current")

    response = client.patch(
        "/api/v1/settings",
        json={"resource_limits": {"email_recipients": ["ops@example.com nope"]}},
    )

    assert response.status_code == 422
    assert CommonConfig.read(benches_root).resource_limits.email_recipients == []


def test_patch_accepts_recipients_before_a_mailbox_exists(tmp_path: Path) -> None:
    """Recipients are edited on the notifications page and the mailbox on its own
    one, so saving them in either order has to work."""
    benches_root = tmp_path / "benches"
    client = _client(benches_root / "current")

    response = client.patch(
        "/api/v1/settings", json={"resource_limits": {"email_recipients": ["ops@example.com"]}}
    )

    assert response.status_code == 200
    assert CommonConfig.read(benches_root).resource_limits.email_recipients == ["ops@example.com"]


def test_patch_checks_the_mail_credentials_before_storing_them(tmp_path: Path) -> None:
    """The framework's Email Account opens a session on save; settings that cannot
    reach the server must not be stored as if they were working."""
    benches_root = tmp_path / "benches"
    client = _client(benches_root / "current")

    with patch(
        "admin.backend.api.v1.settings.config.check_mail_credentials",
        side_effect=smtplib.SMTPAuthenticationError(535, b"Bad credentials"),
    ):
        response = client.patch(
            "/api/v1/settings",
            json={
                "mail": {
                    "server": "smtp.example.com",
                    "email": "alerts@example.com",
                    "password": "wrong",
                }
            },
        )

    assert response.status_code == 422
    assert "login name and password" in response.get_json()["error"]["message"]
    assert MailConfig.read(benches_root / "current" / "sites").server == ""


def test_a_save_that_leaves_mail_alone_does_not_dial_the_relay(tmp_path: Path) -> None:
    benches_root = tmp_path / "benches"
    client = _client(benches_root / "current")

    with patch("admin.backend.api.v1.settings.config.check_mail_credentials") as check:
        client.patch("/api/v1/settings", json={"resource_limits": {"cpu_usage_limit": 85}})

    check.assert_not_called()


def test_patch_rejects_invalid_limit_without_saving(tmp_path: Path) -> None:
    benches_root = tmp_path / "benches"
    bench_root = benches_root / "current"
    client = _client(bench_root)
    client.patch("/api/v1/settings", json={"resource_limits": {"cpu_usage_limit": 85}})

    response = client.patch("/api/v1/settings", json={"resource_limits": {"memory_usage_limit": 120}})

    assert response.status_code == 422
    assert CommonConfig.read(benches_root).resource_limits.memory_usage_limit == 0
    assert CommonConfig.read(benches_root).resource_limits.cpu_usage_limit == 85
