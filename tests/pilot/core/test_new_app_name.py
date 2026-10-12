from unittest.mock import patch

import pytest

from pilot.config import AppConfig, BenchConfig
from pilot.core.app import App, NewAppOptions
from pilot.exceptions import BenchError
from tests.pilot.commands.test_commands import make_bench


def test_normalizes_spaces_and_hyphens():
    assert App._normalize_new_app_name("My Cool-App") == "my_cool_app"


@pytest.mark.parametrize("bad", ["9app", "app.name", "", "/tmp/demo", "foo/bar", "../evil", "a b/c"])
def test_rejects_invalid_names(bad):
    with pytest.raises(BenchError):
        App._normalize_new_app_name(bad)


@pytest.mark.parametrize("blank", ["description", "publisher", "email"])
def test_rejects_blank_required_answers(blank):
    answers = {"description": "Hi", "publisher": "Tanmoy", "email": "t@f.io", blank: "  "}
    with pytest.raises(BenchError):
        NewAppOptions(**answers)


def test_answers_are_ordered_to_match_make_app_prompts():
    options = NewAppOptions(
        title="", description="Hi", publisher="Tanmoy", email="t@f.io",
        license="mit", branch="develop", github_workflow=True,
    )
    assert options.as_answers() == "\nHi\nTanmoy\nt@f.io\nmit\ny\ndevelop\n"


@pytest.mark.parametrize("branch", ["", "custom"])
def test_new_app_records_the_scaffold_branch_in_bench_config(tmp_path, branch):
    bench = make_bench(tmp_path)
    bench.create_directories()
    bench.config.write(bench.path)
    options = NewAppOptions(description="Hi", publisher="Frappe", email="dev@example.com", branch=branch)

    def scaffold(argv, **kwargs):
        import subprocess

        path = bench.apps_path / "my_app"
        path.mkdir()
        subprocess.run(["git", "init", "-q", "-b", branch or "develop", str(path)], check=True)

    with patch("pilot.core.app.run_command", scaffold), patch.object(App, "_install_into_environment"):
        app = bench.new_app("My App", options)

    config = BenchConfig.read(bench.path)
    entry = next(entry for entry in config.apps if entry.name == "my_app")
    assert entry.branch == (branch or "develop")
    assert entry.repo == str(app.path)
    assert app.config.branch == entry.branch
    assert bench.registered_apps() == ["my_app"]


def test_new_app_replaces_stale_config_entry(tmp_path):
    bench = make_bench(tmp_path)
    bench.create_directories()
    bench.config.apps.append(AppConfig(name="my_app", repo="https://example.com/old", branch="old"))
    bench.config.write(bench.path)
    options = NewAppOptions(description="Hi", publisher="Frappe", email="dev@example.com")

    def scaffold(argv, **kwargs):
        import subprocess

        path = bench.apps_path / "my_app"
        path.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "develop", str(path)], check=True)

    with patch("pilot.core.app.run_command", scaffold), patch.object(App, "_install_into_environment"):
        app = bench.new_app("My App", options)

    entries = [entry for entry in BenchConfig.read(bench.path).apps if entry.name == "my_app"]
    assert len(entries) == 1
    assert entries[0].repo == str(app.path)
    assert entries[0].branch == "develop"
    assert bench.registered_apps() == ["my_app"]
