from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from flask import Flask

from admin.backend.api.v1.settings import settings_bp
from pilot.config import BenchConfig


def _client(bench_root: Path, production: bool = True):
    bench_root.mkdir()
    config = BenchConfig.from_flat(
        bench_root.name, {"admin_domain": "admin.example.com", "admin_password": "secret"}
    )
    config.production.enabled = production
    config.production.process_manager = "systemd"
    (bench_root / "bench.toml").write_text(config.dumps())
    app = Flask(__name__)
    app.config["BENCH_ROOT"] = bench_root
    app.register_blueprint(settings_bp, url_prefix="/api/v1/settings")
    return app.test_client()


def test_enabling_cloudflare_saves_the_switch_and_rewrites_nginx(tmp_path: Path) -> None:
    client = _client(tmp_path / "bench")
    with patch("pilot.core.bench.settings.regenerate_nginx") as regenerate_nginx:
        response = client.patch("/api/v1/settings", json={"proxy": {"cloudflare": True}})

    assert response.status_code == 200
    regenerate_nginx.assert_called_once()
    assert BenchConfig.read(tmp_path / "bench").proxy.cloudflare is True
    assert client.get("/api/v1/settings").get_json()["proxy"] == {"cloudflare": True}


def test_the_cloudflare_switch_rewrites_nginx_for_every_production_bench(tmp_path: Path) -> None:
    client = _client(tmp_path / "bench", production=False)
    _client(tmp_path / "sibling")
    _client(tmp_path / "staging", production=False)
    with patch("pilot.core.bench.settings.regenerate_nginx") as regenerate_nginx:
        response = client.patch("/api/v1/settings", json={"proxy": {"cloudflare": True}})

    assert response.status_code == 200
    assert [call.args[0].path.name for call in regenerate_nginx.call_args_list] == ["sibling"]


def test_the_cloudflare_switch_reports_an_unreadable_bench(tmp_path: Path) -> None:
    client = _client(tmp_path / "bench")
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "bench.toml").write_text("not = [valid")
    with patch("pilot.core.bench.settings.regenerate_nginx"):
        response = client.patch("/api/v1/settings", json={"proxy": {"cloudflare": True}})

    assert response.status_code == 500
    assert response.get_json()["error"]["details"] == {"saved": True}
    assert "'broken'" in response.get_json()["error"]["message"]


def test_an_unchanged_cloudflare_switch_leaves_nginx_alone(tmp_path: Path) -> None:
    client = _client(tmp_path / "bench")
    with patch("pilot.core.bench.settings.regenerate_nginx") as regenerate_nginx:
        response = client.patch("/api/v1/settings", json={"proxy": {"cloudflare": False}})

    assert response.status_code == 200
    regenerate_nginx.assert_not_called()


def test_cloudflare_status_reports_the_edge_proxy_and_proxied_domains(tmp_path: Path) -> None:
    client = _client(tmp_path / "bench")
    site = type(
        "Site",
        (),
        {"config": type("Config", (), {"all_domains": ["shop.example.com", "direct.example.com"]})},
    )
    addresses = {"shop.example.com": "104.21.76.133", "direct.example.com": "203.0.113.7"}

    def getaddrinfo(host, port):
        return [(None, None, None, "", (addresses[host], 0))]

    with (
        patch("pilot.core.bench.Bench.sites", return_value=[site]),
        patch(
            "pilot.core.adapters.domain_provider.DomainRouteProvider.proxy_servers",
            return_value=["fdaa:1::/64"],
        ),
        patch("pilot.core.bench.settings.socket.getaddrinfo", side_effect=getaddrinfo),
    ):
        response = client.get("/api/v1/settings/cloudflare")

    assert response.get_json() == {
        "has_edge_proxy": True,
        "domains": [
            {"domain": "direct.example.com", "is_behind_cloudflare": False},
            {"domain": "shop.example.com", "is_behind_cloudflare": True},
        ],
    }
