"""Task queue, task reader, worker state, and audit log benchmarks."""

from __future__ import annotations

from benchmark.fixtures import Fixture
from benchmark.harness import Case
from pilot.internal.tasks.queue import TaskQueue
from pilot.internal.tasks.runner import generate_task_id
from pilot.internal.tasks.store import TaskStore
from pilot.internal.tasks.worker_state import WorkerStore
from pilot.managers.task import TaskActivityReader, TaskReader, TaskWorkerControl
from pilot.tasks import TaskRunner
from pilot.tasks.clear_cache import ClearCacheTask


def _task_id(fixture: Fixture) -> str:
    return sorted(path.name for path in (fixture.bench_root / "tasks").iterdir() if path.is_dir())[0]


CASES = [
    Case(
        name="tasks.runner_init",
        group="tasks",
        description="TaskRunner(bench): discover every task module and build the engine",
        fn=lambda fixture: TaskRunner(fixture.bench_root),
        iterations=20,
        warmup=3,
    ),
    Case(
        name="tasks.queue",
        group="tasks",
        description="ClearCacheTask.queue(): payload, metadata, audit entry, housekeeping",
        fn=lambda fixture: ClearCacheTask.queue(fixture.bench(), site="site1.localhost"),
        setup=lambda fixture: fixture.reset(),
        iterations=10,
        warmup=2,
        profile=True,
    ),
    Case(
        name="tasks.submit",
        group="tasks",
        description="TaskRunner.submit_task(): queue without running",
        fn=lambda fixture: fixture.bench().tasks.submit_task(ClearCacheTask, site="site1.localhost"),
        setup=lambda fixture: fixture.reset(),
        iterations=10,
        warmup=2,
    ),
    Case(
        name="tasks.list",
        group="tasks",
        description="TaskReader.list_tasks(): read every task dir, sort, limit",
        fn=lambda fixture: TaskReader(fixture.bench_root).list_tasks(limit=50),
        setup=lambda fixture: fixture.reset(),
        iterations=20,
        warmup=3,
        profile=True,
    ),
    Case(
        name="tasks.read",
        group="tasks",
        description="TaskReader.read_task(): one task's metadata and status",
        fn=lambda fixture: TaskReader(fixture.bench_root).read_task(_task_id(fixture)),
        iterations=50,
        warmup=5,
    ),
    Case(
        name="tasks.progress",
        group="tasks",
        description="TaskReader.get_progress(): parse a task's output log for STEP markers",
        fn=lambda fixture: TaskReader(fixture.bench_root).get_progress(
            TaskReader(fixture.bench_root).read_task(_task_id(fixture))
        ),
        iterations=20,
        warmup=3,
    ),
    Case(
        name="tasks.output",
        group="tasks",
        description="TaskReader.read_output(): read and normalize a task log",
        fn=lambda fixture: TaskReader(fixture.bench_root).read_output(_task_id(fixture)),
        iterations=20,
        warmup=3,
    ),
    Case(
        name="tasks.activity",
        group="tasks",
        description="TaskActivityReader.read(): worker state, intent, and task counts",
        fn=lambda fixture: TaskActivityReader(fixture.bench_root).read(),
        iterations=30,
        warmup=5,
    ),
    Case(
        name="tasks.claim_next",
        group="tasks",
        description="TaskQueue.claim_next(): scan queued tasks and transition the oldest",
        fn=lambda fixture: TaskQueue(fixture.bench_root).claim_next(),
        setup=lambda fixture: fixture.reset(),
        iterations=20,
        warmup=3,
    ),
    Case(
        name="tasks.store_create",
        group="tasks",
        description="TaskStore.create_queued(): lock, sequence, and atomic task publish",
        fn=lambda fixture: TaskStore(fixture.bench_root).create_queued(
            {
                "task_id": generate_task_id(),
                "command": "clear-cache",
                "args": {"site": "site1.localhost"},
                "queued_at": "2026-01-01T00:00:00+00:00",
            }
        ),
        setup=lambda fixture: fixture.reset(),
        iterations=10,
        warmup=2,
    ),
    Case(
        name="tasks.purge_terminal",
        group="tasks",
        description="TaskStore.purge_terminal(): scan terminal tasks and delete past the limit",
        fn=lambda fixture: TaskStore(fixture.bench_root).purge_terminal(100),
        setup=lambda fixture: fixture.reset(),
        iterations=20,
        warmup=3,
    ),
    Case(
        name="tasks.worker_state",
        group="tasks",
        description="WorkerStore.read_state()/read_intent(): worker control-plane files",
        fn=lambda fixture: (
            WorkerStore(fixture.bench_root).read_state(),
            WorkerStore(fixture.bench_root).read_intent(),
        ),
        iterations=50,
        warmup=5,
    ),
    Case(
        name="tasks.worker_control",
        group="tasks",
        description="TaskWorkerControl.request_start(): write intent and wake the worker",
        fn=lambda fixture: TaskWorkerControl(fixture.bench_root).request_start(),
        setup=lambda fixture: fixture.reset(),
        iterations=20,
        warmup=3,
    ),
]
