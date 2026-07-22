from __future__ import annotations

import argparse
from dataclasses import MISSING, fields
from typing import Annotated, Any, Literal, NamedTuple, get_args, get_origin, get_type_hints

from pilot.commands import Arg


class ArgField(NamedTuple):
    name: str
    hint: Any
    metadata: Arg
    has_default: bool
    default: Any


def arg_fields(
    cls: type, *, exclude: frozenset[str] = frozenset(), hint_namespace: dict | None = None
) -> list[ArgField]:
    """Return dataclass fields with resolved Arg metadata."""
    hints = get_type_hints(cls, include_extras=True, localns=hint_namespace or {})
    result = []
    for field in fields(cls):
        if field.name in exclude:
            continue
        hint = hints[field.name]
        metadata = Arg()
        if get_origin(hint) is Annotated:
            hint, *extras = get_args(hint)
            metadata = next((extra for extra in extras if isinstance(extra, Arg)), metadata)
        if field.default is not MISSING:
            has_default, default = True, field.default
        elif field.default_factory is not MISSING:
            has_default, default = True, field.default_factory()
        else:
            has_default, default = False, None
        result.append(ArgField(field.name, hint, metadata, has_default, default))
    return result


def add_argument(parser: argparse.ArgumentParser, arg_field: ArgField) -> None:
    base_hint = strip_optional(arg_field.hint)
    kwargs = argument_kwargs(arg_field, base_hint)

    if base_hint == tuple[str, ...]:
        parser.add_argument(arg_field.name, nargs=argparse.REMAINDER, **kwargs)
        return
    if base_hint is bool:
        parser.add_argument(
            *option_flags(arg_field),
            action="store_true",
            default=arg_field.default,
            **kwargs,
        )
        return

    if not arg_field.has_default and not arg_field.metadata.required:
        parser.add_argument(arg_field.name, **kwargs)
        return
    if arg_field.has_default:
        kwargs["default"] = arg_field.default
    else:
        kwargs["required"] = True
    parser.add_argument(*option_flags(arg_field), **kwargs)


def argument_kwargs(arg_field: ArgField, base_hint: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"help": arg_field.metadata.help} if arg_field.metadata.help else {}
    if arg_field.metadata.metavar:
        kwargs["metavar"] = arg_field.metadata.metavar
    if base_hint in (bool, tuple[str, ...]):
        return kwargs
    if get_origin(base_hint) is Literal:
        kwargs["choices"] = get_args(base_hint)
    elif get_origin(base_hint) is list:
        update_list_kwargs(kwargs, arg_field, base_hint)
    elif base_hint is not str:
        kwargs["type"] = base_hint
    return kwargs


def update_list_kwargs(kwargs: dict[str, Any], arg_field: ArgField, base_hint: Any) -> None:
    (item_type,) = get_args(base_hint)
    kwargs["nargs"] = "*" if arg_field.has_default else "+"
    if item_type is not str:
        kwargs["type"] = item_type


def option_flags(arg_field: ArgField) -> list[str]:
    flags = [f"--{to_kebab_case(arg_field.name)}"]
    if arg_field.metadata.short:
        flags.append(f"-{arg_field.metadata.short}")
    return flags


def value_from_namespace(args: argparse.Namespace, arg_field: ArgField) -> Any:
    value = getattr(args, arg_field.name)
    return tuple(value) if strip_optional(arg_field.hint) == tuple[str, ...] else value


def strip_optional(hint: Any) -> Any:
    union_args = get_args(hint)
    if type(None) in union_args:
        return next(candidate for candidate in union_args if candidate is not type(None))
    return hint


def to_kebab_case(name: str) -> str:
    return name.replace("_", "-")
