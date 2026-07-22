import json

from admin.backend.api.v1.sites.configuration import _public_config
from tests.admin.backend.test_admin_app import _client


def test_public_site_config_hides_system_and_secret_like_fields() -> None:
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

    assert _public_config(config) == {
        "developer_mode": 1,
        "maintenance_mode": 0,
        "pause_scheduler": 1,
        "frappe_branch": "version-16",
        "custom_provider": {
            "endpoint": "https://provider.example",
            "options": [{"region": "eu"}, "unchanged"],
        },
        "unreviewed_option": True,
    }


def test_public_site_config_copies_mutable_values() -> None:
    config = {"maintenance_mode": {"enabled": True}}

    public = _public_config(config)
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
