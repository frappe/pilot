from pilot.internal.cli.registry import command_names


def test_cloning_commands_expose_separate_bench_and_site_operations():
    commands = command_names()
    assert {"clone-bench", "clone-site"} <= commands
    assert not {"fork", "prepare-template"} & commands
