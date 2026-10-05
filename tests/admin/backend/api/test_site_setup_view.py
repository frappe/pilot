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
            json={"full_name": "Asha Rao", "email": "asha@example.com", "time_zone": "Asia/Kolkata"},
        )

    kwargs = queue.call_args.kwargs
    assert (kwargs["site"], kwargs["full_name"], kwargs["email"]) == (
        "s.localhost",
        "Asha Rao",
        "asha@example.com",
    )
    assert kwargs["time_zone"] == "Asia/Kolkata"
    assert "country" not in kwargs
    assert kwargs["resource_key"] == "site:s.localhost"


def test_complete_setup_needs_a_name_and_an_email(tmp_path: Path) -> None:
    bench_root = tmp_path / "bench"
    client = _client(bench_root)
    _make_site(bench_root, "s.localhost")

    for body in ({"email": "asha@example.com"}, {"full_name": "Asha Rao", "email": "asha"}, {"full_name": 1}):
        response = client.post("/api/v1/sites/s.localhost/actions/complete-setup", json=body)
        assert response.status_code == 422
