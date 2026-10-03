import shlex
import subprocess
from pathlib import Path

import pytest

from pilot.config import (
    AppConfig,
    BenchConfig,
    MariaDBConfig,
    RedisConfig,
    SiteConfig,
    WorkerConfig,
    WorkerGroup,
)
from pilot.core.app import App, RevisionPin
from pilot.core.bench import Bench
from pilot.core.server import Server
from pilot.core.site import Site
from pilot.exceptions import BenchError
from pilot.internal.git import GitRepo
from pilot.managers.processes.local import ProcessDefinition, ProcessManager

FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures"


def make_bench(tmp_path: Path) -> Bench:
    config = BenchConfig(
        name="test-bench",
        python_version="3.14",
        apps=[
            AppConfig(name="frappe", repo="https://github.com/frappe/frappe", branch="version-16"),
        ],
        mariadb=MariaDBConfig(root_password="root"),
        redis=RedisConfig(cache_port=13000, queue_port=11000),
        workers=WorkerConfig(
            groups=[
                WorkerGroup(queues=["default"], count=2),
                WorkerGroup(queues=["short"], count=1),
                WorkerGroup(queues=["long"], count=1),
            ]
        ),
    )
    return Bench(config, tmp_path)


def _write_bench_toml(bench_dir: Path, name: str) -> None:
    bench_dir.mkdir(parents=True)
    (bench_dir / "bench.toml").write_text(BenchConfig.from_flat(name).dumps())


def test_bench_loads_from_path(tmp_path: Path) -> None:
    bench_dir = tmp_path / "benches" / "alpha"
    _write_bench_toml(bench_dir, "alpha")

    bench = Bench(bench_dir)

    assert bench.path == bench_dir
    assert bench.config.name == "alpha"


def test_bench_loads_name_from_known_benches_dir(tmp_path: Path, monkeypatch) -> None:
    bench_dir = tmp_path / "benches" / "alpha"
    _write_bench_toml(bench_dir, "alpha")
    monkeypatch.setattr("pilot.utils.cli_root", lambda: tmp_path)

    bench = Bench("alpha")

    assert bench.path == bench_dir
    assert bench.config.name == "alpha"


def test_server_resolves_bench_by_name(tmp_path: Path, monkeypatch) -> None:
    bench_dir = tmp_path / "benches" / "alpha"
    _write_bench_toml(bench_dir, "alpha")
    monkeypatch.setattr("pilot.utils.cli_root", lambda: tmp_path)

    bench = Server().bench("alpha")

    assert bench.path == bench_dir
    assert bench.config.name == "alpha"


def test_app_is_cloned_returns_false_for_nonexistent_path(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    app_config = AppConfig(name="frappe", repo="https://example.com/frappe", branch="main")
    app = App(app_config, bench)
    assert app.is_cloned is False


def test_app_is_cloned_returns_false_when_no_git_directory(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    app_config = AppConfig(name="myapp", repo="https://example.com/myapp", branch="main")
    app = App(app_config, bench)
    app.path.mkdir(parents=True)
    assert app.is_cloned is False


def test_app_is_cloned_returns_true_when_git_directory_exists(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    app_config = AppConfig(name="myapp", repo="https://example.com/myapp", branch="main")
    app = App(app_config, bench)
    app.path.mkdir(parents=True)
    (app.path / ".git").mkdir()
    assert app.is_cloned is True


def _init_git_repo(path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)


def _commit(path: Path, message: str) -> None:
    import subprocess

    (path / "f").write_text(message)
    subprocess.run(["git", "-C", str(path), "add", "f"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", message], check=True)


def _tag(path: Path, tag: str) -> None:
    import subprocess

    subprocess.run(["git", "-C", str(path), "tag", tag], check=True)


def test_revision_pin_from_marketplace_release_is_a_commit() -> None:
    pin = RevisionPin.from_marketplace_release({"version": "1.0.0", "branch": "main", "commit": "abc123"})
    assert pin == RevisionPin(kind="commit", ref="abc123")


def test_revision_pin_from_marketplace_release_without_commit_is_none() -> None:
    # Nothing to pin to - a release that advertises no commit is unusable.
    assert RevisionPin.from_marketplace_release({"version": "1.0.0", "branch": "main"}) is None


def test_app_is_on_revision_tag_matches(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    app = App(AppConfig(name="myapp", repo="r", branch=""), bench)
    app.path.mkdir(parents=True)
    _init_git_repo(app.path)
    _commit(app.path, "c1")
    _tag(app.path, "v1.0.0")

    assert app.is_on_revision(RevisionPin(kind="tag", ref="v1.0.0")) is True


def test_app_is_on_revision_tag_mismatch(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    app = App(AppConfig(name="myapp", repo="r", branch=""), bench)
    app.path.mkdir(parents=True)
    _init_git_repo(app.path)
    _commit(app.path, "c1")
    _tag(app.path, "v1.0.0")

    # Marketplace has since moved to a newer tag the app hasn't been updated to.
    assert app.is_on_revision(RevisionPin(kind="tag", ref="v2.0.0")) is False


def test_app_is_on_revision_no_tag_at_head(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    app = App(AppConfig(name="myapp", repo="r", branch=""), bench)
    app.path.mkdir(parents=True)
    _init_git_repo(app.path)
    _commit(app.path, "c1")  # HEAD has no tag

    assert app.is_on_revision(RevisionPin(kind="tag", ref="v1.0.0")) is False


def test_app_is_on_revision_commit_prefix_matches(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    app = App(AppConfig(name="myapp", repo="r", branch=""), bench)
    app.path.mkdir(parents=True)
    _init_git_repo(app.path)
    _commit(app.path, "c1")
    full_sha = app.installed_hash

    # Registry may store an abbreviated hash; a full HEAD sha starting with it counts.
    assert app.is_on_revision(RevisionPin(kind="commit", ref=full_sha[:8])) is True


def test_app_is_on_revision_commit_mismatch(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    app = App(AppConfig(name="myapp", repo="r", branch=""), bench)
    app.path.mkdir(parents=True)
    _init_git_repo(app.path)
    _commit(app.path, "c1")

    assert app.is_on_revision(RevisionPin(kind="commit", ref="0" * 40)) is False


def test_app_update_target_is_none_without_tracked_branch(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    app = App(AppConfig(name="myapp", repo="r", branch=""), bench)
    app.path.mkdir(parents=True)
    _init_git_repo(app.path)
    _commit(app.path, "c1")

    # No configured remote at all - must not crash, and an app with no branch
    # has no tip to compare against.
    _publish(None)

    assert app.update_target() is None


def _clone_at_tag(remote: Path, clone_dir: Path, tag: str, shallow: bool = True) -> None:
    import subprocess

    subprocess.run(["git", "clone", "-q", str(remote), str(clone_dir)], check=True)
    if shallow:
        subprocess.run(
            ["git", "-C", str(clone_dir), "fetch", "-q", "origin", tag, "--depth", "1"], check=True
        )
    subprocess.run(["git", "-C", str(clone_dir), "checkout", "-q", tag], check=True)


def test_sync_remote_url_scrubs_a_token_an_older_clone_left_in_origin(tmp_path: Path) -> None:
    """.git/config is world-readable in a bench directory, so no token may live there.
    Requests are authenticated by git config passed per call instead."""
    bench = make_bench(tmp_path)
    repo_url = "https://github.com/frappe/myapp"
    app = App(AppConfig(name="myapp", repo=repo_url, branch="master"), bench)
    app.path.mkdir(parents=True)
    _init_git_repo(app.path)
    embedded = "https://x-access-token:stale-token@github.com/frappe/myapp"
    subprocess.run(["git", "-C", str(app.path), "remote", "add", "origin", embedded], check=True)

    app._repository._sync_remote_url()

    origin_url = subprocess.run(
        ["git", "-C", str(app.path), "remote", "get-url", "origin"], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert origin_url == repo_url
    assert "stale-token" not in origin_url


def test_sync_remote_url_leaves_origin_untouched_without_stored_token(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    app = App(AppConfig(name="myapp", repo="https://github.com/frappe/myapp", branch="master"), bench)
    app.path.mkdir(parents=True)
    _init_git_repo(app.path)
    original_url = "https://example.com/some/other/path.git"
    subprocess.run(["git", "-C", str(app.path), "remote", "add", "origin", original_url], check=True)

    app._repository._sync_remote_url()

    origin_url = subprocess.run(
        ["git", "-C", str(app.path), "remote", "get-url", "origin"], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert origin_url == original_url


def test_app_update_with_tag_target_checks_out_advertised_tag_not_latest(tmp_path: Path) -> None:
    remote = tmp_path / "remote"
    remote.mkdir()
    _init_git_repo(remote)
    _commit(remote, "c1")
    _tag(remote, "v1.0.0")
    _commit(remote, "c2")
    _tag(remote, "v2.0.0")  # the repo's actual latest tag
    _commit(remote, "c3")
    _tag(remote, "v3.0.0")  # marketplace hasn't advertised this one yet

    bench = make_bench(tmp_path)
    app = App(AppConfig(name="myapp", repo="r", branch=""), bench)
    _clone_at_tag(remote, app.path, "v1.0.0")

    # Marketplace's next advertised pin is v2.0.0 - not the repo's true latest (v3.0.0).
    app.update(pin=RevisionPin(kind="tag", ref="v2.0.0"))

    assert app.is_on_revision(RevisionPin(kind="tag", ref="v2.0.0")) is True
    assert app.is_on_revision(RevisionPin(kind="tag", ref="v3.0.0")) is False


def test_app_update_with_commit_target_checks_out_advertised_commit(tmp_path: Path) -> None:
    remote = tmp_path / "remote"
    remote.mkdir()
    _init_git_repo(remote)
    _commit(remote, "c1")
    _tag(remote, "v1.0.0")
    _commit(remote, "c2")

    bench = make_bench(tmp_path)
    app = App(AppConfig(name="myapp", repo="r", branch=""), bench)
    _clone_at_tag(remote, app.path, "v1.0.0")

    import subprocess

    target_sha = subprocess.run(
        ["git", "-C", str(remote), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()

    app.update(pin=RevisionPin(kind="commit", ref=target_sha))

    assert app.installed_hash == target_sha


def test_app_update_with_commit_target_stays_on_configured_branch(tmp_path: Path) -> None:
    """A pinned-commit update should land at that commit without detaching HEAD."""
    remote = tmp_path / "remote"
    remote.mkdir()
    _init_git_repo(remote)
    _commit(remote, "c1")
    _commit(remote, "c2")

    import subprocess

    target_sha = subprocess.run(
        ["git", "-C", str(remote), "rev-parse", "HEAD^"], capture_output=True, text=True, check=True
    ).stdout.strip()

    bench = make_bench(tmp_path)
    app = App(AppConfig(name="myapp", repo="r", branch="develop"), bench)
    subprocess.run(["git", "clone", "-q", str(remote), str(app.path)], check=True)
    subprocess.run(["git", "-C", str(app.path), "checkout", "-q", "-B", "develop"], check=True)

    app.update(pin=RevisionPin(kind="commit", ref=target_sha))

    assert app.installed_hash == target_sha
    assert GitRepo(app.path).branch == "develop"

    assert app.installed_hash == target_sha


def _publish(entry: dict | None) -> None:
    """Make the marketplace registry advertise `entry`, and nothing else."""
    from tests.pilot.marketplace_registry import publish

    publish([entry] if entry else [])


def test_app_has_marketplace_update_false_when_advertised_commit_is_checked_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app_on_branch(tmp_path, "https://github.com/frappe/myapp")
    monkeypatch.setattr("pilot.core.app.installed_app_version", lambda *_: "1.0.0")
    entry = {
        "name": "myapp",
        "repo": "https://github.com/frappe/myapp",
        "releases": [{"version": "1.0.0", "branch": "main", "commit": app.installed_hash}],
    }

    _publish(entry)

    assert app.has_marketplace_update() is False


def test_app_has_marketplace_update_true_when_advertised_commit_is_unrelated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """git can't relate a commit this clone has never seen and can't fetch, so the
    advertised release wins - the registry only ever advertises newer code."""
    app = _app_on_branch(tmp_path, "https://github.com/frappe/myapp")
    monkeypatch.setattr("pilot.core.app.installed_app_version", lambda *_: "1.0.0")
    entry = {
        "name": "myapp",
        "repo": "https://github.com/frappe/myapp",
        "releases": [{"version": "1.0.0", "branch": "main", "commit": "0" * 40}],
    }

    _publish(entry)

    assert app.has_marketplace_update() is True


def test_app_is_marketplace_matches_the_registry_entry_for_its_repository(tmp_path: Path) -> None:
    app = _app_on_branch(tmp_path, "https://token:x@github.com/frappe/myapp.git")

    _publish({"name": "myapp", "repo": "https://github.com/frappe/myapp", "releases": []})

    assert app.is_marketplace is True
    assert app.marketplace_entry == {"name": "myapp", "repo": "https://github.com/frappe/myapp"}


def test_app_is_marketplace_false_for_a_fork_of_a_listed_app(tmp_path: Path) -> None:
    app = _app_on_branch(tmp_path, "https://github.com/someone/fork")

    _publish({"name": "myapp", "repo": "https://github.com/frappe/myapp", "releases": []})

    assert app.is_marketplace is False
    assert app.marketplace_entry is None


def test_app_is_marketplace_false_when_unlisted(tmp_path: Path) -> None:
    app = _app_on_branch(tmp_path, "https://github.com/frappe/myapp")

    _publish(None)

    assert app.is_marketplace is False


def test_app_update_target_prefers_the_release_on_the_apps_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app_on_branch(tmp_path, "https://github.com/frappe/myapp", branch="version-15")
    monkeypatch.setattr("pilot.core.app.installed_app_version", lambda *_: "1.0.0")
    entry = {
        "name": "myapp",
        "repo": "https://github.com/frappe/myapp",
        "releases": [
            {"version": "1.0.0", "branch": "main", "commit": "a" * 40},
            {"version": "1.0.0", "branch": "version-15", "commit": "b" * 40},
        ],
    }

    _publish(entry)

    assert app.update_target() == RevisionPin(kind="commit", ref="b" * 40)


def test_app_install_checks_out_the_pinned_commit_before_validating(tmp_path: Path) -> None:
    """A marketplace install validates the advertised commit, not the branch tip."""
    from unittest.mock import patch

    remote = tmp_path / "remote"
    remote.mkdir()
    _init_git_repo(remote)
    _commit(remote, "c1")
    published = GitRepo(remote).head_sha
    _commit(remote, "c2")  # branch has moved on since publication

    bench = make_bench(tmp_path)
    bench.create_directories()
    app = App(AppConfig(name="myapp", repo=str(remote), branch="master"), bench)
    seen_at_validation = {}

    with (
        patch.object(App, "validate", lambda self: seen_at_validation.update(sha=self.installed_hash)),
        patch.object(App, "_install_into_environment"),
        patch.object(App, "_build_assets_via_env_manager"),
    ):
        app.install(commit=published)

    assert seen_at_validation["sha"] == published
    assert app.installed_hash == published
    assert GitRepo(app.path).branch == "master"


def _commit_ahead_of_head(app: App) -> str:
    """A commit on top of the app's HEAD, left unchecked-out."""
    import subprocess

    head = app.installed_hash
    _commit(app.path, "published")
    ahead = app.installed_hash
    subprocess.run(["git", "-C", str(app.path), "reset", "--hard", "-q", head], check=True)
    return ahead


def _app_on_branch(tmp_path: Path, repo: str, branch: str = "main") -> App:
    bench = make_bench(tmp_path)
    app = App(AppConfig(name="myapp", repo=repo, branch=branch), bench)
    app.path.mkdir(parents=True)
    _init_git_repo(app.path)
    _commit(app.path, "c1")
    return app


def test_app_has_marketplace_update_falls_back_to_branch_tip_on_repo_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A fork with a different repo URL isn't the marketplace's app; detection
    # falls through to comparing the branch tip.
    app = _app_on_branch(tmp_path, "https://github.com/someone/fork")
    monkeypatch.setattr("pilot.internal.git.GitRepo.remote_branch_sha", lambda self, branch: "0" * 40)
    entry = {
        "name": "myapp",
        "repo": "https://github.com/frappe/myapp",
        "releases": [{"version": "1.0.0", "branch": "main", "commit": "a" * 40}],
    }

    _publish(entry)

    assert app.has_marketplace_update() is True


def test_app_has_marketplace_update_false_when_branch_tip_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app_on_branch(tmp_path, "https://github.com/frappe/myapp")
    monkeypatch.setattr(
        "pilot.internal.git.GitRepo.remote_branch_sha", lambda self, branch: app.installed_hash
    )
    _publish(None)

    assert app.has_marketplace_update() is False


def test_app_update_target_offers_a_release_ahead_of_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """git decides, not the version label: HEAD is behind the advertised commit."""
    app = _app_on_branch(tmp_path, "https://github.com/frappe/myapp")
    ahead = _commit_ahead_of_head(app)
    entry = {
        "name": "myapp",
        "repo": "https://github.com/frappe/myapp",
        "releases": [{"version": "1.1.0", "branch": "main", "commit": ahead}],
    }

    _publish(entry)

    assert app.update_target() == RevisionPin(kind="commit", ref=ahead)


def test_app_update_target_ignores_a_release_behind_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The advertised commit is an ancestor of HEAD - nothing to move to, and the
    branch tip is never a candidate for a registry app."""
    app = _app_on_branch(tmp_path, "https://github.com/frappe/myapp")
    behind = app.installed_hash
    _commit(app.path, "c2")  # HEAD moves past the advertised release
    entry = {
        "name": "myapp",
        "repo": "https://github.com/frappe/myapp",
        "releases": [{"version": "1.0.0", "branch": "main", "commit": behind}],
    }

    _publish(entry)

    assert app.update_target() is None
    assert app.has_marketplace_update() is False


def test_app_update_target_ignores_a_newer_release_on_another_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app_on_branch(tmp_path, "https://github.com/frappe/myapp", branch="version-15")
    entry = {
        "name": "myapp",
        "repo": "https://github.com/frappe/myapp",
        "releases": [
            {"version": "16.0.0", "branch": "version-16", "commit": _commit_ahead_of_head(app)},
            {"version": "15.1.0", "branch": "version-15", "commit": app.installed_hash},
        ],
    }

    _publish(entry)

    assert app.update_target() is None


def test_app_update_target_follows_the_branch_tip_outside_the_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-marketplace app updates branch-wide, to whatever the tip is."""
    app = _app_on_branch(tmp_path, "https://github.com/someone/private-app")
    monkeypatch.setattr("pilot.internal.git.GitRepo.remote_branch_sha", lambda self, branch: "e" * 40)
    _publish(None)

    assert app.update_target() == RevisionPin(kind="commit", ref="e" * 40)


def test_app_path_is_under_apps_directory(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    app_config = AppConfig(name="frappe", repo="https://example.com", branch="main")
    app = App(app_config, bench)
    assert app.path == tmp_path / "apps" / "frappe"


def test_record_branch_appends_new_app_entry(tmp_path: Path) -> None:
    bench_dir = tmp_path / "bench1"
    _write_bench_toml(bench_dir, "test-bench")
    bench = Bench(BenchConfig.from_file(bench_dir / "bench.toml"), bench_dir)
    app = App(AppConfig(name="myapp", repo="https://example.com/myapp", branch="develop"), bench)

    app.record_branch()

    apps = BenchConfig.from_file(bench_dir / "bench.toml").apps
    entry = next(a for a in apps if a.name == "myapp")
    assert entry.branch == "develop"
    assert entry.repo == "https://example.com/myapp"


def test_record_branch_updates_existing_app_entry(tmp_path: Path) -> None:
    bench_dir = tmp_path / "bench1"
    _write_bench_toml(bench_dir, "test-bench")
    bench = Bench(BenchConfig.from_file(bench_dir / "bench.toml"), bench_dir)
    app = App(AppConfig(name="frappe", repo="https://github.com/frappe/frappe", branch="develop"), bench)

    app.record_branch()

    apps = BenchConfig.from_file(bench_dir / "bench.toml").apps
    assert len(apps) == 1
    assert apps[0].branch == "develop"


def test_record_branch_skips_when_branch_is_commit_hash(tmp_path: Path) -> None:
    bench_dir = tmp_path / "bench1"
    _write_bench_toml(bench_dir, "test-bench")
    bench = Bench(BenchConfig.from_file(bench_dir / "bench.toml"), bench_dir)
    app = App(AppConfig(name="myapp", repo="https://example.com/myapp", branch="a" * 40), bench)

    app.record_branch()

    apps = BenchConfig.from_file(bench_dir / "bench.toml").apps
    assert not any(a.name == "myapp" for a in apps)


def test_record_branch_no_op_without_bench_toml(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    app = App(AppConfig(name="myapp", repo="https://example.com/myapp", branch="develop"), bench)

    app.record_branch()  # should not raise even though bench.toml doesn't exist on disk


def test_site_exists_returns_false_for_nonexistent_path(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    site_config = SiteConfig(name="site1.localhost", apps=["frappe"])
    site = Site(site_config, bench)
    assert site.exists is False


def test_site_exists_returns_false_when_no_site_config_json(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    site_config = SiteConfig(name="site1.localhost", apps=["frappe"])
    site = Site(site_config, bench)
    site.path.mkdir(parents=True)
    assert site.exists is False


def test_site_exists_returns_true_when_site_config_json_present(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    site_config = SiteConfig(name="site1.localhost", apps=["frappe"])
    site = Site(site_config, bench)
    site.path.mkdir(parents=True)
    (site.path / "site_config.json").write_text("{}")
    assert site.exists is True


def test_site_path_is_under_sites_directory(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    site_config = SiteConfig(name="site1.localhost", apps=["frappe"])
    site = Site(site_config, bench)
    assert site.path == tmp_path / "sites" / "site1.localhost"


def test_bench_create_directories(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    assert (tmp_path / "apps").is_dir()
    assert (tmp_path / "sites").is_dir()
    assert (tmp_path / "sites" / "assets").is_dir()
    assert (tmp_path / "logs").is_dir()
    assert (tmp_path / "config").is_dir()
    assert (tmp_path / "pids").is_dir()


def test_bench_apps_scans_filesystem(tmp_path: Path) -> None:
    """bench.apps() discovers apps from apps/ directory, not bench.toml."""
    bench = make_bench(tmp_path)
    bench.create_directories()

    # Create a fake cloned app
    app_dir = tmp_path / "apps" / "testapp"
    app_dir.mkdir()
    (app_dir / ".git").mkdir()

    apps = bench.apps()
    assert len(apps) == 1
    assert apps[0].config.name == "testapp"


def test_bench_apps_ignores_non_git_directories(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    (tmp_path / "apps" / "notapp").mkdir()  # no .git

    apps = bench.apps()
    assert apps == []


def test_bench_sites_scans_filesystem(tmp_path: Path) -> None:
    """bench.sites() discovers sites from sites/ directory."""
    bench = make_bench(tmp_path)
    bench.create_directories()

    site_dir = tmp_path / "sites" / "site1.localhost"
    site_dir.mkdir()
    (site_dir / "site_config.json").write_text("{}")

    sites = bench.sites()
    assert len(sites) == 1
    assert sites[0].config.name == "site1.localhost"


def test_bench_init_apps_comes_from_config(tmp_path: Path) -> None:
    """bench.init_apps() returns apps from bench.toml (used during pilot init)."""
    bench = make_bench(tmp_path)
    init_apps = bench.init_apps()
    assert len(init_apps) == 1
    assert init_apps[0].config.name == "frappe"


def test_process_definitions_returns_correct_count(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    # workers: default=2, short=1, long=1 => 4 worker processes
    # plus web, socketio, redis_cache, redis_queue = 4
    # plus admin, watch (on by default in dev) = 2
    # total = 10
    process_manager = ProcessManager(bench)
    definitions = process_manager._process_definitions()
    assert len(definitions) == 10
    assert "watch" in [pd.name for pd in definitions]
    assert "admin-ui" not in [pd.name for pd in definitions]


def test_process_definitions_watch_admin_js_adds_vite_ui(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    definitions = ProcessManager(bench, watch_admin_js=True)._process_definitions()
    assert "admin-ui" in [pd.name for pd in definitions]
    assert len(definitions) == 11


def test_process_definitions_can_disable_app_watch(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.config.watch_apps_js = False
    definitions = ProcessManager(bench)._process_definitions()
    assert "watch" not in [pd.name for pd in definitions]
    assert len(definitions) == 9


def test_run_processes_survives_noncritical_exit(tmp_path: Path) -> None:
    import time

    bench = make_bench(tmp_path)
    bench.create_directories()
    log = tmp_path / "logs"
    defs = [
        ProcessDefinition(name="flaky", argv=["true"], log_file=log / "flaky.log", critical=False),
        ProcessDefinition(name="main", argv=["sleep", "1.2"], log_file=log / "main.log"),
    ]
    started = time.monotonic()
    ProcessManager(bench)._run_processes(defs)
    assert time.monotonic() - started >= 1.0
    assert not (bench.pids_path / "flaky.pid").exists()


def test_watch_definition_is_noncritical_frappe_watch(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    definitions = ProcessManager(bench)._process_definitions()
    watch = next(pd for pd in definitions if pd.name == "watch")
    assert "frappe watch" in shlex.join(watch.argv)
    assert watch.working_dir == bench.sites_path
    assert watch.critical is False
    assert all(pd.critical for pd in definitions if pd.name != "watch")


def test_process_definitions_worker_names_are_numbered(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    process_manager = ProcessManager(bench)
    definitions = process_manager._process_definitions()
    names = [pd.name for pd in definitions]
    assert "worker_default_1" in names
    assert "worker_default_2" in names
    assert "worker_short_1" in names
    assert "worker_long_1" in names


def test_process_definitions_includes_redis_processes(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    process_manager = ProcessManager(bench)
    definitions = process_manager._process_definitions()
    names = [pd.name for pd in definitions]
    assert "redis_cache" in names
    assert "redis_queue" in names
    assert "redis_socketio" not in names


def test_process_definitions_order_starts_with_web(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    process_manager = ProcessManager(bench)
    definitions = process_manager._process_definitions()
    assert definitions[0].name == "web"


def test_dev_web_enables_python_reloader_by_default(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    web = ProcessManager(bench)._process_definitions()[0]
    assert web.name == "web"
    assert "--noreload" not in web.argv


def test_dev_web_can_disable_python_reloader(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.config.reload_python = False
    web = ProcessManager(bench)._process_definitions()[0]
    assert web.name == "web"
    assert "--noreload" in web.argv


# ── ProcessManager tests ───────────────────────────────────────────────


def test_honcho_generate_config_writes_procfile(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    process_manager = ProcessManager(bench)
    process_manager.write_config()

    procfile = tmp_path / "config" / "Procfile"
    assert procfile.exists()
    content = procfile.read_text()
    assert "web:" in content
    assert "socketio:" in content
    assert "worker_default_1:" in content
    assert "watch:" in content
    assert "redis_cache:" in content


def test_honcho_generate_config_writes_redis_configs(tmp_path: Path) -> None:
    # The generated Procfile runs `redis-server config/redis_{cache,queue}.conf`,
    # so write_config must (re)create those files. Regression: an upgraded
    # bench whose config dir predates the split redis layout used to fail to
    # start with "can't open config file".
    bench = make_bench(tmp_path)
    bench.create_directories()
    process_manager = ProcessManager(bench)
    process_manager.write_config()

    assert (tmp_path / "config" / "redis_cache.conf").exists()
    assert (tmp_path / "config" / "redis_queue.conf").exists()


def test_honcho_generate_config_procfile_format(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    process_manager = ProcessManager(bench)
    process_manager.write_config()

    procfile = tmp_path / "config" / "Procfile"
    content = procfile.read_text()
    for line in content.strip().splitlines():
        assert ": " in line, f"Line missing ': ' separator: {line!r}"


def test_honcho_start_writes_per_process_pid_files(tmp_path: Path) -> None:
    """Each spawned process gets its own pids/<name>.pid file."""
    from unittest.mock import MagicMock, patch

    bench = make_bench(tmp_path)
    bench.create_directories()
    process_manager = ProcessManager(bench)
    process_manager.write_config()

    fake_proc = MagicMock()
    fake_proc.pid = 12345
    fake_proc.stdout = iter([])
    fake_proc.poll.return_value = None
    fake_proc.wait.return_value = 0

    def fake_popen(cmd, **kwargs):
        return fake_proc

    with (
        patch("pilot.managers.processes.local.subprocess.Popen", side_effect=fake_popen),
        patch.object(process_manager, "_stop_all"),
    ):
        for pd in process_manager._process_definitions():
            proc = fake_popen(pd.argv)
            process_manager._procs[pd.name] = proc
            (bench.pids_path / f"{pd.name}.pid").write_text(str(proc.pid))

    for name in process_manager._procs:
        pid_file = bench.pids_path / f"{name}.pid"
        assert pid_file.exists(), f"Missing PID file for process '{name}'"
        assert pid_file.read_text().strip() == "12345"


def _capture_admin_sql(monkeypatch) -> list[str]:
    """Record the SQL the temporary setup account is created and dropped with."""
    statements: list[str] = []
    monkeypatch.setattr(
        "pilot.managers.database.mariadb.MariaDBManager.run_admin_sql",
        lambda self, sql: statements.append(sql),
    )
    return statements


def _write_site_config(bench, site: str, db_name: str, db_type: str = "mariadb") -> None:
    import json

    site_dir = bench.sites_path / site
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "site_config.json").write_text(json.dumps({"db_name": db_name, "db_type": db_type}))


def _capture_site_cmd(monkeypatch) -> dict:
    import subprocess

    captured: dict = {}

    def stub(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("pilot.core.site.commands.run_command", stub)
    return captured


def _postgres_bench(tmp_path: Path, **postgres):
    bench = make_bench(tmp_path)
    bench.config.db_type = "postgres"
    for key, value in postgres.items():
        setattr(bench.config.postgres, key, value)
    return bench


def test_site_create_postgres_builds_db_args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bench = _postgres_bench(tmp_path, root_password="pgsecret", port=5433)
    captured = _capture_site_cmd(monkeypatch)

    Site(SiteConfig(name="pg.localhost", apps=["frappe"], admin_password="secret"), bench).create()

    cmd = captured["cmd"]
    assert cmd[cmd.index("--db-type") + 1] == "postgres"
    assert cmd[cmd.index("--db-host") + 1] == "localhost"
    assert cmd[cmd.index("--db-port") + 1] == "5433"
    assert cmd[cmd.index("--db-root-username") + 1] == "postgres"
    assert cmd[cmd.index("--db-root-password") + 1] == "pgsecret"
    assert "--db-socket" not in cmd


def test_site_create_mariadb_when_bench_is_mariadb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """frappe reads its database credential from argv, where any local process can see
    it, so it gets a throwaway account scoped to this site's database - never root."""
    bench = make_bench(tmp_path)  # mariadb bench, root_password="root"
    captured = _capture_site_cmd(monkeypatch)
    statements = _capture_admin_sql(monkeypatch)
    monkeypatch.setattr("pilot.managers.database.mariadb.MariaDBManager._detect_socket", lambda self: "")

    Site(SiteConfig(name="mdb.localhost", apps=["frappe"], admin_password="secret"), bench).create()

    cmd = captured["cmd"]
    # mariadb is frappe's default engine - no --db-type flag is passed
    assert "--db-type" not in cmd
    assert "--db-host" in cmd
    database = cmd[cmd.index("--db-name") + 1]
    user = cmd[cmd.index("--db-root-username") + 1]
    assert user.startswith("pilot_setup_")
    assert "root" not in (user, cmd[cmd.index("--db-root-password") + 1])
    assert f"GRANT ALL PRIVILEGES ON `{database}`.*" in statements[0]
    assert f"DROP USER IF EXISTS '{user}'@'%'" in statements[-1]


def test_site_restore_uses_postgres_root_creds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bench = _postgres_bench(tmp_path, root_password="pgpw")
    _write_site_config(bench, "pg.localhost", "pgdb", "postgres")
    captured = _capture_site_cmd(monkeypatch)

    Site(SiteConfig(name="pg.localhost", apps=[]), bench).restore("/tmp/db.sql.gz")

    cmd = captured["cmd"]
    assert "restore" in cmd
    assert "--db-type" not in cmd  # restore reads the engine from the site's config
    assert cmd[cmd.index("--db-root-username") + 1] == "postgres"
    assert cmd[cmd.index("--db-root-password") + 1] == "pgpw"


def test_site_restore_scopes_its_account_to_the_site_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = make_bench(tmp_path)  # mariadb bench, root_password="root"
    _write_site_config(bench, "m.localhost", "_restoredb")
    captured = _capture_site_cmd(monkeypatch)
    statements = _capture_admin_sql(monkeypatch)

    Site(SiteConfig(name="m.localhost", apps=[]), bench).restore(
        "/tmp/db.sql.gz", public_files="/tmp/pub.tar", private_files="/tmp/priv.tar"
    )

    cmd = captured["cmd"]
    assert "--db-type" not in cmd
    assert cmd[cmd.index("--db-root-username") + 1].startswith("pilot_setup_")
    assert "GRANT ALL PRIVILEGES ON `_restoredb`.*" in statements[0]
    assert cmd[cmd.index("--with-public-files") + 1] == "/tmp/pub.tar"
    assert cmd[cmd.index("--with-private-files") + 1] == "/tmp/priv.tar"


def test_site_reinstall_postgres_root_creds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bench = _postgres_bench(tmp_path, root_password="pgpw")
    _write_site_config(bench, "pg.localhost", "pgdb", "postgres")
    captured = _capture_site_cmd(monkeypatch)

    Site(SiteConfig(name="pg.localhost", apps=[]), bench).reinstall("secret")

    cmd = captured["cmd"]
    assert "reinstall" in cmd and "--yes" in cmd
    assert cmd[cmd.index("--admin-password") + 1] == "secret"
    assert cmd[cmd.index("--db-root-username") + 1] == "postgres"
    assert cmd[cmd.index("--db-root-password") + 1] == "pgpw"


def test_site_reinstall_refuses_root_on_argv_without_a_site_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no resolvable site database there is nothing to scope an account to; rather
    than fall back to the root password on argv, the command fails loudly."""
    from pilot.exceptions import BenchError

    bench = make_bench(tmp_path)
    _write_site_config(bench, "m.localhost", "")
    _capture_site_cmd(monkeypatch)

    with pytest.raises(BenchError, match="root database password"):
        Site(SiteConfig(name="m.localhost", apps=[]), bench).reinstall("secret")


def test_site_reinstall_uses_the_site_engine_not_the_bench_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A postgres site on a MariaDB bench gets postgres creds, never the MariaDB root password."""
    bench = make_bench(tmp_path)  # mariadb bench, root_password="root"
    bench.config.postgres.root_password = "pgpw"
    _write_site_config(bench, "pg.localhost", "pgdb", "postgres")
    captured = _capture_site_cmd(monkeypatch)

    Site(SiteConfig(name="pg.localhost", apps=[]), bench).reinstall("secret")

    cmd = captured["cmd"]
    assert cmd[cmd.index("--db-root-username") + 1] == "postgres"
    assert cmd[cmd.index("--db-root-password") + 1] == "pgpw"


def test_unknown_site_engine_keeps_the_scoped_mariadb_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = make_bench(tmp_path)
    _write_site_config(bench, "m.localhost", "_mdb", "oracle")
    captured = _capture_site_cmd(monkeypatch)
    _capture_admin_sql(monkeypatch)

    Site(SiteConfig(name="m.localhost", apps=[]), bench).reinstall("secret")

    cmd = captured["cmd"]
    assert cmd[cmd.index("--db-root-username") + 1].startswith("pilot_setup_")
    assert cmd[cmd.index("--db-root-password") + 1] != "root"


def test_site_db_type_falls_back_to_frappe_default_not_the_bench(tmp_path: Path) -> None:
    import json

    bench = _postgres_bench(tmp_path)
    site_dir = bench.sites_path / "old.localhost"
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text(json.dumps({"db_name": "_old"}))

    assert Site(SiteConfig(name="old.localhost", apps=[]), bench).db_type == "mariadb"


def test_site_create_and_reinstall_reject_empty_admin_password(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    site = Site(SiteConfig(name="m.localhost", apps=[]), bench)

    with pytest.raises(BenchError, match="must not be empty"):
        site.create()
    with pytest.raises(BenchError, match="must not be empty"):
        site.reinstall("   ")


def test_site_migrate_skip_failing_adds_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bench = make_bench(tmp_path)
    captured = _capture_site_cmd(monkeypatch)

    Site(SiteConfig(name="m.localhost", apps=[]), bench).migrate(skip_failing=True)

    assert "--skip-failing" in captured["cmd"]


def test_site_migrate_without_skip_failing_omits_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = make_bench(tmp_path)
    captured = _capture_site_cmd(monkeypatch)

    Site(SiteConfig(name="m.localhost", apps=[]), bench).migrate()

    assert "--skip-failing" not in captured["cmd"]


def test_site_create_postgres_empty_password_uses_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = _postgres_bench(tmp_path, root_password="")  # trust/peer auth - no password
    captured = _capture_site_cmd(monkeypatch)

    Site(SiteConfig(name="pg.localhost", apps=[], admin_password="secret"), bench).create()

    cmd = captured["cmd"]
    # frappe prompts on an empty password (hanging the task); a placeholder avoids it.
    assert cmd[cmd.index("--db-root-password") + 1] == "trust_auth"


def test_bench_get_db_root_args_postgres(tmp_path: Path) -> None:
    bench = _postgres_bench(tmp_path, root_password="pgpw")
    assert bench.get_db_root_args("postgres") == ["--db-root-username", "postgres", "--db-root-password", "pgpw"]


def test_bench_get_db_root_args_mariadb(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    assert bench.get_db_root_args("mariadb") == ["--db-root-username", "root", "--db-root-password", "root"]


def test_bench_get_db_root_args_follows_the_engine_asked_for(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)  # mariadb bench
    assert bench.get_db_root_args("sqlite") == []
