# Tasks

Tasks run long operations outside the Admin API request cycle. They are also the preferred interface for CLI commands that need progress, logs, or callbacks.

## Public API

Task classes inherit from `Task` and are exported from `pilot/tasks/__init__.py` with real imports for IDE autocomplete.

Queue tasks by class:

```python
from pilot.core.bench import Bench
from pilot.tasks import GetAndInstallAppTask

bench = Bench("main")
task_id = GetAndInstallAppTask.queue(
    bench,
    name="erpnext",
    repo="https://github.com/frappe/erpnext",
)
```

Use `queue_submission()` when the caller needs to know whether an idempotent submission created a new task.

## Task Shape

```python
from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, on_failure, on_success, step


@dataclass(kw_only=True)
class ExampleTask(Task):
    command: ClassVar[str] = "example"

    site: str

    @step("run", "Running example")
    def run(self) -> None:
        self.bench.site(self.site).migrate()

    @on_success
    def refresh_site(self) -> dict:
        return {"site": self.site}

    @on_failure
    def reload_task_list(self) -> dict:
        return {}
```

`command` is the stable task name stored in task records. Constructor fields are validated before submission.

## Steps

Use `@step("key", "Label")` around meaningful phases. Step output is parsed by the task runner and exposed to the Admin UI.

Call `self.step_failed()` only when a task handles an error internally and still needs to mark the active step as failed.

## Callbacks

`@on_success`, `@on_failure`, and `@on_cancel` take no arguments. The decorated method returns callback args, and the method name becomes the operation with underscores converted to hyphens.

Return `None` to skip a callback for that task instance.

Explicit callbacks passed to `queue()` override callbacks declared on the task.

## Idempotency

Use `idempotency_key` when duplicate submissions should return the same task. Use `resource_key` when only one active task should own a resource.

Required submit-only args can be named in `required_submit_args` when the runner needs a value that is not a dataclass constructor field.

## Implementation Boundaries

- Task authoring helpers live in `pilot/tasks`.
- Execution internals live in `pilot/internal/tasks`.
- Task behavior should call core objects.
- API routes should submit tasks and return task ids.
- Commands may run tasks or call core objects directly for short work.

Do not build task command strings by hand when a task class exists.

## Internal Workings

### 1. Caller writes a task

`pilot/tasks/wizard_setup.py`:

```python
@dataclass(kw_only=True)
class WizardSetupTask(Task):
    command: ClassVar[str] = "wizard-setup"

    def run(self) -> None:
        self.init()

    @step("init", "Initialize bench")
    def init(self) -> None:
        self.bench.initialize(on_progress=self.report)


if __name__ == "__main__":
    WizardSetupTask.main()
```

That `if __name__ == "__main__"` block matters — this same file gets re-run as a standalone process later. Keep it in mind.

### 2. Something calls `.queue()`

For example, an admin API route:

```python
task_id = WizardSetupTask.queue(bench)
```

`Task.queue()` in `pilot/tasks/base.py:65-81` does almost nothing itself — it forwards to `bench.tasks`, which is a `pilot.tasks.base.TaskRunner` (the facade), which forwards again to the real engine:

```python
# pilot/tasks/base.py
@classmethod
def queue(cls, bench, callbacks=None, idempotency_key=None, resource_key=None, **args) -> str:
    callbacks = cls._queue_callbacks(bench, args, callbacks)
    return bench.tasks.run_task(
        cls,
        callbacks=callbacks,
        idempotency_key=idempotency_key,
        resource_key=resource_key,
        **args,
    )
```

Two facades deep, no real work yet — just arg-filtering. The real engine is `pilot.internal.tasks.runner.TaskRunner`.

### 3. The real engine builds a payload and writes it to disk

`pilot/internal/tasks/runner.py` + `payload.py`:

```python
# internal/tasks/runner.py TaskRunner.submit()
payload = self._payloads.build(command, args, callbacks)
self._store.create_queued(payload.metadata, payload.private_files, resource_key=resource_key)
```

`TaskPayloadBuilder.build()` (`payload.py:82-99`) is where `command_argv` gets constructed — this is the key line to understand:

```python
def build_command_argv(self, command: str, args: dict) -> list[str]:
    return [
        sys.executable,
        "-m",
        self._jobs[command].__module__,
        str(self._bench_root),
        *task_argv_suffix(self._jobs[command], args),
    ]
```

For `wizard-setup` that's literally:

```python
["python", "-m", "pilot.tasks.wizard_setup", "/path/to/bench"]
```

i.e. the exact command that would re-trigger that `if __name__ == "__main__": WizardSetupTask.main()` block from step 1. This argv string gets written into `tasks/<task_id>/meta.json` as `"command_argv"` — it isn't run yet, just recorded.

`create_queued` (`store.py`) writes `tasks/<task_id>/{meta.json, status}` with `status = "queued"`, then `runner.py` calls `task_workers.wake(bench_root)` to nudge the background thread.

### 4. The worker thread claims and forks

`worker.py` + `process.py`:

```python
# worker.py TaskWorker._run_next()
task_id = self._claim_next()                  # TaskQueue.claim_next(): queued -> running
process = self._processes.start(task_id)      # TaskProcess.start()
self._wait_for_task(process, pid, task_id)    # blocks until it exits
```

```python
# process.py TaskProcess.start()
argv = [sys.executable, "-m", "pilot.internal.tasks.wrapper", str(task_dir)]
process = subprocess.Popen(argv, start_new_session=True, ...)
identity = self._inspector.capture(process.pid, argv, launch_id)   # ProcessIdentity
self._store.write_process(task_id, process.pid, record.to_dict())  # process.json
```

Note this argv is not the task's own module — it's always `pilot.internal.tasks.wrapper`. That's fork #1: a generic supervisor child, same for every task type.

### 5. The wrapper child reads meta.json and forks again, running the real command

`wrapper.py`:

```python
def _run_task() -> None:
    ...
    meta = store.read_metadata(task_id)     # has "command_argv" from step 3
    exit_code = run_with_syslog_output(
        meta["command_argv"],               # ["python", "-m", "pilot.tasks.wizard_setup", bench_root]
        cwd,
        meta["command"],
        task_dir / "output.log",
        redactions,
    )
    _finalize_task(store, task_id, exit_code)   # status -> success/failed


def run_with_syslog_output(command_argv, cwd, tag, log_path, redactions=None) -> int:
    process = subprocess.Popen(command_argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    ...
    return process.wait()
```