from __future__ import annotations

import logging
import os
import runpy
import sys


class LinkSafeLogs:
    """Opens the log files below a bench one folder at a time, refusing a link at each step. A sandboxed
    workload can write in sites/, so it can plant a link where Frappe logs, such as sites/<site>/logs."""

    folder_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC

    def __init__(self, bench_root: str) -> None:
        self.root = os.path.realpath(bench_root)
        self.root_descriptor = os.open(self.root, self.folder_flags)

    def install(self) -> None:
        """Make every `logging.FileHandler` open its file through `open_log`."""
        open_outside = logging.FileHandler._open
        guard = self

        def open_log(handler: logging.FileHandler):
            return guard.open_log(handler) or open_outside(handler)

        logging.FileHandler._open = open_log

    def open_log(self, handler: logging.FileHandler):
        """The handler's open file, or None when the file is outside the bench."""
        relative = os.path.relpath(os.path.abspath(handler.baseFilename), self.root)
        if relative == os.pardir or relative.startswith(os.pardir + os.sep):
            return None
        flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if "a" in handler.mode else os.O_TRUNC)
        descriptor = self.open_beneath(relative, flags)
        return os.fdopen(descriptor, handler.mode, encoding=handler.encoding, errors=handler.errors)

    def open_beneath(self, relative: str, flags: int) -> int:
        """Open `relative` below the bench, refusing a link in any part of it."""
        *folders, name = relative.split(os.sep)
        descriptor = os.dup(self.root_descriptor)
        try:
            for folder in folders:
                child = os.open(folder, self.folder_flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            return os.open(name, flags | os.O_NOFOLLOW | os.O_CLOEXEC, 0o666, dir_fd=descriptor)
        finally:
            os.close(descriptor)


class UnsandboxedRun:
    """`python -P unsandboxed_entry.py <bench> -c <code> | -m <module> ...`: runs it as `python -P` would, with
    link-safe logs. The bench venv Python runs this file, so it imports only the standard library."""

    def __init__(self, argv: list[str]) -> None:
        self.bench_root, self.option, self.target, *self.arguments = argv

    def start(self) -> None:
        """Install the log guard, then run the target."""
        LinkSafeLogs(self.bench_root).install()
        if self.option == "-m":
            sys.argv = [self.target, *self.arguments]
            runpy.run_module(self.target, run_name="__main__", alter_sys=True)
        elif self.option == "-c":
            sys.argv = ["-c", *self.arguments]
            exec(compile(self.target, "<string>", "exec"), {"__name__": "__main__"})
        else:
            raise SystemExit(f"Expected -c or -m, got {self.option!r}.")


if __name__ == "__main__":
    UnsandboxedRun(sys.argv[1:]).start()
