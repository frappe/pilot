import json

import pytest

from pilot.core.site.config.public import PublicConfig
from tests.admin.backend.test_admin_app import _client


def test_public_site_config_hides_system_fields_and_masks_secrets() -> None:
    config = {
        "developer_mode": 1,
        "maintenance_mode": 0,
        "pause_scheduler": 1,
        "frappe_branch": "version-16",
        "db_password": "database-secret",
        "encryption_key": "encryption-secret",
        "pilot_auth_token": "session-secret",
        "future_provider_api_token": "unknown-secret",
        "apiKey": "camel-secret",
        "apiKeys": ["camel-secret"],
        "accessKey": "camel-secret",
        "key": "generic-secret",
        "custom_provider": {
            "endpoint": "https://provider.example",
            "access_token": "nested-secret",
            "options": [
                {"region": "eu", "client_secret": "list-secret"},
                "unchanged",
            ],
        },
        "unreviewed_option": True,
    }

    assert PublicConfig(config).visible == {
        "developer_mode": 1,
        "maintenance_mode": 0,
        "pause_scheduler": 1,
        "frappe_branch": "version-16",
        "encryption_key": PublicConfig.SECRET_MASK,
        "future_provider_api_token": PublicConfig.SECRET_MASK,
        "apiKey": PublicConfig.SECRET_MASK,
        "apiKeys": PublicConfig.SECRET_MASK,
        "accessKey": PublicConfig.SECRET_MASK,
        "key": PublicConfig.SECRET_MASK,
        "custom_provider": {
            "endpoint": "https://provider.example",
            "options": [{"region": "eu"}, "unchanged"],
        },
        "unreviewed_option": True,
    }


def test_public_site_config_copies_mutable_values() -> None:
    config = {"maintenance_mode": {"enabled": True}}

    public = PublicConfig(config).visible
    public["maintenance_mode"]["enabled"] = False

    assert config["maintenance_mode"] == {"enabled": True}


def test_site_configuration_preserves_custom_keys_without_exposing_secrets(tmp_path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    site_dir = bench_root / "sites" / "s.localhost"
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text(
        json.dumps(
            {
                "installed_apps": [],
                "db_name": "private-database-name",
                "db_host": "private-database-host",
                "developer_mode": 1,
                "custom_app_mode": "strict",
                "future_provider_api_token": "unknown-secret",
            }
        )
    )

    response = client.get("/api/v1/sites/s.localhost/configuration")

    assert response.status_code == 200
    body = response.get_json()
    assert body == {
        "developer_mode": 1,
        "custom_app_mode": "strict",
        "future_provider_api_token": PublicConfig.SECRET_MASK,
    }
    assert "db_name" not in body
    assert "db_host" not in body
    assert "db_type" not in body

    detail = client.get("/api/v1/sites/s.localhost").get_json()
    assert "site_config" not in detail
    assert detail["framework_branch"] == ""


def test_site_config_patch_preserves_omitted_and_hidden_custom_keys(tmp_path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    config_path = bench_root / "sites" / "s.localhost" / "site_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "installed_apps": [],
                "developer_mode": 0,
                "future_provider_api_token": "unknown-secret",
                "custom_credential": {"value": "also-hidden"},
                "custom_provider": {
                    "endpoint": "https://old.example",
                    "accessToken": "nested-secret",
                    "regions": [
                        {"name": "eu", "apiKey": "region-secret"},
                    ],
                },
            }
        )
    )

    response = client.patch(
        "/api/v1/sites/s.localhost/configuration",
        json={
            "developer_mode": 1,
            "custom_provider": {
                "endpoint": "https://new.example",
            },
        },
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "developer_mode": 1,
        "future_provider_api_token": PublicConfig.SECRET_MASK,
        "custom_credential": PublicConfig.SECRET_MASK,
        "custom_provider": {
            "endpoint": "https://new.example",
            "regions": [{"name": "eu"}],
        },
    }
    assert json.loads(config_path.read_text()) == {
        "installed_apps": [],
        "developer_mode": 1,
        "future_provider_api_token": "unknown-secret",
        "custom_credential": {"value": "also-hidden"},
        "custom_provider": {
            "endpoint": "https://new.example",
            "accessToken": "nested-secret",
            "regions": [
                {"name": "eu", "apiKey": "region-secret"},
            ],
        },
    }


def test_site_config_patch_deletes_public_key_with_null(tmp_path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    config_path = bench_root / "sites" / "s.localhost" / "site_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"custom_mode": "strict", "developer_mode": 1}))

    response = client.patch(
        "/api/v1/sites/s.localhost/configuration",
        json={"custom_mode": None},
    )

    assert response.status_code == 200
    assert response.get_json() == {"developer_mode": 1}
    assert json.loads(config_path.read_text()) == {"developer_mode": 1}


def test_site_config_patch_rejects_protected_keys_and_hidden_container_changes(tmp_path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    config_path = bench_root / "sites" / "s.localhost" / "site_config.json"
    config_path.parent.mkdir(parents=True)
    original = {
        "custom_provider": {
            "endpoint": "https://old.example",
            "apiToken": "hidden",
        },
        "regions": [{"name": "eu", "apiKey": "hidden"}],
    }
    config_path.write_text(json.dumps(original))

    protected = client.patch(
        "/api/v1/sites/s.localhost/configuration",
        json={"db_password": "changed"},
    )
    type_change = client.patch(
        "/api/v1/sites/s.localhost/configuration",
        json={"custom_provider": "reset"},
    )
    list_change = client.patch(
        "/api/v1/sites/s.localhost/configuration",
        json={"regions": [{"name": "us"}]},
    )

    assert [protected.status_code, type_change.status_code, list_change.status_code] == [
        422,
        422,
        422,
    ]
    assert json.loads(config_path.read_text()) == original


def test_a_secret_can_be_set_and_removed_but_never_read(tmp_path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    config_path = bench_root / "sites" / "s.localhost" / "site_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"encryption_key": "old"}))
    url = "/api/v1/sites/s.localhost/configuration"

    changed = client.patch(url, json={"encryption_key": "new", "mail_password": "p"})
    masked = client.patch(url, json={"encryption_key": PublicConfig.SECRET_MASK})

    assert changed.get_json() == {"encryption_key": PublicConfig.SECRET_MASK, "mail_password": PublicConfig.SECRET_MASK}
    assert client.get(url).get_json() == {"encryption_key": PublicConfig.SECRET_MASK, "mail_password": PublicConfig.SECRET_MASK}
    assert masked.status_code == 422
    assert json.loads(config_path.read_text()) == {"encryption_key": "new", "mail_password": "p"}

    assert client.patch(url, json={"mail_password": None}).get_json() == {"encryption_key": PublicConfig.SECRET_MASK}


def test_a_secret_inside_a_value_stays_hidden_and_unwritable(tmp_path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    config_path = bench_root / "sites" / "s.localhost" / "site_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"provider": {"url": "u"}}))

    response = client.patch("/api/v1/sites/s.localhost/configuration", json={"provider": {"api_key": "k"}})

    assert response.status_code == 422
    assert json.loads(config_path.read_text()) == {"provider": {"url": "u"}}


@pytest.mark.parametrize("key", ["cloud_server_url", "cloud_settings_embed_url"])
@pytest.mark.parametrize("value", ["https://attacker.example", None])
def test_site_config_patch_rejects_the_browser_admin_urls(tmp_path, key: str, value: str | None) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    config_path = bench_root / "sites" / "s.localhost" / "site_config.json"
    config_path.parent.mkdir(parents=True)
    original = {key: "https://admin.example.com"}
    config_path.write_text(json.dumps(original))

    response = client.patch("/api/v1/sites/s.localhost/configuration", json={key: value})

    assert response.status_code == 422
    assert json.loads(config_path.read_text()) == original


def test_site_detail_rejects_symlinked_site(tmp_path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "site_config.json").write_text(json.dumps({"installed_apps": []}))
    sites = bench_root / "sites"
    sites.mkdir()
    (sites / "linked.localhost").symlink_to(outside, target_is_directory=True)

    response = client.get("/api/v1/sites/linked.localhost")

    assert response.status_code == 404
