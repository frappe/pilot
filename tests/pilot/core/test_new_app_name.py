import pytest

from pilot.core.app import App, NewAppOptions
from pilot.exceptions import BenchError


def test_normalizes_spaces_and_hyphens():
    assert App._normalize_new_app_name("My Cool-App") == "my_cool_app"


@pytest.mark.parametrize("bad", ["9app", "app.name", "", "/tmp/demo", "foo/bar", "../evil", "a b/c"])
def test_rejects_invalid_names(bad):
    with pytest.raises(BenchError):
        App._normalize_new_app_name(bad)


def test_answers_are_ordered_to_match_make_app_prompts():
    options = NewAppOptions(
        title="", description="Hi", publisher="Tanmoy", email="t@f.io",
        license="mit", branch="develop", github_workflow=True,
    )
    assert options.as_answers() == "\nHi\nTanmoy\nt@f.io\nmit\ny\ndevelop\n"
