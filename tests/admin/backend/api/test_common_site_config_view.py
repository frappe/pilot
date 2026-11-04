import json

from pilot.core.site.config import SECRET_MASK
from tests.admin.backend.test_admin_app import _client


def _write(bench_root, config: dict) -> None:
    sites = bench_root / "sites"
    sites.mkdir(parents=True, exist_ok=True)
    (sites / "common_site_config.json").write_text(json.dumps(config))


def _read(bench_root) -> dict:
    return json.loads((bench_root / "sites" / "common_site_config.json").read_text())


def test_get_hides_managed_keys_and_masks_secrets(tmp_path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    _write(
        bench_root,
        {
            "redis_cache": "redis://localhost:13000",
            "mail_password": "x",
            "s3_secret_key": "y",
            "encryption_key": "z",
            "db_password": "p",
            "allow_cors": "*",
        },
    )

    response = client.get("/api/v1/settings/common-site-config")

    assert response.status_code == 200
    assert response.get_json() == {"encryption_key": SECRET_MASK, "allow_cors": "*"}


def test_patch_sets_and_removes_keys_and_keeps_the_rest(tmp_path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    _write(bench_root, {"redis_queue": "redis://localhost:11000", "allow_cors": "*", "db_password": "p"})

    response = client.patch(
        "/api/v1/settings/common-site-config", json={"allow_cors": None, "max_file_size": 104857600}
    )

    assert response.status_code == 200
    assert response.get_json() == {"max_file_size": 104857600}
    assert _read(bench_root) == {
        "redis_queue": "redis://localhost:11000",
        "db_password": "p",
        "max_file_size": 104857600,
    }


def test_patch_refuses_managed_and_protected_keys(tmp_path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    _write(bench_root, {"webserver_port": 8000})

    managed = client.patch("/api/v1/settings/common-site-config", json={"webserver_port": 9999})
    s3_secret = client.patch("/api/v1/settings/common-site-config", json={"s3_secret_key": "k"})
    protected = client.patch("/api/v1/settings/common-site-config", json={"db_password": "p"})

    assert managed.status_code == 422
    assert "webserver_port" in managed.get_json()["error"]["message"]
    assert s3_secret.status_code == 422
    assert protected.status_code == 422
    assert _read(bench_root) == {"webserver_port": 8000}


def test_patch_sets_a_secret_without_showing_it(tmp_path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    _write(bench_root, {})

    response = client.patch("/api/v1/settings/common-site-config", json={"encryption_key": "k"})

    assert response.get_json() == {"encryption_key": SECRET_MASK}
    assert _read(bench_root) == {"encryption_key": "k"}


def test_damaged_file_is_reported_not_rewritten(tmp_path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    (bench_root / "sites").mkdir(parents=True, exist_ok=True)
    (bench_root / "sites" / "common_site_config.json").write_text("{broken")

    response = client.patch("/api/v1/settings/common-site-config", json={"allow_cors": "*"})

    assert response.status_code == 422
    assert (bench_root / "sites" / "common_site_config.json").read_text() == "{broken"


def test_managed_keys_are_listed_for_the_editor(tmp_path) -> None:
    client = _client(tmp_path / "benches" / "current")

    keys = client.get("/api/v1/settings/common-site-config/managed-keys").get_json()

    assert "webserver_port" in keys
    assert "mail_server" in keys
    assert keys == sorted(keys)
