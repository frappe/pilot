from __future__ import annotations

from pathlib import Path

import pytest

from pilot.config import AppConfig
from pilot.core.app import App
from pilot.core.app.processes import AppProcessError
from tests.pilot.commands.test_commands import make_bench

_SECTION = "[tool.pilot.processes.stalwart]\n"


def _app(tmp_path: Path, pyproject: str) -> App:
    bench = make_bench(tmp_path)
    bench.create_directories()
    (bench.apps_path / "mail" / ".git").mkdir(parents=True)
    (bench.apps_path / "mail" / "pyproject.toml").write_text(pyproject)
    return App(AppConfig(name="mail", repo="https://github.com/frappe/mail", branch="main"), bench)


def test_reads_full_declaration(tmp_path: Path) -> None:
    app = _app(
        tmp_path,
        _SECTION + 'command = ["./stalwart", "--config", "config.toml"]\n'
        'pre_run = ["scripts/install.sh"]\n'
        'post_run = ["rm", "-f", "stalwart.sock"]\n'
        'working_dir = "/opt/stalwart"\n'
        "restart_on_failure = false\n"
        "stop_timeout = 30\n"
        'env = { STALWART_PATH = "/opt/stalwart", PORT = 8080 }\n',
    )

    (definition,) = app.processes.get_definitions()

    assert definition.name == "mail-stalwart"
    assert definition.argv == ["/opt/stalwart/stalwart", "--config", "config.toml"]
    assert definition.pre_run == ["/opt/stalwart/scripts/install.sh"]
    assert definition.post_run == ["rm", "-f", "stalwart.sock"]
    assert definition.working_dir == Path("/opt/stalwart")
    assert definition.restart_on_failure is False
    assert definition.stop_timeout == 30
    assert definition.env["STALWART_PATH"] == "/opt/stalwart"
    assert definition.env["PORT"] == "8080"
    assert definition.log_file == app.bench.logs_path / "mail-stalwart.log"
    assert definition.critical is False


def test_minimal_declaration_runs_in_the_app_directory(tmp_path: Path) -> None:
    app = _app(tmp_path, _SECTION + 'command = ["./stalwart"]\n')

    (definition,) = app.processes.get_definitions()

    assert definition.working_dir == app.path
    assert definition.argv == [str(app.path / "stalwart")]
    assert definition.restart_on_failure is True
    assert definition.pre_run == definition.post_run == []


def test_every_process_runs_with_the_bench_venv_first_on_path(tmp_path: Path) -> None:
    app = _app(tmp_path, _SECTION + 'command = ["flow", "serve"]\nenv = { VIRTUAL_ENV = "/custom" }\n')

    (definition,) = app.processes.get_definitions()

    assert definition.env["PATH"].startswith(f"{app.bench.env_path / 'bin'}:")
    assert definition.env["FRAPPE_BENCH_ROOT"] == str(app.bench.path)
    assert definition.env["PYTHONUNBUFFERED"] == "1"
    assert definition.env["VIRTUAL_ENV"] == "/custom"  # the app's own env wins


def test_frappe_command_runs_through_the_bench_helper_in_sites(tmp_path: Path) -> None:
    app = _app(tmp_path, _SECTION + 'frappe_command = ["flow-server", "serve"]\nenv = { MODE = "test" }\n')

    (definition,) = app.processes.get_definitions()

    assert definition.argv == [*app.bench.frappe_call, "frappe", "flow-server", "serve"]
    assert definition.working_dir == app.bench.sites_path
    assert definition.env["MODE"] == "test"


@pytest.mark.parametrize("pyproject", ["", "[project]\nname = 'mail'\n"])
def test_app_without_declarations_has_no_processes(tmp_path: Path, pyproject: str) -> None:
    assert _app(tmp_path, pyproject).processes.get_definitions() == []


@pytest.mark.parametrize(
    "pyproject",
    [
        "[tool.pilot\n",
        "[tool.pilot]\nprocesses = 1\n",
        '[tool.pilot.processes."Bad Name"]\ncommand = ["/bin/true"]\n',
        # `mail` + `smtp-relay` would share a name with app `mail-smtp` + `relay`.
        '[tool.pilot.processes.smtp-relay]\ncommand = ["/bin/true"]\n',
        _SECTION + "restart_on_failure = true\n",
        _SECTION + "command = []\n",
        _SECTION + 'command = "/bin/true"\n',
        _SECTION + 'command = ["/bin/true\\n[Service]"]\n',
        _SECTION + 'command = ["/bin/true"]\npre_run = "echo hi"\n',
        _SECTION + 'command = ["/bin/true"]\nenv = { "bad-key" = "v" }\n',
        _SECTION + 'command = ["/bin/true"]\nenv = { DEBUG = true }\n',
        _SECTION + 'command = ["/bin/true"]\nrestart_on_failure = "yes"\n',
        _SECTION + 'command = ["/bin/true"]\nstop_timeout = true\n',
        _SECTION + 'command = ["/bin/true"]\nfrappe_command = ["flow-server", "serve"]\n',
        _SECTION + 'frappe_command = ["flow-server", "serve"]\nworking_dir = "server"\n',
    ],
)
def test_malformed_declaration_is_rejected(tmp_path: Path, pyproject: str) -> None:
    with pytest.raises(AppProcessError, match="App 'mail'"):
        _app(tmp_path, pyproject).processes.get_definitions()
