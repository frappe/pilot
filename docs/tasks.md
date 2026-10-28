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

Class flags tune runtime behavior: `audit_on_queue`, `is_cancellable_while_running`, and `is_listed`. Set `is_listed = False` on frequent polling tasks to keep them out of task listings (`TaskReader.list_tasks` and `GET /api/v1/tasks`, which honors a positive integer `limit` parameter, default 50); unlisted tasks stay readable by task id.

## Deadlines and recovery

`timeout_seconds` sets a task's total execution budget. It defaults to one hour. Submission saves the budget in task metadata. Queue wait does not count. The worker uses a monotonic deadline while it runs and restores the remaining budget from `started_at` when it adopts a task after a restart. Tasks queued before budgets were recorded use 24 hours. Invalid deadline metadata produces a worker log error; process ownership and dead-process recovery still run.

On expiry, the worker stops verified task processes, including descendants that started their own sessions. It records `failed` with `task_timeout` and runs the failure callback before claiming more work. An uncertain process identity keeps the queue blocked for manual recovery. A timeout does not undo database writes, restore deleted data, or cancel a service operation already accepted by systemd. Review the output before retrying a destructive operation. These deadlines apply to queued tasks; direct CLI calls to core methods do not use the task worker.

Callbacks run in separate processes with a 20-minute limit. This allows the default lite worker drain to finish. A callback timeout stops its verified descendants. Callback failures appear in the task output. A failed or cancelled migration pauses for Retry or Restore; an interrupted restore can resume from its saved checkpoints. If only the final worker restart failed, Retry queues `complete-migration` without running completed site migrations again. Migration details include this task's retained log as **Finish update**.

Replayed callbacks save their process identity before they can change state. After a worker crash, the next worker stops that callback and its verified descendants before replaying cleanup. If a process record is corrupt, recovery checks for a live wrapper or replay callback before clearing the finished task. A live process with unverified ownership blocks the queue for manual recovery. An interrupted `retrying` transition returns to Needs attention, so Retry and Restore remain available.

Site creation records its owning task under a per-site lock. Failure cleanup takes the same lock and deletes a partial site only when its owner matches the task. Cancelling queued creation does not delete a site created later by another task or the CLI. Successful creation releases its ownership record. Old tasks without an ownership record preserve partial sites for manual recovery.

Retry enables maintenance mode again before resuming an update. The operation keeps the original maintenance settings and restores them after a successful completion or recovery.

The limits below are conservative safety limits, not expected operation times. Large backup, restore, and migration tasks have a 24-hour budget because their cost depends on the database and files. Queued tasks can always be cancelled. The last column describes cancellation after a task starts.

| Task | Execution budget | Running cancellation |
| --- | --- | --- |
| `backup-site` | 24 hours | Yes |
| `build` | 1 hour | Yes |
| `bypass-patch` | 5 minutes | Yes |
| `change-admin-domain` | 1 hour | No |
| `clear-cache` | 5 minutes | Yes |
| `complete-migration` | 1 hour | Yes |
| `delete-backup` | 1 hour | Yes |
| `drop-site` | 24 hours | No |
| `fetch-all-app-updates` | 5 minutes | Yes |
| `get-and-install-app` | 1 hour | No |
| `get-app` | 1 hour | Yes |
| `install-app` | 1 hour | No |
| `migrate` | 24 hours | Yes |
| `migration-backup` | 24 hours | Yes |
| `new-app` | 1 hour | No |
| `new-site` | 1 hour | Yes |
| `new-site-from-backup` | 24 hours | Yes |
| `refresh-storage-usage` | 5 minutes | Yes |
| `reinstall-site` | 24 hours | No |
| `remove-app` | 1 hour | Yes |
| `rename-site` | 1 hour | No |
| `restart-database` | 1 hour | No |
| `restart-services` | 1 hour | Yes |
| `restore-site` | 24 hours | No |
| `retry-update` | 5 minutes | Yes |
| `revert-apps` | 24 hours | Yes |
| `revert-migration` | 5 minutes | Yes |
| `revert-site` | 24 hours | Yes |
| `set-innodb-buffer-pool-size` | 5 minutes | No |
| `set-mariadb-configuration` | 5 minutes | No |
| `set-mariadb-memory-share` | 5 minutes | No |
| `set-max-database-connections` | 5 minutes | No |
| `set-performance-schema` | 5 minutes | No |
| `setup-letsencrypt` | 1 hour | Yes |
| `setup-nginx` | 1 hour | Yes |
| `switch-branch` | 1 hour | No |
| `switch-lite-mode` | 1 hour | Yes |
| `uninstall-app` | 1 hour | No |
| `update` | 24 hours | Yes |
| `update-cli` | 1 hour | Yes |
| `wizard-setup` | 1 hour | Yes |

### Other timeout layers

| Layer | Limit | Meaning |
| --- | --- | --- |
| Worker database startup wait | 120 seconds | Only initialized benches wait. After the wait, queued tasks run even if the database is unavailable. |
| User cancellation | 3 seconds, then SIGKILL | Stops verified descendants; process identity checks can prevent an unsafe kill. |
| Unreadable process scan | 60 seconds | Scan errors get a grace period. Restarting the worker restarts this grace period. |
| Systemd workload restart | Largest selected process stop budget + 90 seconds | A default lite worker has 690 seconds to stop, so its restart allows 780 seconds. |
| Supervisor shutdown | 900 seconds | Some individual control subprocesses rely on the outer task deadline. |
| Domain provider command | 30 seconds | Per provider invocation. |
| Git revision probe / branch switch | 30 / 300 seconds | Clone and other fetch paths rely on the outer task deadline. |
| Remote backup API / download read | 30 / 300 seconds | Download reads have an idle limit, not a total transfer deadline. |
| Prebuilt asset download read | 60 seconds | Per socket operation; the task deadline bounds total work. |
| Site login / app enable or disable | 30 / 110 seconds | These can run directly in Admin requests rather than as queued tasks. |

Frappe install, migration, backup, restore, package installation, asset builds, and certificate commands do not all have their own total subprocess limit. The task execution budget bounds them when they run through the queue.

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
