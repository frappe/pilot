from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tests.admin.backend.api.test_rename_and_domain_views import _client, _make_site


def test_complete_setup_queues_the_task_with_the_answers(tmp_path: Path) -> None:
    bench_root = tmp_path / "bench"
    client = _client(bench_root)
    _make_site(bench_root, "s.localhost")

    with patch("pilot.tasks.complete_setup.CompleteSetupTask.queue", return_value="task-1") as queue:
        client.post(
            "/api/v1/sites/s.localhost/actions/complete-setup",
            json={"full_name": "Prathamesh Kurunkar", "email": "prathamesh@example.com", "time_zone": "Asia/Kolkata"},
        )

    kwargs = queue.call_args.kwargs
    assert (kwargs["site"], kwargs["full_name"], kwargs["email"]) == (
        "s.localhost",
        "Prathamesh Kurunkar",
        "prathamesh@example.com",
    )
    assert kwargs["time_zone"] == "Asia/Kolkata"
    assert "country" not in kwargs
    assert kwargs["resource_key"] == "site:s.localhost"


def test_complete_setup_needs_a_name_and_an_email(tmp_path: Path) -> None:
    bench_root = tmp_path / "bench"
    client = _client(bench_root)
    _make_site(bench_root, "s.localhost")

    for body in ({"email": "prathamesh@example.com"}, {"full_name": "Prathamesh Kurunkar", "email": "prathamesh"}, {"full_name": 1}):
        response = client.post("/api/v1/sites/s.localhost/actions/complete-setup", json=body)
        assert response.status_code == 422


def test_complete_setup_passes_the_site_address(tmp_path: Path) -> None:
    bench_root = tmp_path / "bench"
    client = _client(bench_root)
    _make_site(bench_root, "s.localhost")

    with patch("pilot.tasks.complete_setup.CompleteSetupTask.queue", return_value="task-1") as queue:
        client.post(
            "/api/v1/sites/s.localhost/actions/complete-setup",
            json={
                "full_name": "Prathamesh Kurunkar",
                "email": "prathamesh@example.com",
                "host_name": "https://acme.example.com/",
            },
        )

    assert queue.call_args.kwargs["host_name"] == "https://acme.example.com"


def test_complete_setup_refuses_an_address_that_is_not_an_origin(tmp_path: Path) -> None:
    bench_root = tmp_path / "bench"
    client = _client(bench_root)
    _make_site(bench_root, "s.localhost")

    for host_name in (
        "acme.example.com",
        "ftp://acme.example.com",
        "https://acme.example.com/app",
        "https://",
    ):
        response = client.post(
            "/api/v1/sites/s.localhost/actions/complete-setup",
            json={"full_name": "Prathamesh Kurunkar", "email": "prathamesh@example.com", "host_name": host_name},
        )
        assert response.status_code == 422, host_name
