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
    secret_path = os.environ.get("BENCH_TASK_SECRETS_FILE")
    if not secret_path:
        return
    for key, value in json.loads(Path(secret_path).read_text()).items():
        setattr(args, key, value)


def required_task_args(cls: type[Task]) -> list[str]:
    required = [field.name for field in task_fields(cls) if not field.has_default]
    return [*required, *cls.required_submit_args]


def task_parser(cls: type[Task]) -> argparse.ArgumentParser:
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
    kwargs = {arg_field.name: value_from_namespace(args, arg_field) for arg_field in task_fields(cls)}
    return cls(bench=bench, bench_root=bench_root, **kwargs)


def task_argv_suffix(cls: type[Task], args: dict) -> list[str]:
    argv: list[str] = []
    for arg_field in task_fields(cls):
        if not arg_field.metadata.cli or arg_field.name not in args:
            continue
        value = args[arg_field.name]
        if is_positional_arg(arg_field):
            argv += argv_values(value)
        elif strip_optional(arg_field.hint) is bool:
            if value:
                argv.append(long_flag(arg_field.name))
        elif value:
            argv.append(long_flag(arg_field.name))
            argv += argv_values(value)
    return argv


def run_task_main(cls: type[Task]) -> None:
    from pilot.core.bench import Bench

    args = task_parser(cls).parse_args()
    apply_task_secrets(args)
    bench_root = Path(args.bench_root)
    bench = Bench(bench_root)
    run_task(task_from_args(cls, bench, bench_root, args))


def run_task(task: Task) -> None:
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
    return arg_fields(cls, exclude=_EXCLUDED_FIELDS, hint_namespace=_HINT_NAMESPACE)


def is_positional_arg(arg_field) -> bool:
    return not arg_field.has_default and not arg_field.metadata.required


def argv_values(value) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else [str(value)]


def long_flag(name: str) -> str:
    return f"--{to_kebab_case(name)}"
