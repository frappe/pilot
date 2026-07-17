from __future__ import annotations

import argparse
import sys
from dataclasses import MISSING, dataclass, fields
from enum import Enum, auto
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal, NamedTuple, get_args, get_origin, get_type_hints

from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.core.bench import Bench

# Discovery imports every pilot/commands/ module (pilot/registry.py), so this
# stands in for Bench when resolving field types — real import would pull
# pilot.core into every CLI invocation just to build --help.
_HINT_NAMESPACE = {"Bench": object}


class BenchMode(Enum):
    """How the registry resolves Command.bench before dispatch."""

    NONE = auto()
    OPTIONAL = auto()
    AUTO = auto()
    EXPLICIT = auto()


@dataclass(frozen=True)
class Arg:
    """Overrides the CLI shape inferred from a Command field's type/default.

    help: shown in --help. short: adds -x alongside the long flag.
    metavar: displayed name, when it should differ from the field name.
    required: forces a --flag (rather than a positional) to be mandatory.
    cli=False: constructor-only, never exposed as a CLI argument.
    """

    help: str = ""
    short: str | None = None
    cli: bool = True
    metavar: str | None = None
    required: bool = False


@dataclass
class Command:
    """Base class for a CLI command; the registry discovers every subclass
    that sets name and wires it into the parser (registry.py; walkthrough
    in docs/architecture.md).

    Declare a command as a dataclass — each field becomes a CLI argument:

        @dataclass(kw_only=True)
        class GreetCommand(Command):
            name: ClassVar[str] = "greet"
            help: ClassVar[str] = "Print a greeting."

            who: Annotated[str, Arg(help="Name to greet.")]
            loud: bool = False

            def run(self) -> None:
                greeting = f"Hello, {self.who}!"
                self.print(greeting.upper() if self.loud else greeting)

    Field -> argument:
        no default                    -> required positional
        has a default                 -> --kebab-case flag
        bool                          -> --flag (store_true)
        list[T]                       -> nargs ("+" positional, "*" optional)
        tuple[str, ...]                -> raw passthrough (argparse.REMAINDER)
        Literal["a", "b"]              -> choices
        Annotated[T, Arg(cli=False)]   -> constructor-only, never a CLI flag

    bench/skip_confirm are always excluded — bench is injected by the
    registry, skip_confirm reads the global -y/--yes flag. Put cross-field
    defaulting or validation in __post_init__.
    """

    name: ClassVar[str]
    help: ClassVar[str] = ""
    group: ClassVar[str | None] = None
    bench_mode: ClassVar[BenchMode] = BenchMode.AUTO
    supports_all_benches: ClassVar[bool] = False

    bench: Bench | None = None

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        for cli_field in _cli_fields(cls):
            _add_argument(parser, cli_field)

    @classmethod
    def from_args(cls, args: argparse.Namespace, bench: Bench | None) -> "Command":
        kwargs = {cli_field.name: _value_from_namespace(args, cli_field) for cli_field in _cli_fields(cls)}
        if any(field.name == "skip_confirm" for field in fields(cls)):
            kwargs["skip_confirm"] = args.yes
        return cls(bench=bench, **kwargs)

    def run(self) -> None:
        raise NotImplementedError

    def print(self, message: str) -> None:
        print(message)
        sys.stdout.flush()

    def confirm(self, prompt: str, *, skip: bool = False, error: type[Exception] = BenchError) -> None:
        if skip:
            return
        try:
            answer = input(f"{prompt} [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer not in ("y", "yes"):
            raise error("Aborted.")


class _CliField(NamedTuple):
    name: str
    hint: Any
    metadata: Arg
    has_default: bool
    default: Any


_EXCLUDED_FIELDS = frozenset({"bench", "skip_confirm"})


def _cli_fields(command_class: type) -> list[_CliField]:
    hints = get_type_hints(command_class, include_extras=True, localns=_HINT_NAMESPACE)
    result = []
    for field in fields(command_class):
        if field.name in _EXCLUDED_FIELDS:
            continue
        hint = hints[field.name]
        metadata = Arg()
        if get_origin(hint) is Annotated:
            hint, *extras = get_args(hint)
            metadata = next((extra for extra in extras if isinstance(extra, Arg)), metadata)
        if not metadata.cli:
            continue
        if field.default is not MISSING:
            has_default, default = True, field.default
        elif field.default_factory is not MISSING:
            has_default, default = True, field.default_factory()
        else:
            has_default, default = False, None
        result.append(_CliField(field.name, hint, metadata, has_default, default))
    return result


def _add_argument(parser: argparse.ArgumentParser, cli_field: _CliField) -> None:
    kwargs: dict[str, Any] = {"help": cli_field.metadata.help} if cli_field.metadata.help else {}
    if cli_field.metadata.metavar:
        kwargs["metavar"] = cli_field.metadata.metavar
    base_hint = _strip_optional(cli_field.hint)

    if base_hint == tuple[str, ...]:
        parser.add_argument(cli_field.name, nargs=argparse.REMAINDER, **kwargs)
        return
    if base_hint is bool:
        flags = [f"--{_to_kebab_case(cli_field.name)}"]
        if cli_field.metadata.short:
            flags.append(f"-{cli_field.metadata.short}")
        parser.add_argument(*flags, action="store_true", default=cli_field.default, **kwargs)
        return
    if get_origin(base_hint) is Literal:
        kwargs["choices"] = get_args(base_hint)
    elif get_origin(base_hint) is list:
        (item_type,) = get_args(base_hint)
        kwargs["nargs"] = "*" if cli_field.has_default else "+"
        if item_type is not str:
            kwargs["type"] = item_type
    elif base_hint is not str:
        kwargs["type"] = base_hint

    if not cli_field.has_default and not cli_field.metadata.required:
        parser.add_argument(cli_field.name, **kwargs)
        return
    flags = [f"--{_to_kebab_case(cli_field.name)}"]
    if cli_field.metadata.short:
        flags.append(f"-{cli_field.metadata.short}")
    if cli_field.has_default:
        kwargs["default"] = cli_field.default
    else:
        kwargs["required"] = True
    parser.add_argument(*flags, **kwargs)


def _value_from_namespace(args: argparse.Namespace, cli_field: _CliField) -> Any:
    value = getattr(args, cli_field.name)
    return tuple(value) if _strip_optional(cli_field.hint) == tuple[str, ...] else value


def _strip_optional(hint: Any) -> Any:
    union_args = get_args(hint)
    if type(None) in union_args:
        return next(candidate for candidate in union_args if candidate is not type(None))
    return hint


def _to_kebab_case(name: str) -> str:
    return name.replace("_", "-")
