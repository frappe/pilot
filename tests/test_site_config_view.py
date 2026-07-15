import json

from admin.backend.views.sites import _public_config
from tests.test_admin_app import _client


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
        "unreviewed_option": True,
    }

    assert _public_config(config) == {
        "developer_mode": 1,
        "maintenance_mode": 0,
        "pause_scheduler": 1,
        "frappe_branch": "version-16",
        "unreviewed_option": True,
    }


def test_public_site_config_copies_mutable_values() -> None:
    config = {"maintenance_mode": {"enabled": True}}

    public = _public_config(config)
    public["maintenance_mode"]["enabled"] = False

    assert config["maintenance_mode"] == {"enabled": True}


def test_site_detail_preserves_custom_keys_without_exposing_secrets(tmp_path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    site_dir = bench_root / "sites" / "s.localhost"
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text(json.dumps({
        "installed_apps": [],
        "developer_mode": 1,
        "custom_app_mode": "strict",
        "future_provider_api_token": "unknown-secret",
    }))

    response = client.get("/api/v1/sites/s.localhost")

    assert response.status_code == 200
    assert response.get_json()["site"]["site_config"] == {
        "developer_mode": 1,
        "custom_app_mode": "strict",
    }
