# Task Execution Specification

Covers how admin-triggered commands (migrate, build, site creation, branch switches, etc.) run as durable, queued background tasks: one FIFO queue per bench, one worker per bench, crash recovery, and cancellation. The admin panel and API read task state from the filesystem on every request — no in-memory task state, no database.

---

## Design

```
Admin view / API
   │
   ├─ TaskRunner.submit(command, args)
   │    ├─ TaskStore.create_queued(): write meta.json + status=queued atomically
   │    └─ wake the bench's worker thread
   │
   └─ TaskWorker (background thread inside the admin process)
        ├─ holds tasks/worker.lock for the lifetime of the bench's admin process
        ├─ claims the oldest queued task (TaskQueue.claim_next)
        ├─ starts pilot.tasks.manager.wrapper as a detached child
        │     └─ wrapper runs the job's command_argv, streams output → output.log
        │     └─ wrapper finalizes status on exit (success/failed/killed)
        └─ repeats until the queue is empty, then goes idle
```

The worker is a **thread**, not a separate OS process: `task_workers.start(bench_root)` is called once from `server.py`/`wsgi.py` at admin startup. The `tasks/worker.lock` file (via `flock`) is what actually enforces "one worker per bench" — it protects against more than one admin process (e.g. two gunicorn workers) racing to run the same bench's queue, not just concurrent threads.

---

## Task states and transitions

```
queued --> running --> success
                    --> failed
                    --> killed
queued --------------> killed
```

Success, failed, and killed are terminal — no further transitions are allowed from them. Every transition is validated (`pilot/tasks/manager/task_state.py: validate_task_transition`) and applied as a compare-and-swap under the tasks-directory lock (`TaskStore.transition_locked` re-reads the current status and only writes if it still matches the expected one).

| Transition | Trigger |
|---|---|
| `queued` → `running` | `TaskQueue.claim_next()`: the worker claims the oldest queued task |
| `running` → `success` | wrapper exits with code 0 |
| `running` → `failed` | wrapper exits with a non-zero code (`failure.code = command_failed`), or a crash/orphan is detected (`failure.code = task_interrupted`) |
| `running` → `killed` | `TaskProcess.cancel()` confirms the process group was signalled |
| `queued` → `killed` | `TaskRunner.kill()` on a task that hasn't been claimed yet |

---

## On-disk layout

```
<bench-root>/tasks/
├── worker.lock            # flock target; held by the single active worker thread
├── worker.pid             # pid of the admin process currently holding the lock (empty if none)
├── worker-state.json       # {status, pid, current_task_id, updated_at}
├── worker-control.json     # {desired: "running" | "stopped"} — persisted operator intent
├── store                  # flock target guarding all metadata/status writes below
├── queue-sequence         # monotonic FIFO counter
└── <task-id>/              # task_id = YYYYMMDD-HHMMSS-<6 lowercase hex chars>
    ├── meta.json
    ├── status              # one word: queued | running | success | failed | killed
    ├── process.json         # present only while a wrapper process is (or was) tracked
    ├── pid                 # wrapper's pid, written alongside process.json
    ├── output.log           # syslog-framed combined stdout+stderr of the command
    ├── secrets.json         # transient; args like admin_password, deleted once the wrapper reads them
    └── callbacks.json       # transient; optional on_success/on_failure/on_cancel spec
```

### `meta.json` schema

```json
{
  "task_id": "20260716-143022-a1b2c3",
  "command": "migrate",
  "args": { "site": "site1.example.com" },
  "command_argv": ["/bench/env/bin/python", "-m", "pilot.tasks.jobs.migrate_task", "/bench/root", "site1.example.com"],
  "queued_at": "2026-07-16T14:30:22.441000+00:00",
  "started_at": "2026-07-16T14:30:22.501000+00:00",
  "finished_at": "2026-07-16T14:30:35.112000+00:00",
  "exit_code": 0,
  "failure": null,
  "bench_root": "/bench/root",
  "queue_sequence": 482
}
```

`started_at`, `finished_at`, `exit_code`, and `failure` are `null` until set by the corresponding transition. `args` is already redacted/whitelisted for public consumption (`public_task_args`) — secret values (e.g. `admin_password`) never appear here; they live only in `secrets.json`, which the wrapper deletes as soon as it hands them to the job. `failure`, when present, stores only `{"code": "command_failed"}` or `{"code": "task_interrupted"}`; the human-readable message is looked up from a fixed table at read time, not stored. Tasks submitted with an `Idempotency-Key` also carry `idempotency_digest` and `request_fingerprint`; tasks scoped to a resource (e.g. one site) carry `resource_key`, which blocks a second active task from touching the same resource.

### `process.json` schema

```json
{
  "task_id": "20260716-143022-a1b2c3",
  "argv": ["/bench/env/bin/python", "-m", "pilot.tasks.manager.wrapper", "/bench/root/tasks/20260716-143022-a1b2c3"],
  "identity": {
    "pid": 48213, "pgid": 48213, "sid": 48213, "boot_id": "...",
    "start_ticks": 918273, "uid": 1000, "argv_hash": "...", "launch_id": "..."
  }
}
```

`argv` here is the **wrapper's** own argv, not `command_argv` — `process.json` identifies the wrapper (the process-group leader), and the actual bench command runs as its child in the same session/group, inheriting the same `launch_id` environment variable. See [Process ownership and PID safety](#process-ownership-and-pid-safety).

---

## Worker lifecycle

`TaskWorker` (`pilot/tasks/manager/worker.py`) runs one loop per bench:

1. `try_acquire()` the `worker.lock`; if another worker already holds it, exit immediately (no-op).
2. Write `worker.pid` and enter the loop. On each iteration:
   - `TaskProcess.reconcile()` first — checks whether any previously-tracked task process is still alive, unowned, or dead (see [Crash recovery](#crash-recovery-and-orphan-handling)). If one is still alive/uncertain, park and re-poll instead of claiming new work.
   - If the operator has requested a stop (`worker-control.json` desired = `stopped`), report status `stopped` and park without claiming.
   - Otherwise claim and run the oldest queued task; loop immediately to try for more work.
   - If nothing is queued, report status `idle` and wait (woken early by a new submission, a cancel, or a worker start/stop request, otherwise polls every 0.2s).
3. On exit (drain complete), write status `stopped` and clear `worker.pid`.

### Worker states (`worker-state.json`)

| Status | Meaning |
|---|---|
| `starting` | Lock acquired, loop not yet run once |
| `idle` | No queued task; waiting for work |
| `running` | A task is actively executing |
| `draining` | Finishing the current (or orphaned) task before stopping — see below |
| `stopped` | Not claiming work — either the operator paused the queue (`worker.pid` still set, thread alive) or the worker thread has fully exited after a drain (`worker.pid` empty) |

`worker.pid` is the disambiguator for `stopped`: non-empty means the thread is alive and idling because the queue was deliberately paused; empty means the thread has actually terminated.

### Operator intent vs. drain

These are two independent controls that both stop the worker from claiming new work, but behave differently:

- **`worker-control.json` intent** (`running`/`stopped`) — set via `POST /task-worker/actions/start` or `.../stop`. Persisted to disk, survives admin restarts, and only pauses claiming; the worker thread stays alive and holds the lock.
- **Drain** (`threading.Event`, in-memory only) — set when the admin process receives `SIGTERM`/`SIGINT`. Not persisted; a freshly started admin process is never pre-drained. Once set, the worker finishes any in-flight task, then the loop exits and the thread releases the lock.

---

## SIGTERM / drain behavior

| Event | Actual behavior |
|---|---|
| Admin process gets `SIGTERM`/`SIGINT` while its worker is idle | Drain flag set; loop has no task to wait for, breaks immediately; state → `stopped`, `worker.pid` cleared, lock released |
| ...while a task is running | The running task's process is **not** signalled; state flips to `draining` and the worker keeps polling `process.wait()` until it exits normally; only then does the loop see the drain flag and exit |
| A task is submitted while draining | Persisted as `queued` exactly as normal (`TaskStore.create_queued` doesn't check worker state at all); `TaskWorker._claim_next()` refuses to claim it while `_drain` is set, so it stays queued |
| A fresh admin process starts later | New `TaskWorker` acquires the now-free `worker.lock`; `TaskQueue.claim_next()` always picks the oldest queued task by `queue_sequence`, so it resumes FIFO order regardless of which worker enqueued what |
| A task is cancelled (`DELETE /tasks/<id>`) | Only that task's own process group is signalled; the worker thread and every other task are untouched |
| The admin process dies mid-task and a new one starts | `reconcile()` finds the still-alive wrapper via `process.json` and blocks claiming new work until it exits — see below |

`WorkerRegistry.install_signal_handlers()` hooks `SIGTERM`/`SIGINT` on the admin process itself: the handler calls `request_drain()` on every bench worker it manages, then chains to whatever handler was previously installed (e.g. gunicorn's own). If there was no previous custom handler, it resets the signal to default and spawns a thread that joins all workers before re-raising the same signal — so the process's actual exit is deferred until every worker has drained.

---

## Cancellation

`TaskRunner.kill(task_id)`:

- **Queued task:** transitions `queued` → `killed` directly, deletes `secrets.json`, wakes the worker (in case it's idling on that same task's resource). No process is involved.
- **Running task:** delegates to `TaskProcess.cancel()`:
  1. Reject if the task isn't currently `running`.
  2. Inspect the tracked process's ownership (see below). If it's already dead/stale, treat this as an orphan and mark the task `failed`/`task_interrupted` instead of `killed`. If ownership is uncertain, refuse (`TaskNotRunningError`) rather than guess.
  3. Send `SIGTERM` to every process sharing the wrapper's launch id (its whole process group — descendants signalled before the group leader, so children don't get orphaned mid-signal).
  4. As soon as the signal is confirmed delivered, transition the task to `killed` and delete `secrets.json` — the visible state does not wait for the process to actually exit.
  5. Poll for exit every 0.05s for up to 3 seconds (`CANCEL_GRACE_SECONDS`); if still alive, send `SIGKILL` to the group and poll for up to another `max(1, grace)` seconds. This cleanup is best-effort and doesn't change the already-recorded `killed` status.

---

## Crash recovery and orphan handling

Every worker loop iteration calls `TaskProcess.reconcile()` before claiming new work — this is both routine post-task cleanup and crash recovery, driven entirely by whether `process.json` still exists and what the wrapper's identity looks like now:

| `process.json` present, ownership is... | Task status | Result |
|---|---|---|
| `owned` (process alive, all identity checks pass) or `unknown` (can't tell) | any | Treated as still blocking — worker parks and does not claim new work until this resolves |
| `dead`/`stale` (process is gone or was reused by something else) | `running` | Task transitioned `running` → `failed` with `failure.code = "task_interrupted"`; `process.json`/`secrets.json` removed |
| `dead`/`stale` | already terminal | Stale `process.json` left behind by a wrapper that exited normally is just cleaned up; any pending callback for that terminal status still gets run |

So: if the worker (admin process) dies while a task's wrapper is still alive, a replacement worker waits for that live orphan to finish on its own — it is never signalled or replayed. If both the worker and the task's process die while status was `running`, the task is marked `failed` with `task_interrupted` and must be retried explicitly; mid-step replay is never attempted (unsafe for migrations, site creation, package installs, production setup).

`POST /tasks/<id>/actions/retry` re-submits the same `command`/`args` as a brand-new queued task. It refuses tasks that are still active, and refuses commands whose whitelist requires a secret (`task_requires_secrets`, e.g. `admin_password` for `new-site`) since the original plaintext value was deleted after first use.

---

## Process ownership and PID safety

A bare PID is not trustworthy: PIDs get reused, so `kill(pid)` after any delay risks signalling an unrelated process that now happens to own that number. `ProcessIdentity` (`pilot/tasks/manager/process_identity.py`) is captured once at launch and re-checked before every signal:

| Field | Why it's checked |
|---|---|
| `pgid`, `sid` | Must equal the process's own pid — proves it's still the session/group leader the wrapper was started as (`start_new_session=True`) |
| `boot_id` (`/proc/sys/kernel/random/boot_id`) | A reboot invalidates any PID/start-time comparison; a boot id mismatch short-circuits straight to "not owned" |
| `start_ticks` (process start time from `/proc/<pid>/stat`) | Detects the same PID being reused by a different process after this one exited, without needing a reboot |
| `argv_hash` (`sha256` of `/proc/<pid>/cmdline`) | Confirms the process at this PID is still running the expected wrapper command line |
| `uid` | The signalling process must own the target |
| `launch_id` (random token passed via `BENCH_TASK_LAUNCH_ID` and read back from `/proc/<pid>/environ`) | The strongest check — a reused PID/argv-lookalike can't forge another process's environment |

`ProcessInspector.inspect()` returns one of: `owned` (alive, everything matches), `dead` (gone), `stale` (a PID/group match but the identity fields disagree — some other process now occupies that slot), or `unknown` (couldn't determine, e.g. permission denied) — `unknown` is never signalled or treated as dead; code that hits it backs off rather than guessing.

Signalling uses `pidfd_send_signal` (falling back to `os.kill`) so the check-then-signal isn't racy against the PID being recycled between the ownership check and the actual signal. Cancelling a whole task additionally scans `/proc` for every process (not just the tracked one) whose environment carries the same `launch_id`, so descendants that forked under the wrapper are signalled too, not just the leader.

### Startup gate

`TaskProcess.start()` passes the new wrapper a read end of a pipe (`BENCH_TASK_READY_FD`) and blocks it in `_wait_until_ready()` before it does anything. Only after `process.json` (the durable identity record) has been written does `start()` write one byte to the other end, releasing the wrapper. If the admin crashes between forking the wrapper and persisting `process.json`, the pipe's write end is closed unused, the wrapper reads EOF, and it exits without ever running the underlying command — so a task can never execute without a durable, discoverable record of its process pointing at it.

---

## Watchdog interaction

`AdminIdleWatchdog` (`admin/backend/watchdog.py`) must never shut the admin down while a task is running or a worker is draining. It reads one shared source of truth, `TaskActivityReader.read()` (`pilot/tasks/manager/activity.py`), which is also what the CLI and API status endpoints use:

```
active = uncertain
      or worker_state.status in {starting, running, draining}
      or running_tasks > 0
```

Notably, **queued-but-not-yet-claimed tasks do not count as activity** — if the worker was deliberately stopped via the API while tasks sit `queued`, the admin can still idle-shut-down; nothing is holding it open on their behalf (matches `worker-control.json` intent being an operator decision, not a crash).

`check_once()` double-checks before acting: it first does an unlocked idle/activity check, then re-checks both request idleness and task activity again under the lock immediately before calling `terminate()`, closing the race where a request or task starts in between. `AdminProcessOwner.terminate()` only ever signals the exact PID it was constructed to own (its own PID in dev/gunicorn mode, or `getppid()` in the systemd-managed case) and refuses if that PID no longer matches — the watchdog can shut down the admin process it's attached to, never a task's process group, and never a whole process group it doesn't own.

---

## Job contract

Every job subclasses `BaseTask` (`pilot/tasks/jobs/base_task.py`). `BaseTask.main()` loads any secret arguments handed off via `BENCH_TASK_SECRETS_FILE`, constructs the bench and the task, and calls `run()`:

- `self._step(key, label)` prints `STEP <key>,<timestamp> <label>` — opens a collapsible step in the admin UI. Every job emits at least one, even single-step ones.
- `self._step_failed()` prints `STEP-FAILED <key>,<timestamp>` for the currently open step.
- `main()` calls `_step_failed()` automatically around `run()` if it raises, or exits with a non-zero `SystemExit` code — a job never has to handle its own top-level failure reporting; raising is enough to make the task fail.

`SwitchBranchTask` (`pilot/tasks/jobs/switch_branch_task.py`) is a representative example: it calls `_step` for `fetch`, `checkout`, `install`, an optional `js`, `assets`, and a final `done`, and calls `sys.exit(result.returncode)` on a failed checkout rather than raising, which `main()` also treats as a failure via the `SystemExit` branch.

The wrapper (`pilot/tasks/manager/wrapper.py`) runs `command_argv` as a subprocess, capturing merged stdout/stderr into `output.log` with a syslog envelope per line (so `\r`-terminated progress redraws each get re-stamped and `TaskReader` can collapse them like a terminal would) and redacting known secret values before they ever reach disk.

---

## API surface

Routes registered by `admin/backend/views/tasks.py` (`/tasks` and `/task-worker`, both under `/api/v1`):

| Route | Behavior |
|---|---|
| `GET /tasks` | List tasks, optionally filtered by `?status=` |
| `POST /tasks` | Create a task; accepts `Idempotency-Key`; `202` with the task resource |
| `GET /tasks/<id>` | Task detail |
| `DELETE /tasks/<id>` | Cancel (queued or running); `204` on success |
| `POST /tasks/<id>/actions/retry` | Re-submit a finished task's command/args as a new task |
| `GET /tasks/<id>/events` | Structured JSON SSE: `status`, `line`/`overwrite`, and a final `done` event; replays `output.log` from the start on every new connection, so a reconnecting client gets full history |
| `GET /tasks/<id>/output/content` | Download the redacted, envelope-stripped output as `text/plain` |
| `GET /task-worker` | Current worker activity (`status`, `desired`, `queued_tasks`, `running_tasks`) |
| `POST /task-worker/actions/start` / `.../stop` | Set the persisted worker intent and wake the worker |
