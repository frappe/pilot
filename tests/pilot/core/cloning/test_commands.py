import pytest

from pilot.internal.cli.registry import command_names


def test_cloning_commands_expose_separate_bench_and_site_operations():
    commands = command_names()
    assert {"clone-bench", "clone-site"} <= commands
    assert not {"fork", "prepare-template"} & commands


@pytest.mark.parametrize("password", [None, "chosen-password"])
@pytest.mark.parametrize("fails", [False, True])
def test_clone_cli_reports_generated_password_only_after_success(
    source, monkeypatch, capsys, password, fails
):
    from pilot.commands.sites.clone import CloneSiteCommand
    from pilot.core.site import Site

    passwords = []

    def clone(self, name, destination, admin_password, on_progress):
        passwords.append(admin_password)
        if fails:
            raise RuntimeError("clone failed")

    monkeypatch.setattr(Site, "clone", clone)
    command = CloneSiteCommand(
        bench=source, source="source.localhost", target="copy.localhost", admin_password=password
    )
    if fails:
        with pytest.raises(RuntimeError, match="clone failed"):
            command.run()
    else:
        command.run()
    output = capsys.readouterr().out
    assert bool(output) == (password is None and not fails)
    if output:
        assert f"Generated Administrator password: {passwords[0]}" in output
    assert passwords[0] == password if password is not None else len(passwords[0]) >= 24
