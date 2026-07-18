from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Annotated, Literal

import pytest

from pilot.commands import Arg, Command
from pilot.exceptions import BenchError
from pilot.internal.cli.command import add_command_arguments, command_from_args


def test_arg_is_public_authoring_type() -> None:
    assert Arg.__module__ == "pilot.commands.base"


def test_command_public_api_does_not_expose_parser_plumbing() -> None:
    assert not hasattr(Command, "add_arguments")
    assert not hasattr(Command, "from_args")


def test_report_prints_message(capsys: pytest.CaptureFixture) -> None:
    Command().report("hello")
    assert capsys.readouterr().out == "hello\n"


def test_confirm_skips_when_requested() -> None:
    Command().confirm("Proceed?", skip=True)  # no raise, no input


def test_confirm_raises_on_negative_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _: "n")
    with pytest.raises(BenchError, match="Aborted"):
        Command().confirm("Proceed?")


def test_confirm_passes_on_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _: "yes")
    Command().confirm("Proceed?")  # no raise


def test_confirm_raises_custom_error_type(monkeypatch: pytest.MonkeyPatch) -> None:
    class CustomError(Exception):
        pass

    monkeypatch.setattr("builtins.input", lambda _: "n")
    with pytest.raises(CustomError, match="Aborted"):
        Command().confirm("Proceed?", error=CustomError)


@pytest.mark.parametrize("exception", [EOFError, KeyboardInterrupt])
def test_confirm_treats_eof_and_ctrl_c_as_decline(
    monkeypatch: pytest.MonkeyPatch, exception: type[BaseException]
) -> None:
    def raise_exception(_prompt: str) -> str:
        raise exception

    monkeypatch.setattr("builtins.input", raise_exception)
    with pytest.raises(BenchError, match="Aborted"):
        Command().confirm("Proceed?")


def _parse(cls: type[Command], argv: list[str]) -> Command:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", "-y", action="store_true")
    add_command_arguments(cls, parser)
    return command_from_args(cls, parser.parse_args(argv), bench=None)


@dataclass(kw_only=True)
class _Greet(Command):
    who: str
    loud: bool = False
    times: int = 1
    tags: list[str] | None = None
    kind: Literal["formal", "casual"] = "casual"
    apps: Annotated[list[str], Arg(help="apps to greet")]
    secret: Annotated[str, Arg(cli=False)] = "hidden"


def test_positional_field_is_required() -> None:
    assert _parse(_Greet, ["alice", "a"]).who == "alice"


def test_bool_field_becomes_store_true_flag() -> None:
    assert _parse(_Greet, ["alice", "a", "--loud"]).loud is True
    assert _parse(_Greet, ["alice", "a"]).loud is False


def test_optional_bool_field_defaults_to_its_own_default_not_false() -> None:
    @dataclass(kw_only=True)
    class _TriState(Command):
        tls: bool | None = None

    assert _parse(_TriState, []).tls is None
    assert _parse(_TriState, ["--tls"]).tls is True


def test_optional_field_uses_kebab_case_flag_and_default() -> None:
    assert _parse(_Greet, ["alice", "a", "--times", "3"]).times == 3
    assert _parse(_Greet, ["alice", "a"]).times == 1


def test_optional_list_field_uses_nargs_star() -> None:
    assert _parse(_Greet, ["alice", "a", "--tags", "x", "y"]).tags == ["x", "y"]
    assert _parse(_Greet, ["alice", "a"]).tags is None


def test_required_list_field_uses_nargs_plus() -> None:
    assert _parse(_Greet, ["alice", "a", "b"]).apps == ["a", "b"]
    with pytest.raises(SystemExit):
        _parse(_Greet, ["alice"])


def test_literal_field_becomes_choices() -> None:
    assert _parse(_Greet, ["alice", "a", "--kind", "formal"]).kind == "formal"
    with pytest.raises(SystemExit):
        _parse(_Greet, ["alice", "a", "--kind", "bogus"])


def test_cli_false_field_is_not_a_cli_argument() -> None:
    assert _parse(_Greet, ["alice", "a"]).secret == "hidden"
    with pytest.raises(SystemExit):
        _parse(_Greet, ["alice", "a", "--secret", "x"])


def test_tuple_field_captures_remaining_args_verbatim() -> None:
    @dataclass(kw_only=True)
    class _Passthrough(Command):
        args: tuple[str, ...] = ()

    assert _parse(_Passthrough, ["migrate", "site1.localhost"]).args == (
        "migrate",
        "site1.localhost",
    )


def test_bench_field_is_never_a_cli_argument() -> None:
    with pytest.raises(SystemExit):
        _parse(_Greet, ["alice", "a", "--bench", "x"])


def test_skip_confirm_is_sourced_from_global_yes_flag() -> None:
    @dataclass(kw_only=True)
    class _Destructive(Command):
        skip_confirm: bool = False

    assert _parse(_Destructive, ["--yes"]).skip_confirm is True
    assert _parse(_Destructive, []).skip_confirm is False
