"""Tests for the mini template renderer."""

from __future__ import annotations

import contextlib

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pilot.internal.template import Template


def render(template: str, **context: object) -> str:
    return Template(template).render(**context)


def test_renders_literal_text() -> None:
    assert render("hello world") == "hello world"


def test_substitutes_expression() -> None:
    assert render("hello {{ name }}", name="frappe") == "hello frappe"


def test_expression_uses_safe_globals() -> None:
    assert render("{{ len(items) }}", items=[1, 2, 3]) == "3"


def test_none_expression_renders_empty_string() -> None:
    assert render("[{{ value }}]", value=None) == "[]"


def test_undefined_variable_raises() -> None:
    with pytest.raises(NameError):
        render("{{ missing }}")


def test_builtins_are_not_available() -> None:
    with pytest.raises(NameError):
        render("{{ open('/etc/passwd') }}")


def test_if_true_keeps_body() -> None:
    assert render("{% if show %}yes{% endif %}", show=True) == "yes"


def test_if_false_drops_body() -> None:
    assert render("{% if show %}yes{% endif %}", show=False) == ""


def test_if_else_picks_branch() -> None:
    template = "{% if show %}yes{% else %}no{% endif %}"

    assert render(template, show=True) == "yes"
    assert render(template, show=False) == "no"


def test_if_elif_else_picks_matching_branch() -> None:
    template = "{% if n == 1 %}one{% elif n == 2 %}two{% else %}many{% endif %}"

    assert render(template, n=1) == "one"
    assert render(template, n=2) == "two"
    assert render(template, n=3) == "many"


def test_for_loop_renders_each_item() -> None:
    template = "{% for item in items %}{{ item }},{% endfor %}"

    assert render(template, items=[1, 2, 3]) == "1,2,3,"


def test_for_loop_variable_does_not_leak_outside_body() -> None:
    template = "{% for item in items %}{{ item }}{% endfor %}{{ len(items) }}"

    assert render(template, items=[1, 2]) == "122"


def test_block_tag_alone_on_line_swallows_newline() -> None:
    template = "a\n{% if show %}\nb\n{% endif %}\nc"

    assert render(template, show=True) == "a\nb\nc"


def test_comment_renders_nothing() -> None:
    assert render("a{# note #}b") == "ab"


def test_comment_alone_on_line_swallows_newline() -> None:
    assert render("a\n{# note #}\nb") == "a\nb"


def test_comment_may_span_tags_and_lines() -> None:
    assert render("x{# {% if %} {{ y }} \n still comment #}z") == "xz"


def test_unknown_statement_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unknown statement"):
        Template("{% bogus %}")


def test_missing_endif_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Missing"):
        Template("{% if true %}body")


def test_from_path_renders_file_contents(tmp_path) -> None:
    path = tmp_path / "greeting.txt"
    path.write_text("hi {{ name }}")

    assert Template.from_path(path).render(name="pilot") == "hi pilot"


def test_empty_expression_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Empty expression"):
        Template("{{ }}")


def test_invalid_for_statement_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Invalid for statement"):
        Template("{% for x %}{% endfor %}")


def test_stray_elif_raises_value_error() -> None:
    with pytest.raises(ValueError):
        Template("{% elif x %}body{% endif %}")


def test_stray_else_raises_value_error() -> None:
    with pytest.raises(ValueError):
        Template("{% else %}body{% endif %}")


def test_trailing_endfor_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unknown statement"):
        Template("{% endfor %}")


# Grammar tokens a malformed or hostile template could combine in any order.
FUZZ_ALPHABET = [
    "{{",
    "}}",
    "{%",
    "%}",
    " if ",
    " for ",
    " in ",
    " endif ",
    " endfor ",
    " elif ",
    " else ",
    "x",
    "1",
    "==",
    "range(3)",
    "\n",
    "text",
    ".",
    "(",
    ")",
    "[",
    "]",
    ",",
    "items",
]

# Malformed templates and expressions are expected to fail loudly with one of
# these, never with an unhandled crash (IndexError, RecursionError, ...).
FUZZ_EXPECTED_ERRORS = (ValueError, TypeError, NameError, SyntaxError)

token_soup = st.lists(st.sampled_from(FUZZ_ALPHABET), max_size=20).map("".join)


@settings(max_examples=500)
@given(token_soup)
def test_fuzz_random_token_soup_never_crashes_unexpectedly(source: str) -> None:
    with contextlib.suppress(*FUZZ_EXPECTED_ERRORS):
        Template(source).render(x=1, items=[1, 2, 3])


@settings(max_examples=500)
@given(st.text(max_size=60))
def test_fuzz_random_text_never_crashes_unexpectedly(source: str) -> None:
    with contextlib.suppress(*FUZZ_EXPECTED_ERRORS):
        Template(source).render()
