from __future__ import annotations

from pathlib import Path

from admin.backend.providers.logs import _read_tail_text


def test_returns_full_content_when_file_smaller_than_block(tmp_path: Path) -> None:
    path = tmp_path / "small.log"
    path.write_text("a\nb\nc\n")
    assert _read_tail_text(path, min_lines=10) == "a\nb\nc\n"


def test_returns_empty_string_for_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.log"
    path.write_text("")
    assert _read_tail_text(path, min_lines=5) == ""


def test_grows_window_until_min_lines_found(tmp_path: Path) -> None:
    path = tmp_path / "big.log"
    path.write_text("".join(f"line-{i}\n" for i in range(1000)))

    text = _read_tail_text(path, min_lines=5, block_size=16)

    assert text.count("\n") >= 5
    assert text.splitlines()[-1] == "line-999"
    assert len(text) < path.stat().st_size


def test_never_reads_more_than_the_file_size(tmp_path: Path) -> None:
    path = tmp_path / "tiny.log"
    path.write_text("only-one-line\n")
    assert _read_tail_text(path, min_lines=1000, block_size=4) == "only-one-line\n"
