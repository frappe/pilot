# Task Execution Specification

Covers how admin-triggered commands are run as tracked background processes, how their output is stored, and how the admin panel reads and displays task state.

---

## Design

Every command triggered from the admin panel (migrate, build, update, etc.) is run as a **forked child process** that is fully detached from the Flask web server. The admin server writes the task metadata to disk and returns immediately. The child process runs independently, writing its output to a log file and updating a status file when it finishes.

The admin panel reads task state from the filesystem on every request — no in-memory state, no database.

```
Admin view
   │
   ├─ TaskRunner.run(command, args)
   │    ├─ create tasks/<task-id>/ directory
   │    ├─ write meta.json
   │    ├─ fork: python -m bench_cli.tasks.wrapper <task-dir>
   │    │         └─ runs command
   │    │         └─ streams output → output.log
   │    │         └─ writes status on exit
   │    ├─ write pid file
   │    └─ return task_id   ← admin redirects to /tasks/<task-id>
   │
   └─ (child process runs independently)
```

---

## Task directory layout

Each task lives in its own directory under `<bench-root>/tasks/`:

```
tasks/
├── 20250521-143022-a1b2c3/        # task_id = YYYYMMDD-HHMMSS-<6-hex>
│   ├── meta.json                  # command, args, started_at, finished_at, exit_code
│   ├── pid                        # integer PID of the child process (one line)
│   ├── output.log                 # combined stdout + stderr of the command
│   └── status                     # one word: running | success | failed | killed
├── 20250521-144501-f7a3d1/
│   └── ...
```

### `meta.json` schema

```json
{
  "task_id": "20250521-143022-a1b2c3",
  "command": "migrate",
  "args": { "site": "site1.example.com" },
  "command_argv": ["env/bin/bench", "--site", "site1.example.com", "migrate"],
  "started_at": "2025-05-21T14:30:22.441Z",
  "finished_at": "2025-05-21T14:30:35.112Z",
  "exit_code": 0
}
```

`command_argv` is the actual list passed to `subprocess`, derived from `command` + `args` by the same whitelist logic used in the old commands endpoint. `finished_at` and `exit_code` are `null` until the task completes.

### `status` file

Contains exactly one word with no trailing newline: `running`, `success`, `failed`, or `killed`.

Written as `running` by `TaskRunner` before the fork. Updated to `success`/`failed` by the wrapper when the child exits. Set to `killed` by the admin if a kill request is received, or by `TaskReader` lazily when the PID is dead but the status file still says `running`.

---

## Package layout additions

```
bench_cli/
└── bench_cli/
    └── tasks/
        ├── __init__.py
        ├── task_runner.py    # TaskRunner — forks child, writes task directory
        ├── task_reader.py    # TaskReader — reads task directory (stateless)
        ├── wrapper.py        # entry point for the forked child process
        └── models.py         # TaskInfo dataclass
```

---

## `TaskRunner`

Creates the task directory and forks the wrapper. Returns immediately after fork.

```python
class TaskRunner:
    def __init__(self, bench_root: Path): ...

    def run(self, command: str, args: dict) -> str:
        """
        Validate command against the whitelist.
        Create tasks/<task-id>/ and write meta.json and status='running'.
        Fork: python -m bench_cli.tasks.wrapper <task-dir>
        Write pid file.
        Purge old tasks if total completed > TASK_RETENTION_LIMIT.
        Return task_id.
        """

    def kill(self, task_id: str) -> None:
        """
        Read pid file. Send SIGTERM. Write status='killed'.
        Raises TaskNotFoundError if task_id is unknown.
        Raises TaskNotRunningError if status is not 'running'.
        """

    def _task_dir(self, task_id: str) -> Path: ...

    def _build_argv(self, command: str, args: dict) -> List[str]:
        """
        Map (command, args) to a concrete argv list.
        Raises ValueError if command is not in the whitelist.
        This is the single place where the whitelist is enforced.
        """

    @staticmethod
    def _generate_task_id() -> str:
        """Return YYYYMMDD-HHMMSS-<secrets.token_hex(3)>."""
```

**Task retention:** `TASK_RETENTION_LIMIT = 100`. After each `run()`, if the number of completed (non-running) tasks exceeds the limit, the oldest completed tasks are deleted (directory removed). Running tasks are never deleted automatically.

**Whitelist** (same commands as the old `/commands/run` endpoint, now centralised here):

| `command` | Required `args` keys | `command_argv` produced |
|-----------|---------------------|------------------------|
| `migrate` | `site` | `env/bin/bench --site <site> migrate` |
| `clear-cache` | `site` | `env/bin/bench --site <site> clear-cache` |
| `install-app` | `site`, `app` | `env/bin/bench --site <site> install-app <app>` |
| `build` | — | `env/bin/bench build` |
| `update` | `apps` (optional list) | `python -m admin.backend.tasks.jobs.update_task --bench-root <root> --apps [...] --task-log <output.log>` |
| `reload-supervisor` | — | `supervisorctl -c config/supervisor.conf reload` |

All paths in `command_argv` are absolute (resolved from `bench_root`) so the child process does not depend on `$CWD`.

---

## `wrapper.py` — the forked child

Invoked as `python -m bench_cli.tasks.wrapper <task-dir>`. This module is the entire body of the child process; it has no imports outside the standard library.

```python
# bench_cli/tasks/wrapper.py
import json, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

def main():
    task_dir = Path(sys.argv[1])
    meta = json.loads((task_dir / 'meta.json').read_text())

    with open(task_dir / 'output.log', 'wb') as log:
        result = subprocess.run(
            meta['command_argv'],
            cwd=str(task_dir.parent.parent),   # bench_root
            stdout=log,
            stderr=subprocess.STDOUT,
        )

    exit_code = result.returncode
    meta['finished_at'] = datetime.now(timezone.utc).isoformat()
    meta['exit_code'] = exit_code
    (task_dir / 'meta.json').write_text(json.dumps(meta, indent=2))
    (task_dir / 'status').write_text('success' if exit_code == 0 else 'failed')

if __name__ == '__main__':
    main()
```

The wrapper has no knowledge of bench internals. If the wrapper itself crashes before writing `status`, `TaskReader` detects the dead PID and reports `killed`.

---

## `TaskReader`

Reads task directories. Stateless — instantiated per request.

```python
class TaskReader:
    def __init__(self, bench_root: Path): ...

    def list_tasks(self, limit: int = 50) -> List[TaskInfo]:
        """
        Scan tasks/ directory. Return tasks sorted by started_at descending.
        Limit to <limit> most recent.
        """

    def read_task(self, task_id: str) -> TaskInfo:
        """Read a single task directory. Raises TaskNotFoundError if missing."""

    def read_output(self, task_id: str, lines: int = 200) -> List[str]:
        """Return the last <lines> lines of output.log."""

    def stream_output(self, task_id: str) -> Generator[str, None, None]:
        """
        Yield lines from output.log as they are written.
        Stops yielding when status changes from 'running' (checked every 0.5s).
        Yields a final sentinel line '__DONE__:<exit_code>' when complete,
        so the SSE client can update the status badge without a page reload.
        """

    def _effective_status(self, task_id: str, raw_status: str, pid: int) -> str:
        """
        If raw_status == 'running' but the PID is no longer alive,
        return 'killed'. Otherwise return raw_status unchanged.
        """
```

```python
@dataclass
class TaskInfo:
    task_id: str
    command: str
    args: dict
    status: str             # effective status (never 'running' for a dead PID)
    pid: Optional[int]
    started_at: datetime
    finished_at: Optional[datetime]
    exit_code: Optional[int]
    output_path: Path
    duration_seconds: Optional[float]   # None if still running
```

---

## Admin routes

Replace the old `POST /commands/run` SSE approach entirely. All command execution goes through tasks.

### Updated `views/commands.py` → `views/tasks.py`

The `commands.py` view is renamed `tasks.py` and registered at `/tasks`.

### `POST /tasks/run` — create a task

```
POST /tasks/run
Content-Type: application/x-www-form-urlencoded

command=migrate&site=site1.example.com
```

Behaviour:
1. Pass `command` and remaining form fields to `TaskRunner.run()`.
2. On `ValueError` (unknown command or missing args): return HTTP 400 with a plain-text error.
3. On success: redirect `303` to `GET /tasks/<task-id>`.

This means submitting a form in the admin immediately takes you to the task's status page.

### `GET /tasks` — task list

`TaskReader.list_tasks()` rendered as a table, most recent first.

| Column | Content |
|--------|---------|
| Command | `command` + key args (e.g. "migrate — site1.example.com") |
| Status | Coloured badge: green (success), red (failed), amber (running), grey (killed) |
| Started | Human-readable relative time (e.g. "3 minutes ago") — computed server-side |
| Duration | `duration_seconds` formatted as `0m 12s`, or "—" if still running |
| Actions | "View" link; "Kill" button if status is `running` |

### `GET /tasks/<task-id>` — task detail

`TaskReader.read_task(task_id)`. Renders:

- **Header bar:** command summary, status badge, started/finished timestamps, exit code.
- **Output block:** `<pre id="output">` populated with `TaskReader.read_output()`.
- **If running:** JavaScript opens an `EventSource` on `/tasks/<task-id>/stream` and appends each line to `#output`, scrolling to the bottom. On receiving the `__DONE__:<exit_code>` sentinel, the status badge and exit code fields are updated in place without a page reload.
- **Kill button:** shown only when status is `running`. Submits a form `POST /tasks/<task-id>/kill`.

### `GET /tasks/<task-id>/stream` — SSE output stream

```python
@tasks_bp.route('/<task_id>/stream')
def stream_task_output(task_id):
    reader = TaskReader(current_app.config['BENCH_ROOT'])
    def generate():
        for line in reader.stream_output(task_id):
            if line.startswith('__DONE__:'):
                yield f"event: done\ndata: {line[9:]}\n\n"
            else:
                yield f"data: {line}\n\n"
    return Response(stream_with_context(generate()), mimetype='text/event-stream')
```

The client's `EventSource` listens for the `done` event type to know the task finished:

```javascript
const es = new EventSource('/tasks/20250521-143022-a1b2c3/stream');
es.onmessage = e => appendLine(e.data);
es.addEventListener('done', e => {
    es.close();
    updateStatusBadge(parseInt(e.data) === 0 ? 'success' : 'failed');
});
```

### `POST /tasks/<task-id>/kill` — kill a running task

```python
@tasks_bp.route('/<task_id>/kill', methods=['POST'])
def kill_task(task_id):
    TaskRunner(current_app.config['BENCH_ROOT']).kill(task_id)
    return redirect(url_for('tasks.task_detail', task_id=task_id))
```

Errors (`TaskNotFoundError`, `TaskNotRunningError`) render a plain error page.

---

## Updated admin package layout

```
bench_cli/
└── bench_cli/
    └── admin/
        ├── readers/
        │   ├── ...
        │   └── (TaskReader lives in bench_cli/tasks/task_reader.py, not here)
        └── views/
            ├── dashboard.py
            ├── apps.py
            ├── sites.py
            ├── processes.py
            ├── logs.py
            ├── database.py
            └── tasks.py          # replaces commands.py
        └── templates/
            ├── ...
            └── tasks/
                ├── list.html
                └── detail.html
```

`TaskRunner` and `TaskReader` live in `bench_cli/tasks/`, not under `bench_cli/admin/`, because they are independent of Flask and could be used by other parts of the system (e.g., a future CLI `bench status` command).

---

## Updated architecture additions

### Bench directory layout

```
<bench-root>/
└── tasks/
    └── 20250521-143022-a1b2c3/
        ├── meta.json
        ├── pid
        ├── output.log
        └── status
```

### Full package layout additions

```
bench_cli/
└── bench_cli/
    └── tasks/
        ├── __init__.py
        ├── models.py        # TaskInfo dataclass
        ├── task_runner.py   # TaskRunner
        ├── task_reader.py   # TaskReader
        └── wrapper.py       # forked child entry point (stdlib only)
```

---

## Error cases

| Situation | Behaviour |
|-----------|-----------|
| `command` not in whitelist | `TaskRunner._build_argv` raises `ValueError`; view returns HTTP 400 |
| Required arg missing (e.g. `site` for migrate) | Same as above |
| Task directory missing on detail page | `TaskReader.read_task` raises `TaskNotFoundError`; view renders 404 page |
| PID alive but `output.log` empty | Normal — command hasn't written anything yet; show empty `<pre>` |
| PID dead, `status` still `running` | `TaskReader._effective_status` returns `killed`; displayed to user |
| Kill sent to already-finished task | `TaskRunner.kill` raises `TaskNotRunningError`; view shows error message |
| Wrapper crashes before writing `status` | Indistinguishable from `killed` — PID dead, status never updated |

---

## Security notes

- `TaskRunner._build_argv` only accepts commands from the hardcoded whitelist. No user-supplied string is ever passed to a shell.
- `task_id` values are validated to match `^\d{8}-\d{6}-[0-9a-f]{6}$` before use as directory names.
- The `tasks/` directory is inside the bench root; it is not web-accessible (the admin serves it through Flask routes only).
