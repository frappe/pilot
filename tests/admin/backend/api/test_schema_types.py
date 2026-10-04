from __future__ import annotations

import importlib.util
from enum import StrEnum
from pathlib import Path
from types import ModuleType
from typing import Literal, NotRequired, TypedDict

import pytest

_SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "generate_ts_types.py"
_spec = importlib.util.spec_from_file_location("generate_ts_types", _SCRIPT)
assert _spec and _spec.loader
generator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(generator)


class Color(StrEnum):
    RED = "red"
    BLUE = "blue"


class Owner(TypedDict):
    name: str


class Tag(TypedDict):
    label: str


class Widget(TypedDict):
    owner: Owner
    tags: list[Tag]
    color: Color
    kind: Literal["a", "b"]
    size: int | None
    config: dict
    labels: dict[str, str]
    note: NotRequired[str]


def test_renders_fields_imports_and_inlined_types():
    source = generator.render_all([((Owner,), "people.ts"), ((Widget,), "shopWidgets.ts")])["shopWidgets.ts"]

    assert "import type { Owner } from '@/types/people'" in source
    assert "export interface Tag {\n  label: string\n}" in source
    assert "  owner: Owner\n  tags: Tag[]\n" in source
    assert "  color: 'red' | 'blue'\n  kind: 'a' | 'b'\n  size: number | null\n" in source
    assert "  config: Record<string, unknown>\n  labels: Record<string, string>\n" in source
    assert "  note?: string\n" in source


def test_module_root_emits_the_types_it_defines():
    module = ModuleType("routes")

    class Local(TypedDict):
        id: int

    Local.__module__ = module.__name__
    module.__dict__.update(Local=Local, Owner=Owner)

    source = generator.render_all([((module,), "routes.ts")])["routes.ts"]

    assert "export interface Local {\n  id: number\n}" in source
    assert "Owner" not in source


def test_rejects_a_type_in_two_targets():
    with pytest.raises(ValueError, match="more than one"):
        generator.render_all([((Owner,), "one.ts"), ((Owner,), "two.ts")])


def test_rejects_unmapped_types():
    class Broken(TypedDict):
        path: Path

    with pytest.raises(TypeError, match="No TypeScript mapping"):
        generator.render_all([((Broken,), "broken.ts")])


def test_every_api_target_renders():
    rendered = generator.render_all(generator._TARGETS)

    assert {"common.ts", "sites.ts", "tasks.ts"} <= set(rendered)
