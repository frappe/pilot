"""Tests for the notification feed endpoints under /api/v1/notifications."""

from __future__ import annotations

from pathlib import Path

from pilot.config import BenchConfig
from pilot.core.notification import NotificationStore


def _client(bench_root: Path, password: str = "secret"):
    from admin.backend.app import create_app
    from admin.backend.internal.session import Session
    from pilot.core.bench import Bench

    bench_root.mkdir(parents=True, exist_ok=True)
    (bench_root / "bench.toml").write_text(
        BenchConfig.from_flat(bench_root.name, {"admin_enabled": True, "admin_password": password}).dumps()
    )
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", Session(Bench(bench_root)).issue_session_token()[0])
    return client


def _seed(bench_root: Path, count: int, category: str = "Tasks") -> NotificationStore:
    store = NotificationStore(bench_root / "logs")
    for index in range(count):
        store.create(f"n{index}", category=category, event="task_failed", severity="Error")
    return store


def test_feed_returns_newest_first_with_the_unread_count(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    _seed(bench_root, 3)

    response = client.get("/api/v1/notifications")

    body = response.get_json()
    assert response.status_code == 200
    assert [item["title"] for item in body["data"]] == ["n2", "n1", "n0"]
    assert body["meta"]["unread"] == 3
    assert body["meta"]["next_cursor"] is None


def test_cursor_walks_the_whole_feed_without_repeats(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    _seed(bench_root, 5)

    titles: list[str] = []
    cursor = None
    for _ in range(10):
        params = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        body = client.get("/api/v1/notifications", query_string=params).get_json()
        titles.extend(item["title"] for item in body["data"])
        cursor = body["meta"]["next_cursor"]
        if not cursor:
            break

    assert titles == ["n4", "n3", "n2", "n1", "n0"]


def test_unknown_category_is_rejected(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    response = client.get("/api/v1/notifications", query_string={"category": "Billing"})

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_category"


def test_category_and_unread_filters_apply(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    _seed(bench_root, 2, category="Sites")
    store = _seed(bench_root, 2, category="Server")
    store.mark_read(store.list(1)[0].name)

    sites = client.get("/api/v1/notifications", query_string={"category": "Sites"}).get_json()
    unread = client.get("/api/v1/notifications", query_string={"unread_only": "1"}).get_json()

    assert [item["title"] for item in sites["data"]] == ["n1", "n0"]
    assert len(unread["data"]) == 3
    assert unread["meta"]["unread"] == 3


def test_mark_one_read_lowers_the_unread_count(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    store = _seed(bench_root, 3)
    newest = store.list(1)[0]

    response = client.post(f"/api/v1/notifications/{newest.name}/read")

    assert response.status_code == 204
    assert client.get("/api/v1/notifications").get_json()["meta"]["unread"] == 2


def test_mark_all_read_clears_the_badge(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    _seed(bench_root, 3)

    response = client.post("/api/v1/notifications/read-all")

    body = client.get("/api/v1/notifications").get_json()
    assert response.status_code == 204
    assert body["meta"]["unread"] == 0
    assert all(item["is_read"] for item in body["data"])


def test_feed_requires_a_session(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    client.delete_cookie("sid")

    assert client.get("/api/v1/notifications").status_code == 401


def test_webhook_test_pings_with_the_stored_token(tmp_path: Path) -> None:
    from unittest.mock import patch

    from pilot.config.common import CommonConfig
    from pilot.config.webhook import WebhookEndpoint
    from pilot.core.notification.webhooks import WebhookDelivery

    benches_root = tmp_path / "benches"
    common = CommonConfig()
    common.webhook_endpoints = [WebhookEndpoint("https://hooks.example.com", "tok-123")]
    benches_root.mkdir()
    common.write(benches_root)
    client = _client(benches_root / "current")
    sent = []

    def send(endpoint, body):
        sent.append((endpoint, body))
        return WebhookDelivery(202, "ok")

    with patch("pilot.core.notification.webhooks.send_webhook", send):
        response = client.post(
            "/api/v1/notifications/webhooks/test",
            json={
                "url": "https://hooks.example.com",
                "original_url": "https://hooks.example.com",
                "token": "",
            },
        )

    assert response.status_code == 200
    assert response.get_json() == {"status": 202, "body": "ok", "is_success": True}
    ((endpoint, body),) = sent
    assert endpoint.token == "tok-123"
    assert body["event"] == "ping"


def test_webhook_test_reports_an_unreachable_receiver(tmp_path: Path) -> None:
    from unittest.mock import patch

    client = _client(tmp_path / "benches" / "current")

    with patch("pilot.core.notification.webhooks.send_webhook", side_effect=OSError("connection refused")):
        response = client.post(
            "/api/v1/notifications/webhooks/test", json={"url": "https://down.example.com"}
        )

    assert response.status_code == 502
    assert "connection refused" in response.get_json()["error"]["message"]


def test_webhook_test_needs_a_web_url(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")

    assert client.post("/api/v1/notifications/webhooks/test", json={"url": ""}).status_code == 422
    assert client.post("/api/v1/notifications/webhooks/test", json={"url": "ftp://x"}).status_code == 422
