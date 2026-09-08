"""Tests for pilot.core.app.requirements.AppRequirements."""

from __future__ import annotations

from pathlib import Path

import pytest

from pilot.core.app.requirements import AppRequirements, AppRequirementsError
from tests.pilot.commands.test_commands import make_bench


def _app_with_pyproject(tmp_path: Path, body: str, name: str = "mail"):
    from pilot.config import AppConfig
    from pilot.core.app import App

    bench = make_bench(tmp_path)
    bench.create_directories()
    app_dir = bench.apps_path / name
    app_dir.mkdir(parents=True)
    (app_dir / "pyproject.toml").write_text(body)
    return App(AppConfig(name=name, repo=f"https://github.com/frappe/{name}", branch="main"), bench)


def test_missing_section_yields_empty(tmp_path: Path) -> None:
    app = _app_with_pyproject(tmp_path, "[project]\nname = 'mail'\n")
    assert AppRequirements(app).process_definitions() == []


def test_no_pyproject_yields_empty(tmp_path: Path) -> None:
    from pilot.config import AppConfig
    from pilot.core.app import App

    bench = make_bench(tmp_path)
    bench.create_directories()
    (bench.apps_path / "mail").mkdir(parents=True)
    app = App(AppConfig(name="mail", repo="https://github.com/frappe/mail", branch="main"), bench)
    assert AppRequirements(app).process_definitions() == []


def test_reads_full_process(tmp_path: Path) -> None:
    app = _app_with_pyproject(
        tmp_path,
        """
[tool.pilot.background_processes.stalwart]
cmd = ["./stalwart", "--config", "config.toml"]
restart_on_failure = false
pre_run = ["bash", "-c", "./scripts/install_stalwart.sh"]
post_run = ["bash", "-c", "rm -f stalwart.sock"]
working_dir = "/opt/stalwart"
stop_timeout = 30
env = { STALWART_PATH = "/opt/stalwart" }
""",
    )
    (pd,) = AppRequirements(app).process_definitions()
    assert pd.name == "mail-stalwart"
    assert pd.argv == ["/opt/stalwart/stalwart", "--config", "config.toml"]
    assert pd.pre_run == ["bash", "-c", "./scripts/install_stalwart.sh"]
    assert pd.post_run == ["bash", "-c", "rm -f stalwart.sock"]
    assert pd.restart_on_failure is False
    assert pd.working_dir == Path("/opt/stalwart")
    assert pd.stop_timeout == 30
    assert pd.env == {"STALWART_PATH": "/opt/stalwart"}
    assert pd.log_file.name == "mail-stalwart.log"


def test_minimal_process_defaults(tmp_path: Path) -> None:
    app = _app_with_pyproject(
        tmp_path,
        '[tool.pilot.background_processes.flow_server]\ncmd = ["flow", "serve"]\n',
    )
    (pd,) = AppRequirements(app).process_definitions()
    assert pd.name == "mail-flow_server"
    assert pd.restart_on_failure is True  # restarting is the default
    assert pd.pre_run == []
    assert pd.post_run == []


def test_working_dir_defaults_to_the_app_directory(tmp_path: Path) -> None:
    app = _app_with_pyproject(
        tmp_path,
        '[tool.pilot.background_processes.p]\ncmd = ["flow", "serve"]\n',
    )
    (pd,) = AppRequirements(app).process_definitions()
    assert pd.working_dir == app.path


def test_relative_working_dir_is_read_from_the_app_directory(tmp_path: Path) -> None:
    app = _app_with_pyproject(
        tmp_path,
        '[tool.pilot.background_processes.p]\ncmd = ["flow"]\nworking_dir = "server"\n',
    )
    (pd,) = AppRequirements(app).process_definitions()
    assert pd.working_dir == app.path / "server"


@pytest.mark.parametrize("declared", ["./stalwart", "bin/stalwart"])
def test_relative_executable_is_anchored_to_working_dir(tmp_path: Path, declared: str) -> None:
    app = _app_with_pyproject(
        tmp_path,
        f'[tool.pilot.background_processes.p]\ncmd = ["{declared}", "--config", "c.toml"]\n'
        'working_dir = "/opt/stalwart"\n',
    )
    (pd,) = AppRequirements(app).process_definitions()
    assert pd.argv[0] == str(Path("/opt/stalwart") / declared)
    assert pd.argv[1:] == ["--config", "c.toml"]  # only the executable is rewritten


def test_bare_executable_is_left_for_path_lookup(tmp_path: Path) -> None:
    app = _app_with_pyproject(
        tmp_path,
        '[tool.pilot.background_processes.p]\ncmd = ["flow", "serve"]\n',
    )
    (pd,) = AppRequirements(app).process_definitions()
    assert pd.argv == ["flow", "serve"]


def test_absolute_executable_is_untouched(tmp_path: Path) -> None:
    app = _app_with_pyproject(
        tmp_path,
        '[tool.pilot.background_processes.p]\ncmd = ["/usr/bin/stalwart"]\nworking_dir = "/opt/x"\n',
    )
    (pd,) = AppRequirements(app).process_definitions()
    assert pd.argv == ["/usr/bin/stalwart"]


def test_hook_executables_are_anchored_too(tmp_path: Path) -> None:
    """A hook that installs the binary is itself shipped by the app, so it is
    resolved the same way - lexically, before the file exists."""
    app = _app_with_pyproject(
        tmp_path,
        '[tool.pilot.background_processes.p]\ncmd = ["flow"]\n'
        'pre_run = ["./scripts/install.sh", "--quiet"]\npost_run = ["./scripts/clean.sh"]\n',
    )
    (pd,) = AppRequirements(app).process_definitions()
    assert pd.pre_run == [str(app.path / "./scripts/install.sh"), "--quiet"]
    assert pd.post_run == [str(app.path / "./scripts/clean.sh")]


def test_multiple_processes_keep_declaration_order(tmp_path: Path) -> None:
    app = _app_with_pyproject(
        tmp_path,
        '[tool.pilot.background_processes.chromium]\ncmd = ["/bin/chromium"]\n'
        '[tool.pilot.background_processes.flow]\ncmd = ["flow", "serve"]\n',
    )
    assert [pd.name for pd in AppRequirements(app).process_definitions()] == [
        "mail-chromium",
        "mail-flow",
    ]


def test_malformed_toml_raises(tmp_path: Path) -> None:
    app = _app_with_pyproject(tmp_path, "[tool.pilot\n")
    with pytest.raises(AppRequirementsError):
        AppRequirements(app).process_definitions()


@pytest.mark.parametrize("name", ["../evil", "bad name", "x\ninjected", "-flag", "UPPER", "a" * 40])
def test_bad_process_name_rejected(tmp_path: Path, name: str) -> None:
    app = _app_with_pyproject(
        tmp_path,
        f'[tool.pilot.background_processes."{name}"]\ncmd = ["/bin/true"]\n',
    )
    with pytest.raises(AppRequirementsError):
        AppRequirements(app).process_definitions()


def test_control_char_in_cmd_rejected(tmp_path: Path) -> None:
    app = _app_with_pyproject(
        tmp_path,
        '[tool.pilot.background_processes.p]\ncmd = ["/bin/true\\n[Service]"]\n',
    )
    with pytest.raises(AppRequirementsError):
        AppRequirements(app).process_definitions()


@pytest.mark.parametrize("hook", ["pre_run", "post_run"])
def test_control_char_in_hook_rejected(tmp_path: Path, hook: str) -> None:
    app = _app_with_pyproject(
        tmp_path,
        f'[tool.pilot.background_processes.p]\ncmd = ["/bin/true"]\n{hook} = ["/bin/x\\n[Service]"]\n',
    )
    with pytest.raises(AppRequirementsError):
        AppRequirements(app).process_definitions()


@pytest.mark.parametrize("hook", ["pre_run", "post_run"])
def test_hook_must_be_a_list(tmp_path: Path, hook: str) -> None:
    app = _app_with_pyproject(
        tmp_path,
        f'[tool.pilot.background_processes.p]\ncmd = ["/bin/true"]\n{hook} = "echo hi"\n',
    )
    with pytest.raises(AppRequirementsError):
        AppRequirements(app).process_definitions()


def test_control_char_in_env_value_rejected(tmp_path: Path) -> None:
    app = _app_with_pyproject(
        tmp_path,
        '[tool.pilot.background_processes.p]\ncmd = ["/bin/true"]\nenv = { K = "v\\nExecStartPre=/x" }\n',
    )
    with pytest.raises(AppRequirementsError):
        AppRequirements(app).process_definitions()


def test_bad_env_key_rejected(tmp_path: Path) -> None:
    app = _app_with_pyproject(
        tmp_path,
        '[tool.pilot.background_processes.p]\ncmd = ["/bin/true"]\nenv = { "bad-key" = "v" }\n',
    )
    with pytest.raises(AppRequirementsError):
        AppRequirements(app).process_definitions()


def test_empty_cmd_rejected(tmp_path: Path) -> None:
    app = _app_with_pyproject(tmp_path, "[tool.pilot.background_processes.p]\ncmd = []\n")
    with pytest.raises(AppRequirementsError):
        AppRequirements(app).process_definitions()


def test_missing_cmd_rejected(tmp_path: Path) -> None:
    app = _app_with_pyproject(
        tmp_path,
        "[tool.pilot.background_processes.p]\nrestart_on_failure = true\n",
    )
    with pytest.raises(AppRequirementsError):
        AppRequirements(app).process_definitions()


def test_non_bool_restart_rejected(tmp_path: Path) -> None:
    app = _app_with_pyproject(
        tmp_path,
        '[tool.pilot.background_processes.p]\ncmd = ["/bin/true"]\nrestart_on_failure = "yes"\n',
    )
    with pytest.raises(AppRequirementsError):
        AppRequirements(app).process_definitions()
