from __future__ import annotations

import argparse
from dataclasses import fields
from typing import TYPE_CHECKING

from pilot.commands import Command
from pilot.internal.cli.fields import NOT_PROVIDED, add_argument, arg_fields, value_from_namespace

if TYPE_CHECKING:
    from pilot.core.bench import Bench


def add_command_arguments(cls: type[Command], parser: argparse.ArgumentParser) -> None:
    """Add one argparse argument per CLI field of the command."""
    for cli_field in command_fields(cls):
        add_argument(parser, cli_field)


def command_from_args(cls: type[Command], args: argparse.Namespace, bench: Bench | None) -> Command:
    """Build the command from parsed arguments, leaving defaults to the dataclass."""
    kwargs = {}
    for cli_field in command_fields(cls):
        value = value_from_namespace(args, cli_field)
        if value is NOT_PROVIDED:
            # The dataclass supplies the default, so a factory runs here only when
            # the command is actually built.
            continue
        kwargs[cli_field.name] = value
    if any(field.name == "skip_confirm" for field in fields(cls)):
        kwargs["skip_confirm"] = args.yes
    return cls(bench=bench, **kwargs)


def command_fields(cls: type[Command]) -> list:
    """The command's dataclass fields that appear on the command line."""
    return [
        field
        for field in arg_fields(
            cls, exclude=frozenset({"bench", "skip_confirm"}), hint_namespace={"Bench": object}
        )
        if field.metadata.cli
    ]
