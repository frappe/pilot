from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from admin.backend.app import create_app
from pilot.core.admin_auth import ensure_jwt_secret, issue_token
from pilot.config.bench_toml_builder import BenchTomlBuilder
from pilot.core.ssh_keys import (
    InvalidSSHKeyError,
    LastSSHKeyError,
    SSHKeyAlreadyExistsError,
    SSHKeyNotFoundError,
)
from pilot.exceptions import (
    BenchError,
    ConfigError,
    DomainConflictError,
    DomainProviderError,
)


def _client(bench_root: Path):
    bench_root.mkdir(parents=True)
    (bench_root / "bench.toml").write_text(
        BenchTomlBuilder(
            bench_root.name,
            {"admin_enabled": True, "admin_password": "secret"},
        ).render()
    )
    secret = ensure_jwt_secret(bench_root / "bench.toml")
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", issue_token(secret))
    return client


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (InvalidSSHKeyError("bad key"), 422, "invalid_ssh_key"),
        (SSHKeyAlreadyExistsError("duplicate"), 409, "ssh_key_already_exists"),
    ],
)
def test_ssh_key_add_errors_have_distinct_statuses(
    tmp_path: Path,
    error: Exception,
    status: int,
    code: str,
) -> None:
    client = _client(tmp_path / "bench")
    with patch("admin.backend.api.v1.ssh_keys.AuthorizedKeysStore") as store:
        store.return_value.add.side_effect = error
        response = client.post(
            "/api/v1/ssh-keys",
            json={"public_key": "ssh-ed25519 AAAA"},
        )

    assert response.status_code == status
    assert response.get_json()["error"]["code"] == code


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (SSHKeyNotFoundError("missing"), 404, "ssh_key_not_found"),
        (LastSSHKeyError("last"), 409, "ssh_key_removal_rejected"),
    ],
)
def test_ssh_key_remove_errors_have_distinct_statuses(
    tmp_path: Path,
    error: Exception,
    status: int,
    code: str,
) -> None:
    client = _client(tmp_path / "bench")
    with patch("admin.backend.api.v1.ssh_keys.AuthorizedKeysStore") as store:
        store.return_value.remove.side_effect = error
        response = client.delete("/api/v1/ssh-keys/SHA256:value")

    assert response.status_code == status
    assert response.get_json()["error"]["code"] == code


def test_database_query_validates_types_before_execution(tmp_path: Path) -> None:
    response = _client(tmp_path / "bench").post(
        "/api/v1/database/queries",
        json={"site": "site.test", "query": ["select 1"]},
    )

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "invalid_query"


def test_database_runtime_errors_are_safe_server_failures(tmp_path: Path) -> None:
    client = _client(tmp_path / "bench")
    with patch(
        "pilot.core.database.make_site_database",
        side_effect=RuntimeError("secret connection detail"),
    ):
        response = client.post(
            "/api/v1/database/queries",
            json={"site": "site.test", "query": "select 1"},
        )

    assert response.status_code == 500
    assert response.get_json()["error"]["code"] == "query_failed"
    assert b"secret connection detail" not in response.data


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (DomainConflictError("duplicate"), 409, "domain_conflict"),
        (DomainProviderError("secret provider stderr"), 503, "domain_provider_unavailable"),
        (ConfigError("secret config detail"), 503, "configuration_unavailable"),
        (BenchError("secret internal detail"), 500, "internal_error"),
    ],
)
def test_domain_failures_preserve_their_semantics(
    tmp_path: Path,
    error: Exception,
    status: int,
    code: str,
) -> None:
    bench_root = tmp_path / "bench"
    client = _client(bench_root)
    site_dir = bench_root / "sites" / "site.test"
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text("{}")
    routes = Mock()
    routes.domains.side_effect = error

    with patch("admin.backend.api.v1.sites.domains._domain_routes", return_value=routes):
        response = client.get("/api/v1/sites/site.test/domains")

    assert response.status_code == status
    assert response.get_json()["error"]["code"] == code
    assert b"secret" not in response.data
