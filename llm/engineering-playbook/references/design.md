# Design defaults

## Principles

- **Simplest design that works.** Reuse an existing object, task, manager or config group before adding one. One implementation per concept.
- **Object model first.** `Server` owns host state, `Bench` owns the bench path, config, apps, sites, runtime and tasks, `Site` owns site operations, `App` owns repository state. New behavior goes on the closest owner.
- **Thin surfaces.** CLI and Admin API parse input, authorize, queue tasks and delegate. They never duplicate Frappe, systemd, nginx, database or filesystem orchestration.
- **One owner per state.** One writer per file or record. Config through the config model; migration state in `MigrationOperation`; task state in the task store.
- **Durable workflows.** Multi-step work keeps a saved operation record plus small tasks and a state machine, like migrations. A crash leaves a record a person or a retry can resume.
- **Explicit over implicit.** Settings are visible in config, not inferred from the environment.
- **Defer the unneeded.** No knobs, modes or abstractions before a second real use.

## Failure and retry

- Fail loudly near the cause. Corrupt config or state stops the operation with a clear error.
- Explicit error over silent side effect.
- Retry only safe operations. Bounded retries on transient errors only.
- Save intent before the side effect (operation record, task submission, backup checkpoint).
- Illegal state transitions fail immediately.
- Idempotent submissions: `queue_submission()` reports whether a task was created; a repeat returns the existing task.
- Long work never blocks a request; the Admin UI polls task status.

## Concurrency

A CLI call, an Admin request, a scheduled job and task workers can act on one bench at once. Lost writes, double submissions, deadlocks and half-applied changes are bugs. Check before handover:

- **Config writes:** read-modify-write of `bench.toml`, `common_config.toml`, site config or JSON records happens under the file lock and writes atomically. Never write a stale copy loaded before slow work.
- **One writer per file or field.**
- **Task submission:** dedupe conflicting tasks on the same bench or site; idempotent submission for retries and double clicks.
- **Lock order and scope:** one fixed order; shortest hold; no subprocess, network or long build under a lock that other requests need.
- **Idempotent tasks:** a task may run twice after a worker restart; the second run sees the first result.
- **Database:** short transactions; no remote call inside one.

Name any residual risk in the handover in one line.

## API design

- **Backward compatible.** Old clients, scripts and benches keep working. Add optional fields and flags with safe defaults. Never rename, remove or retype a field, route, CLI flag, config key, status or task `command`. No new API version; evolve additively and keep accepting old request shapes.
- Admin API under `admin/backend/api/v1`, grouped by resource. Full words in paths. IDs in paths, objects in bodies.
- Mutations that take time queue a task and return its ID.
- Expose only what callers need: no internal phases or implementation details.
- Validate at the boundary with typed models; never pass a request dict into config or shell.
- CLI commands named by action, consistent flags across commands.

## Security

- Trust model: every bench under one benches directory runs as one host user, which is effectively root on the host. Benches are not a security boundary. See `SPEC.md`.
- Deny by default. Every Admin route checks authentication and authorization.
- Never pass user input to a shell or nginx config without validation and escaping.
- Secrets (tokens, keys, passwords) in files with tight modes, never in logs, URLs, commits or docs. Rotate with an overlap window when clients depend on them.
- Least-privilege tokens with the smallest audience and lifetime. Verify JWKS issuers and audiences.
- Bind to specific addresses. The setup wizard is reachable only where its owner accepts that.
- Security audits only on declared development targets ([environments.md](environments.md)). Record accepted risks in [domain.md](domain.md#accepted-risks).
