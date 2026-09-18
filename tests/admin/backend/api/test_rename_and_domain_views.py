from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from pilot.config import BenchConfig


def _client(bench_root: Path):
    from admin.backend.app import create_app
    from admin.backend.internal.session import Session
    from pilot.core.bench import Bench

    bench_root.mkdir(parents=True, exist_ok=True)
    (bench_root / "bench.toml").write_text(
        BenchConfig.from_flat(bench_root.name, {"admin_enabled": True, "admin_password": "secret"}).dumps()
    )
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", Session(Bench(bench_root)).issue_session_token()[0])
    return client


def _make_site(bench_root: Path, name: str, **config) -> None:
    site_dir = bench_root / "sites" / name
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text(json.dumps(config))


# --- rename -----------------------------------------------------------------


def test_rename_queues_the_task_with_both_names(tmp_path: Path) -> None:
    bench_root = tmp_path / "bench"
    client = _client(bench_root)
    _make_site(bench_root, "old.localhost")

    with patch("pilot.tasks.rename_site.RenameSiteTask.queue", return_value="task-1") as queue:
        client.post(
            "/api/v1/sites/old.localhost/actions/rename",
            json={"new_name": "new.localhost"},
        )

    queue.assert_called_once()
    kwargs = queue.call_args.kwargs
    assert kwargs["site"] == "old.localhost"
    assert kwargs["new_name"] == "new.localhost"
    assert set(kwargs["resource_key"]) == {
        "site:old.localhost",
        "site:new.localhost",
        "host:old.localhost",
        "host:new.localhost",
    }


def test_rename_of_an_unknown_site_is_a_404(tmp_path: Path) -> None:
    bench_root = tmp_path / "bench"
    client = _client(bench_root)

    response = client.post(
        "/api/v1/sites/missing.localhost/actions/rename", json={"new_name": "new.localhost"}
    )

    assert response.status_code == 404


def test_rename_rejects_a_name_that_is_not_a_hostname(tmp_path: Path) -> None:
    bench_root = tmp_path / "bench"
    client = _client(bench_root)
    _make_site(bench_root, "old.localhost")

    response = client.post("/api/v1/sites/old.localhost/actions/rename", json={"new_name": "not a hostname"})

    assert response.status_code == 422


def test_rename_onto_an_existing_site_conflicts(tmp_path: Path) -> None:
    bench_root = tmp_path / "bench"
    client = _client(bench_root)
    _make_site(bench_root, "old.localhost")
    _make_site(bench_root, "taken.localhost")

    response = client.post("/api/v1/sites/old.localhost/actions/rename", json={"new_name": "taken.localhost"})

    assert response.status_code == 409


def test_rename_needs_a_new_name(tmp_path: Path) -> None:
    bench_root = tmp_path / "bench"
    client = _client(bench_root)
    _make_site(bench_root, "old.localhost")

    assert client.post("/api/v1/sites/old.localhost/actions/rename", json={}).status_code == 422


# --- admin domain -----------------------------------------------------------


def test_admin_domain_change_queues_the_task(tmp_path: Path) -> None:
    client = _client(tmp_path / "bench")

    with patch("pilot.tasks.change_admin_domain.ChangeAdminDomainTask.queue", return_value="task-2") as queue:
        client.post("/api/v1/settings/admin-domain", json={"domain": "Admin.Example.COM"})

    queue.assert_called_once()
    kwargs = queue.call_args.kwargs
    assert kwargs["domain"] == "admin.example.com"  # normalized
    assert kwargs["tls"] is None
    # The admin key keeps two moves apart; the host key is the one a rename or a
    # new site claiming the same hostname would also take.
    assert kwargs["resource_key"] == ["admin-domain", "host:admin.example.com"]


def test_admin_domain_change_passes_tls_through(tmp_path: Path) -> None:
    client = _client(tmp_path / "bench")

    with patch("pilot.tasks.change_admin_domain.ChangeAdminDomainTask.queue", return_value="task-3") as queue:
        client.post("/api/v1/settings/admin-domain", json={"domain": "admin.example.com", "tls": True})

    assert queue.call_args.kwargs["tls"] is True


def test_admin_domain_change_locks_the_previous_hostname(tmp_path: Path) -> None:
    bench_root = tmp_path / "bench"
    client = _client(bench_root)
    with BenchConfig.open(bench_root) as config:
        config.admin.domain = "old-admin.example.com"

    with patch("pilot.tasks.change_admin_domain.ChangeAdminDomainTask.queue", return_value="task-4") as queue:
        client.post("/api/v1/settings/admin-domain", json={"domain": "new-admin.example.com"})

    assert set(queue.call_args.kwargs["resource_key"]) == {
        "admin-domain",
        "host:old-admin.example.com",
        "host:new-admin.example.com",
    }


def test_admin_domain_rejects_a_bad_hostname(tmp_path: Path) -> None:
    client = _client(tmp_path / "bench")

    response = client.post("/api/v1/settings/admin-domain", json={"domain": "not a hostname"})

    assert response.status_code == 422


def test_admin_domain_rejects_a_non_boolean_tls(tmp_path: Path) -> None:
    client = _client(tmp_path / "bench")

    response = client.post(
        "/api/v1/settings/admin-domain", json={"domain": "admin.example.com", "tls": "yes"}
    )

    assert response.status_code == 422


def test_a_rename_and_an_admin_move_to_one_hostname_share_a_resource(tmp_path: Path) -> None:
    """Keyed in different namespaces, both could be queued at once, each having
    validated against a state the other was about to change."""
    bench_root = tmp_path / "bench"
    client = _client(bench_root)
    _make_site(bench_root, "old.localhost")

    with patch("pilot.tasks.rename_site.RenameSiteTask.queue", return_value="t1") as rename:
        client.post("/api/v1/sites/old.localhost/actions/rename", json={"new_name": "shared.example.com"})
    with patch("pilot.tasks.change_admin_domain.ChangeAdminDomainTask.queue", return_value="t2") as move:
        client.post("/api/v1/settings/admin-domain", json={"domain": "shared.example.com"})

    assert set(rename.call_args.kwargs["resource_key"]) & set(move.call_args.kwargs["resource_key"])


def test_a_hostname_conflict_on_an_admin_move_is_a_conflict_not_a_server_error(tmp_path: Path) -> None:
    """The hostname resource is shared with renames and new sites, so an active
    task holding it is an ordinary conflict - the same 409 those routes give."""
    from pilot.exceptions import TaskConflictError

    client = _client(tmp_path / "bench")

    with patch(
        "pilot.tasks.change_admin_domain.ChangeAdminDomainTask.queue",
        side_effect=TaskConflictError("host:admin.example.com is taken"),
    ):
        response = client.post("/api/v1/settings/admin-domain", json={"domain": "admin.example.com"})

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "task_conflict"


def test_an_invalid_admin_move_is_reported_as_invalid(tmp_path: Path) -> None:
    client = _client(tmp_path / "bench")

    with patch(
        "pilot.tasks.change_admin_domain.ChangeAdminDomainTask.queue",
        side_effect=ValueError("unknown task argument"),
    ):
        response = client.post("/api/v1/settings/admin-domain", json={"domain": "admin.example.com"})

    assert response.status_code == 422


def test_an_unexpected_queue_failure_is_logged(tmp_path: Path, caplog) -> None:
    """A generic 500 is all the caller sees, so the cause has to reach the log."""
    client = _client(tmp_path / "bench")

    with patch(
        "pilot.tasks.change_admin_domain.ChangeAdminDomainTask.queue",
        side_effect=RuntimeError("disk exploded"),
    ), caplog.at_level("ERROR"):
        response = client.post("/api/v1/settings/admin-domain", json={"domain": "admin.example.com"})

    assert response.status_code == 500
    assert "disk exploded" in caplog.text


def test_a_claim_that_cannot_be_read_is_not_treated_as_a_free_name(tmp_path: Path) -> None:
    """Answering "free" because the check itself failed is what lets two vhosts
    claim one hostname; the caller must see the failure instead."""
    import pytest

    from admin.backend.api.v1.sites.shared import new_site_name_error
    from pilot.core.bench import Bench

    bench_root = tmp_path / "bench"
    _client(bench_root)

    with patch.object(Bench, "site_claiming", side_effect=OSError("bench.toml is unreadable")), pytest.raises(
        OSError
    ):
        new_site_name_error(bench_root, "wanted.localhost")
