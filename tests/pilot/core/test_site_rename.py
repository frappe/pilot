"""A rename must carry every name-bound piece of state to the new hostname."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from pilot.config import BenchConfig
from pilot.core.bench import Bench
from pilot.core.site.rename import SiteRename

OLD = "old.example.com"
NEW = "new.example.com"


def _bench(tmp_path: Path) -> Bench:
    # Nested so host_owner's sibling-bench scan stays inside this test's tmp dir.
    bench_root = tmp_path / "benches" / "current"
    config = BenchConfig._from_dict(
        {
            "bench": {"name": "test-bench", "python": "3.14"},
            "apps": [
                {"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}
            ],
            "mariadb": {"root_password": "root"},
            "redis": {"cache_port": 13000, "queue_port": 11000},
            "sites": [{"name": OLD, "apps": ["frappe"]}],
        }
    )
    bench_root.mkdir(parents=True)
    (bench_root / "bench.toml").write_text(
        config.dumps() + f'\n[[sites]]\nname = "{OLD}"\napps = ["frappe"]\n'
    )
    site_dir = bench_root / "sites" / OLD
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text(json.dumps({"pilot_auth_token": "stale-token"}))
    return Bench(config, bench_root)


def test_rename_moves_name_bound_state(tmp_path: Path) -> None:
    bench = _bench(tmp_path)

    with (
        patch("pilot.managers.cron.CronManager") as cron_class,
        patch("pilot.managers.nginx.NginxManager"),
        patch("pilot.core.adapters.domain_provider.DomainRouteProvider") as routes_class,
        patch("admin.backend.internal.session.Session") as session_class,
    ):
        cron = cron_class.return_value
        cron.get_schedule.return_value = "0 3 * * *"
        session_class.return_value.issue_site_token.return_value = "fresh-token"
        routes_class.wildcard_domains.return_value = ["*.example.com"]

        SiteRename(bench.site(OLD), NEW).run(lambda message: None)

    bench_root = tmp_path / "benches" / "current"
    assert not (bench_root / "sites" / OLD).exists()
    new_config = json.loads((bench_root / "sites" / NEW / "site_config.json").read_text())
    assert new_config["pilot_auth_token"] == "fresh-token"
    session_class.return_value.issue_site_token.assert_called_once()
    assert session_class.return_value.issue_site_token.call_args.args[0] == NEW

    cron.remove_schedule.assert_called_once_with(OLD)
    schedule_args = cron.set_schedule.call_args.args
    assert schedule_args[0] == NEW
    assert schedule_args[1] == "0 3 * * *"
    assert NEW in schedule_args[2]

    routes = routes_class.return_value
    routes.release.assert_called_once_with(OLD)
    routes.register.assert_called_once_with(NEW, NEW)

    raw = (bench_root / "bench.toml").read_text()
    assert NEW in raw and OLD not in raw


def test_rename_skips_schedule_and_token_when_absent(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    site_config = tmp_path / "benches" / "current" / "sites" / OLD / "site_config.json"
    site_config.write_text(json.dumps({}))

    with (
        patch("pilot.managers.cron.CronManager") as cron_class,
        patch("pilot.managers.nginx.NginxManager"),
        patch("pilot.core.adapters.domain_provider.DomainRouteProvider") as routes_class,
        patch("admin.backend.internal.session.Session") as session_class,
    ):
        cron_class.return_value.get_schedule.return_value = None
        routes_class.wildcard_domains.return_value = []

        SiteRename(bench.site(OLD), NEW).run(lambda message: None)

    cron_class.return_value.set_schedule.assert_not_called()
    session_class.return_value.issue_site_token.assert_not_called()


def test_rejected_provider_registration_keeps_the_old_route(tmp_path: Path) -> None:
    from pilot.exceptions import BenchError

    bench = _bench(tmp_path)
    messages = []

    with (
        patch("pilot.managers.cron.CronManager") as cron_class,
        patch("pilot.managers.nginx.NginxManager"),
        patch("pilot.core.adapters.domain_provider.DomainRouteProvider") as routes_class,
        patch("admin.backend.internal.session.Session") as session_class,
    ):
        cron_class.return_value.get_schedule.return_value = None
        session_class.return_value.issue_site_token.return_value = "fresh-token"
        routes_class.wildcard_domains.return_value = ["*.example.com"]
        routes = routes_class.return_value
        routes.register.side_effect = BenchError("provider says no")

        SiteRename(bench.site(OLD), NEW).run(messages.append)

    routes.release.assert_not_called()
    assert any("register" in m for m in messages)


def test_rename_requires_a_wildcard_match_on_managed_hosting(tmp_path: Path) -> None:
    import pytest

    from pilot.exceptions import BenchError

    bench = _bench(tmp_path)

    with patch(
        "pilot.core.adapters.domain_provider.DomainRouteProvider.wildcard_domains",
        return_value=["*.pilot.dev"],
    ), pytest.raises(BenchError, match="wildcard"):
        SiteRename(bench.site(OLD), NEW).run(lambda message: None)

    assert (tmp_path / "benches" / "current" / "sites" / OLD).exists()


def test_failed_token_write_leaves_site_config_intact(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    messages = []

    with (
        patch("pilot.managers.cron.CronManager") as cron_class,
        patch("pilot.managers.nginx.NginxManager"),
        patch("pilot.core.adapters.domain_provider.DomainRouteProvider") as routes_class,
        patch("admin.backend.internal.session.Session") as session_class,
        patch("pilot.core.site.rename.write_private_text", side_effect=OSError("disk full")),
    ):
        cron_class.return_value.get_schedule.return_value = None
        routes_class.wildcard_domains.return_value = []
        session_class.return_value.issue_site_token.return_value = "fresh-token"

        SiteRename(bench.site(OLD), NEW).run(messages.append)

    config_path = tmp_path / "benches" / "current" / "sites" / NEW / "site_config.json"
    assert json.loads(config_path.read_text()) == {"pilot_auth_token": "stale-token"}
    assert any("issue-site-token" in m for m in messages)
