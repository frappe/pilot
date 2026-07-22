from __future__ import annotations

import argparse
from dataclasses import fields
from typing import TYPE_CHECKING

from pilot.commands import Command
from pilot.internal.cli.fields import add_argument, arg_fields, value_from_namespace

if TYPE_CHECKING:
    from pilot.core.bench import Bench

_EXCLUDED_FIELDS = frozenset({"bench", "skip_confirm"})
_HINT_NAMESPACE = {"Bench": object}


def add_command_arguments(cls: type[Command], parser: argparse.ArgumentParser) -> None:
    for cli_field in command_fields(cls):
        add_argument(parser, cli_field)


def command_from_args(cls: type[Command], args: argparse.Namespace, bench: Bench | None) -> Command:
    kwargs = {cli_field.name: value_from_namespace(args, cli_field) for cli_field in command_fields(cls)}
    if any(field.name == "skip_confirm" for field in fields(cls)):
        kwargs["skip_confirm"] = args.yes
    return cls(bench=bench, **kwargs)


def command_fields(cls: type[Command]) -> list:
    return [
        field
        for field in arg_fields(cls, exclude=_EXCLUDED_FIELDS, hint_namespace=_HINT_NAMESPACE)
        if field.metadata.cli
    ]
