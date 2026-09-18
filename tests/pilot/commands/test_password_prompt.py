import pytest

from pilot.commands import Command
from pilot.exceptions import BenchError


@pytest.fixture
def command(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    return Command()


def _prompts(monkeypatch, *answers: str) -> list[str]:
    asked: list[str] = []
    replies = iter(answers)

    def fake_getpass(prompt: str) -> str:
        asked.append(prompt)
        return next(replies)

    monkeypatch.setattr("getpass.getpass", fake_getpass)
    return asked


def test_ask_password_confirms_a_valid_password(command, monkeypatch):
    asked = _prompts(monkeypatch, "Str0ng!pass", "Str0ng!pass")

    assert command.ask_password() == "Str0ng!pass"
    assert len(asked) == 2


def test_ask_password_rejects_a_weak_password_without_confirming(command, monkeypatch, capsys):
    asked = _prompts(monkeypatch, "weak")

    with pytest.raises(BenchError, match="at least 8 characters"):
        command.ask_password()
    assert len(asked) == 1
    assert "Password requirements:" in capsys.readouterr().out


def test_ask_password_reports_mismatch(command, monkeypatch):
    _prompts(monkeypatch, "Str0ng!pass", "Str0ng!other")

    with pytest.raises(BenchError, match="Passwords do not match"):
        command.ask_password()


def test_ask_password_returns_empty_for_a_blank_answer(command, monkeypatch):
    asked = _prompts(monkeypatch, "")

    assert command.ask_password() == ""
    assert len(asked) == 1


def test_resolve_password_validates_the_flag_without_prompting(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    _prompts(monkeypatch)

    with pytest.raises(BenchError, match="Password needs at least 8 characters"):
        Command().resolve_password("weak")


def test_resolve_password_returns_empty_without_a_terminal(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    assert Command().resolve_password(None) == ""
