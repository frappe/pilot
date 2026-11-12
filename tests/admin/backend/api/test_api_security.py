"""Every API route checks who is calling, and no caller can steer a request somewhere else."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from admin.backend.app import create_app
from admin.backend.internal.session import Session
from admin.backend.middleware import AuthPolicy, get_auth_policy
from pilot.config import BenchConfig
from pilot.core.bench import Bench
from pilot.integrations.central import CentralClient

ROUTE_PARAMETER = re.compile(r"<(?:\w+:)?(\w+)>")


@pytest.fixture
def bench_root(tmp_path: Path) -> Path:
    root = tmp_path / "bench"
    (root / "apps").mkdir(parents=True)
    for site in ("a.local", "b.local"):
        (root / "sites" / site).mkdir(parents=True)
        (root / "sites" / site / "site_config.json").write_text(json.dumps({"db_name": site}))
    (root / "bench.toml").write_text(
        BenchConfig.from_flat("bench", {"admin_enabled": True, "admin_password": "secret"}).dumps()
    )
    return root


@pytest.fixture
def app(bench_root: Path):
    app = create_app(bench_root)
    app.config["TESTING"] = True
    return app


def _protected_requests(app):
    """(method, url) for every authenticated API route, aimed at site `b.local`."""
    for rule in app.url_map.iter_rules():
        if (
            not rule.rule.startswith("/api/")
            or get_auth_policy(app.view_functions[rule.endpoint]) == AuthPolicy.OPEN
        ):
            continue
        url = ROUTE_PARAMETER.sub(lambda match: "b.local" if match.group(1) == "name" else "x1", rule.rule)
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            yield method, url


def test_every_protected_route_refuses_anonymous_and_other_site_callers(app, bench_root: Path) -> None:
    client = app.test_client()
    site_a = {"Authorization": f"Bearer {Session(Bench(bench_root)).issue_site_token('a.local')}"}
    requests = list(_protected_requests(app))
    assert len(requests) > 150

    anonymous = [
        (m, u, s) for m, u in requests if (s := client.open(u, method=m, json={}).status_code) != 401
    ]
    other_site = [
        (m, u, s)
        for m, u in requests
        if (s := client.open(u, method=m, json={}, headers=site_a).status_code) != 403
    ]

    assert anonymous == []
    assert other_site == []


@pytest.mark.parametrize(
    "method_path",
    [
        "central.billing.api.billing_api.x/../../resource/User",
        "central.billing.api.billing_api.get?cmd=frappe.client.get_list",
        "central.billing.api.billing_api.get#fragment",
        "central.api.pilot.heartbeat/../../resource/User",
        "central.billing.api.billing_api.",
    ],
)
def test_central_proxy_refuses_paths_that_reach_other_methods(
    app, bench_root: Path, monkeypatch, method_path
) -> None:
    forwarded = []
    monkeypatch.setattr(CentralClient, "forward", lambda self, *args: forwarded.append(args))
    client = app.test_client()
    client.set_cookie("sid", Session(Bench(bench_root)).issue_session_token()[0])

    response = client.post(
        f"/api/v1/sites/a.local/central/{method_path.replace('?', '%3F').replace('#', '%23')}", json={}
    )

    assert response.status_code == 403
    assert forwarded == []


def test_cross_site_requests_cannot_use_the_session_cookie(app, bench_root: Path) -> None:
    session = Session(Bench(bench_root))
    client = app.test_client()
    client.set_cookie("sid", session.issue_session_token()[0])
    url = "/api/v1/sites/a.local/actions/clear-cache"

    for site in ("cross-site", "same-site"):
        response = client.post(url, headers={"Sec-Fetch-Site": site}, data="x=1")
        assert response.status_code == 403
        assert response.get_json()["error"]["code"] == "cross_site_request"
        empty_bearer = client.post(url, headers={"Sec-Fetch-Site": site, "Authorization": "Bearer "})
        assert empty_bearer.status_code == 403

    assert client.get("/api/v1/sites/a.local", headers={"Sec-Fetch-Site": "cross-site"}).status_code != 403
    bearer = {"Sec-Fetch-Site": "cross-site", "Authorization": f"Bearer {session.issue_session_token()[0]}"}
    assert client.post(url, headers=bearer).status_code != 403
    assert client.post(url, headers={"Sec-Fetch-Site": "same-origin"}).status_code != 403
