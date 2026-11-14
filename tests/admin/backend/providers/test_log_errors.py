from __future__ import annotations

from pathlib import Path

from admin.backend.providers.server.logs import LogProvider


def test_info_lines_on_stderr_are_not_errors(tmp_path: Path) -> None:
    log = tmp_path / "web.log.error.log"
    log.write_text("2026-11-12 13:21:04,309 INFO uvicorn.error Started server process [36887]\n")
    assert not LogProvider.has_recent_errors(log)

    log.write_text(log.read_text() + "Traceback (most recent call last):\n")
    assert LogProvider.has_recent_errors(log)
