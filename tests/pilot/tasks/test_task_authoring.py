from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, ClassVar, Literal

import pytest

from pilot.internal.tasks.authoring import (
    required_task_args,
    task_argv_suffix,
    task_from_args,
    task_parser,
)
from pilot.tasks import Arg, Task, step
from tests.pilot.commands.test_commands import make_bench


class DemoTask(Task):
    @step("work", "Do work")
    def fail(self) -> None:
        raise RuntimeError("boom")

    @step("work", "Do work")
    def succeed(self) -> None:
        return None


def task(tmp_path: Path) -> DemoTask:
    bench = make_bench(tmp_path)
    return DemoTask(bench=bench, bench_root=tmp_path)


def parse_task(cls: type[Task], tmp_path: Path, argv: list[str]) -> Task:
    args = task_parser(cls).parse_args([str(tmp_path), *argv])
    return task_from_args(cls, make_bench(tmp_path), tmp_path, args)


def test_step_reports_failure_once(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    demo = task(tmp_path)

    with pytest.raises(RuntimeError):
        demo.fail()
    demo.step_failed()

    output = capsys.readouterr().out
    assert output.count("STEP work,") == 1
    assert output.count("STEP-FAILED work,") == 1


def test_successful_step_does_not_leave_stale_current_step(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    demo = task(tmp_path)

    demo.succeed()
    demo.step_failed()

    output = capsys.readouterr().out
    assert output.count("STEP work,") == 1
    assert "STEP-FAILED" not in output


def test_manual_step_can_still_report_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    demo = task(tmp_path)

    demo.step("manual", "Manual step")
    demo.step_failed()

    output = capsys.readouterr().out
    assert output.count("STEP manual,") == 1
    assert output.count("STEP-FAILED manual,") == 1


@dataclass(kw_only=True)
class ParserTask(Task):
    command: ClassVar[str] = "parser-test"

    site: str
    apps: Annotated[list[str], Arg(help="Apps")]
    loud: bool = False
    count: int = 1
    tags: list[str] | None = None
    mode: Literal["fast", "safe"] = "fast"
    secret: Annotated[str, Arg(cli=False)] = "hidden"


def test_task_positional_fields_are_required(tmp_path: Path) -> None:
    parsed = parse_task(ParserTask, tmp_path, ["site.localhost", "frappe"])

    assert parsed.site == "site.localhost"
    assert parsed.apps == ["frappe"]


def test_task_bool_field_becomes_store_true_flag(tmp_path: Path) -> None:
    assert parse_task(ParserTask, tmp_path, ["site.localhost", "frappe", "--loud"]).loud is True
    assert parse_task(ParserTask, tmp_path, ["site.localhost", "frappe"]).loud is False


def test_task_optional_field_uses_kebab_case_flag_and_default(tmp_path: Path) -> None:
    assert parse_task(ParserTask, tmp_path, ["site.localhost", "frappe", "--count", "3"]).count == 3
    assert parse_task(ParserTask, tmp_path, ["site.localhost", "frappe"]).count == 1


def test_task_optional_list_field_uses_nargs_star(tmp_path: Path) -> None:
    parsed = parse_task(ParserTask, tmp_path, ["site.localhost", "frappe", "--tags", "x", "y"])

    assert parsed.tags == ["x", "y"]
    assert parse_task(ParserTask, tmp_path, ["site.localhost", "frappe"]).tags is None


def test_task_literal_field_becomes_choices(tmp_path: Path) -> None:
    assert parse_task(ParserTask, tmp_path, ["site.localhost", "frappe", "--mode", "safe"]).mode == "safe"
    with pytest.raises(SystemExit):
        parse_task(ParserTask, tmp_path, ["site.localhost", "frappe", "--mode", "wrong"])


def test_task_cli_false_field_is_not_a_cli_argument(tmp_path: Path) -> None:
    assert parse_task(ParserTask, tmp_path, ["site.localhost", "frappe"]).secret == "hidden"
    with pytest.raises(SystemExit):
        parse_task(ParserTask, tmp_path, ["site.localhost", "frappe", "--secret", "x"])


def test_task_secret_field_still_counts_as_required_submit_arg() -> None:
    @dataclass(kw_only=True)
    class SecretTask(Task):
        command: ClassVar[str] = "secret-test"

        site: str
        admin_password: Annotated[str, Arg(cli=False)]

    assert required_task_args(SecretTask) == ["site", "admin_password"]


def test_task_can_declare_required_submit_arg_without_constructor_field() -> None:
    @dataclass(kw_only=True)
    class MetadataTask(Task):
        command: ClassVar[str] = "metadata-test"
        required_submit_args: ClassVar[tuple[str, ...]] = ("name",)

        repo: str = ""

    assert required_task_args(MetadataTask) == ["name"]


def test_task_argv_suffix_is_derived_from_task_fields() -> None:
    assert task_argv_suffix(
        ParserTask,
        {
            "site": "site.localhost",
            "apps": ["frappe", "erpnext"],
            "loud": True,
            "count": 3,
            "tags": ["alpha", "beta"],
            "mode": "safe",
            "secret": "hidden",
        },
    ) == [
        "site.localhost",
        "frappe",
        "erpnext",
        "--loud",
        "--count",
        "3",
        "--tags",
        "alpha",
        "beta",
        "--mode",
        "safe",
    ]
