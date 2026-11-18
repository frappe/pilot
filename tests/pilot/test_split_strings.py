"""A string split over lines without parentheses keeps only its first line; the rest is a no-op statement."""

from __future__ import annotations

import ast
from itertools import pairwise
from pathlib import Path

from pilot.managers.waf import CRS_VERSION, WafManager

ROOT = Path(__file__).parents[2]


def is_string(node: ast.AST | None) -> bool:
    return isinstance(node, ast.JoinedStr) or (isinstance(node, ast.Constant) and isinstance(node.value, str))


def split_strings(tree: ast.AST) -> list[int]:
    """Lines of string statements that directly follow a string assignment."""
    lines = []
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for previous, statement in pairwise(body):
            if not (isinstance(statement, ast.Expr) and is_string(statement.value)):
                continue
            if isinstance(previous, (ast.Assign, ast.AnnAssign)) and is_string(previous.value):
                lines.append(statement.lineno)
    return lines


def test_no_string_is_split_without_parentheses() -> None:
    sources = [
        path
        for folder in ("pilot", "admin")
        for path in sorted((ROOT / folder).rglob("*.py"))
        if not {"_vendor", "node_modules"} & set(path.parts)
    ]
    found = [
        f"{path.relative_to(ROOT)}:{line}"
        for path in sources
        for line in split_strings(ast.parse(path.read_text()))
    ]
    assert found == []


def test_crs_url_names_the_pinned_release_asset() -> None:
    assert WafManager.crs_url == (
        "https://github.com/coreruleset/coreruleset/releases/download/"
        f"v{CRS_VERSION}/coreruleset-{CRS_VERSION}-minimal.tar.gz"
    )
