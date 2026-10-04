# Reliability and operations

Code must work at 3 a.m. without a babysitter and give users clear, fast feedback. Reliability comes from good defaults and clear ownership, not extra layers.

## Pre-mortem

Answer in the design, a few lines each. "Cannot happen" needs a one-line reason.

1. Runs twice? Stops halfway? Worker killed between steps?
2. Remote side (Git host, package index, Central, Let's Encrypt, object storage, DNS provider) slow, down, or errors after doing the work?
3. Many sites on one bench, large databases, slow disks?
4. Who else writes this data: CLI, Admin request, task worker, scheduled job?
5. What does the user see while running, on failure, on success?
6. How does an operator learn it broke and fix it without editing files by hand?
7. Which existing bench, config, script or client could break?
8. How do we roll out and undo?

## Multi-step operations

For operations with several steps or a remote side effect (update, migrate, restore, rename, certificate setup). A local write needs no extra state.

- A saved operation record with one owner and a small set of states.
- Every step idempotent; a rerun after success or crash is safe.
- Save intent and checkpoints before the side effect.
- A person can resume, retry or revert from the recorded state. Nothing relies on one request.
- Terminal states stay terminal and record the failure and how to recover.

## Timeouts and backpressure

- Every network call, subprocess and lock wait has a timeout.
- Bounded retries of idempotent operations on transient errors only. Fail fast otherwise and show the output.
- Cap batches, uploads and fan-out. Return a clear error instead of queueing forever.

## Data integrity

- Back up before migrate, restore, rename or drop. Maintenance mode while a bench is partly updated.
- Validate at the boundary with typed models.
- No stored value that can drift from its source.
- Patches in `pilot/patches` safe to run twice.
- Deletes explicit and scoped. Keep backups by retention, not by accident.

## Resource lifecycle

- Everything created has cleanup: temporary files, keys, processes, nginx and systemd units, certificates, backups past retention.
- Startup and the task worker recover what a crash left behind.
- Updates reload services gracefully and keep sites serving where possible.

## Observability

- Task records, steps and logs show what happened. Log the command, its output, and the bench and site.
- Never swallow an error; map it to a clear message and keep the original in the log.
- State readable in the Admin UI or CLI: status, last error, progress.
- Make staleness visible (pending update, failed migration that needs attention).

## End-user experience

- Fast first response; long work queues a task with visible steps.
- Understandable states; no internal phases.
- Errors say what happened and what to do next. No stack traces in the UI.
- Safe defaults without configuration. Destructive actions confirmed.
- One action does one thing; return an error rather than change another setting.
- Old benches, scripts and clients keep working ([design.md](design.md#api-design)).

## Testing

- Test failure paths: partial failure, retry after crash, cancel, concurrent submission, corrupt config.
- Test invariants and contracts (CLI output, API shape, config compatibility, idempotency), not constants.
- Risky changes: on a development bench, kill the worker mid-task, restart, confirm recovery.

## Operations

- Each task, scheduled job or daemon has an owner, timeout, dedupe rule, failure log and visible running state.
- Each setting has a safe default and a reason. Remove settings nobody changes.
- Runbooks for manual work in `docs/`, current state only.
- Rollback plan: commit to revert, backup to restore.

## Definition of done

Pre-mortem answered; idempotent, bounded, observable, recoverable; concurrency safe ([design.md](design.md#concurrency)); old benches and clients work; clear user states and errors; docs match; an operator can fix a failure without reading code.
