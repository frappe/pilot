from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.internal.cli.fields import (
    add_argument,
    arg_fields,
    strip_optional,
    to_kebab_case,
    value_from_namespace,
)
from pilot.tasks import Task

if TYPE_CHECKING:
    from pilot.core.bench import Bench

_EXCLUDED_FIELDS = frozenset({"bench", "bench_root"})
_HINT_NAMESPACE = {"Bench": object}


def apply_task_secrets(args: argparse.Namespace) -> None:
    """Load secret arguments from the task's secrets file into the parsed arguments."""
    secret_path = os.environ.get("BENCH_TASK_SECRETS_FILE")
    if not secret_path:
        return
    for key, value in json.loads(Path(secret_path).read_text()).items():
        setattr(args, key, value)


def required_task_args(cls: type[Task]) -> list[str]:
    """The argument names a task needs at submit time."""
    required = [field.name for field in task_fields(cls) if not field.has_default]
    return [*required, *cls.required_submit_args]


def task_parser(cls: type[Task]) -> argparse.ArgumentParser:
    """The argument parser for a task module run with python -m."""
    parser = argparse.ArgumentParser()
    parser.add_argument("bench_root")
    for arg_field in task_fields(cls):
        if arg_field.metadata.cli:
            add_argument(parser, arg_field)
        else:
            parser.set_defaults(**{arg_field.name: arg_field.default if arg_field.has_default else None})
    return parser


def task_from_args(
    cls: type[Task],
    bench: Bench,
    bench_root: Path,
    args: argparse.Namespace,
) -> Task:
    """Build the task from parsed arguments."""
    from pilot.internal.cli.fields import NOT_PROVIDED

    kwargs = {}
    for arg_field in task_fields(cls):
        value = value_from_namespace(args, arg_field)
        if value is NOT_PROVIDED:
            # The dataclass supplies the default, so a factory runs here only when
            # the task is actually built.
            continue
        kwargs[arg_field.name] = value
    return cls(bench=bench, bench_root=bench_root, **kwargs)


def task_argv_suffix(cls: type[Task], args: dict) -> list[str]:
    """The command-line arguments that pass these task arguments to the task module."""
    argv: list[str] = []
    for arg_field in task_fields(cls):
        if not arg_field.metadata.cli or arg_field.name not in args:
            continue
        value = args[arg_field.name]
        if is_positional_arg(arg_field):
            argv += argv_values(value)
        elif strip_optional(arg_field.hint) is bool:
            argv += bool_flag_argv(arg_field, value)
        elif value:
            argv.append(long_flag(arg_field.name))
            argv += argv_values(value)
    return argv


def bool_flag_argv(arg_field, value: bool) -> list[str]:
    """The flag for true, or its --no- form for false when the flag defaults to on."""
    if value:
        return [long_flag(arg_field.name)]
    return [long_flag(f"no_{arg_field.name}")] if arg_field.default is True else []


def run_task_main(cls: type[Task]) -> None:
    """Parse the arguments, build the task and run it."""
    from pilot.core.bench import Bench

    args = task_parser(cls).parse_args()
    apply_task_secrets(args)
    bench_root = Path(args.bench_root)
    bench = Bench(bench_root)
    run_task(task_from_args(cls, bench, bench_root, args))


def run_task(task: Task) -> None:
    """Run the task and mark the current step failed when it exits with an error."""
    try:
        task.run()
    except SystemExit as exit_error:
        if exit_error.code not in (0, None):
            task.step_failed()
        raise
    except Exception:
        task.step_failed()
        raise
    else:
        if task.has_done_step:
            task.done()


def task_fields(cls: type[Task]) -> list:
    """The task's dataclass fields that are task arguments."""
    return arg_fields(cls, exclude=_EXCLUDED_FIELDS, hint_namespace=_HINT_NAMESPACE)


def is_positional_arg(arg_field) -> bool:
    """Whether the field is a positional command-line argument."""
    return not arg_field.has_default and not arg_field.metadata.required


def argv_values(value) -> list[str]:
    """The command-line values for one argument."""
    if isinstance(value, dict):
        return [json.dumps(value)]
    return [str(item) for item in value] if isinstance(value, list) else [str(value)]


def long_flag(name: str) -> str:
    """The long flag for an argument name."""
    return f"--{to_kebab_case(name)}"
