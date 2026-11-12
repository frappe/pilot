from __future__ import annotations

from pathlib import Path

from pilot.internal.jsonl_log import JsonLines


def test_tail_returns_full_content_when_file_smaller_than_block(tmp_path: Path) -> None:
    path = tmp_path / "small.log"
    path.write_text("a\nb\nc\n")
    assert JsonLines(path).read_tail_text(10) == "a\nb\nc\n"


def test_tail_returns_empty_string_for_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.log"
    path.write_text("")
    assert JsonLines(path).read_tail_text(5) == ""


def test_tail_grows_window_until_min_lines_found(tmp_path: Path) -> None:
    path = tmp_path / "big.log"
    path.write_text("".join(f"line-{i}\n" for i in range(1000)))

    text = JsonLines(path, block_size=16).read_tail_text(5)

    assert text.count("\n") >= 5
    assert text.splitlines()[-1] == "line-999"
    assert len(text) < path.stat().st_size


def test_tail_never_reads_more_than_the_file_size(tmp_path: Path) -> None:
    path = tmp_path / "tiny.log"
    path.write_text("only-one-line\n")
    assert JsonLines(path, block_size=4).read_tail_text(1000) == "only-one-line\n"


def test_lines_across_block_boundaries_come_back_whole_and_newest_first(tmp_path: Path) -> None:
    path = tmp_path / "records.jsonl"
    lines = [f"line-{i:03d}" for i in range(50)]
    path.write_text("\n".join(lines) + "\n")

    assert [line.decode() for line in JsonLines(path, block_size=8).read_lines_newest_first()] == list(
        reversed(lines)
    )


def test_a_last_line_without_newline_and_blank_lines_are_handled(tmp_path: Path) -> None:
    path = tmp_path / "records.jsonl"
    path.write_text('{"n": 1}\n\n{"n": 2}')

    assert list(JsonLines(path, block_size=4).read_newest_first()) == [{"n": 2}, {"n": 1}]


def test_bad_json_and_non_objects_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "records.jsonl"
    path.write_text('{"n": 1}\n{"n": 2\n[1, 2]\n"text"\n{"n": 3}\n')

    assert list(JsonLines(path).read_newest_first()) == [{"n": 3}, {"n": 1}]


def test_a_missing_file_reads_as_empty(tmp_path: Path) -> None:
    lines = JsonLines(tmp_path / "missing.jsonl")

    assert list(lines.read_newest_first()) == []
    assert lines.first_record is None


def test_first_record_is_the_oldest_line(tmp_path: Path) -> None:
    path = tmp_path / "records.jsonl"
    path.write_text('{"n": 1}\n{"n": 2}\n')

    assert JsonLines(path).first_record == {"n": 1}
