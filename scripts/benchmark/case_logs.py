"""Log inventory, tail, and task output benchmarks."""

from __future__ import annotations

from admin.backend.providers.logs import LogProvider
from benchmark.fixtures import Fixture
from benchmark.harness import Case
from pilot.managers.task import TaskReader


def _first_task_id(fixture: Fixture) -> str:
    return sorted(path.name for path in (fixture.bench_root / "tasks").iterdir() if path.is_dir())[0]


CASES = [
    Case(
        name="logs.list",
        group="logs",
        description="LogProvider.get_all(): stat and `wc -l` every log file",
        fn=lambda fixture: LogProvider(fixture.bench_root).get_all(),
        iterations=30,
        warmup=5,
    ),
    Case(
        name="logs.tail",
        group="logs",
        description="LogProvider.tail_file(): bounded tail read of a 2000-line log",
        fn=lambda fixture: LogProvider(fixture.bench_root).tail_file("web.log", 200),
        iterations=50,
        warmup=5,
    ),
    Case(
        name="logs.tail_full",
        group="logs",
        description="LogProvider.tail_file(): read the whole 2000-line log",
        fn=lambda fixture: LogProvider(fixture.bench_root).tail_file("web.log", 2000),
        iterations=30,
        warmup=5,
    ),
    Case(
        name="logs.task_output",
        group="logs",
        description="TaskReader.read_output(): read and normalize a task log",
        fn=lambda fixture: TaskReader(fixture.bench_root).read_output(_first_task_id(fixture)),
        iterations=30,
        warmup=5,
    ),
]
