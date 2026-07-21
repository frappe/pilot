# Recoverable Updates and Site Migration

## Context

Issue [frappe/pilot#151](https://github.com/frappe/pilot/issues/151) asks for migrations to fail safely and become recoverable from the admin UI. The underlying goal is broader than skipping a patch: **keep the framework operational when updated app code or a faulty migration leaves one or more sites in an inconsistent state.**

The intended user flow is:

1. The user chooses to update apps and is offered an opt-out safeguard backup.
2. Pilot creates recovery points, updates the bench, and migrates sites one at a time.
3. Migration stops at the first failed site instead of continuing through the remaining sites.
4. Pilot keeps the failure visible in the navbar and takes the user to a dedicated recovery UI.
5. The user fixes the problem manually or through a supported guided fix.
6. The user then chooses either to retry from the failed site or restore the whole update.
7. Restore is available only when this update created safeguards.

This is an update-level workflow, not a long-running interactive task. Tasks perform background work, while a separate durable object owns progress, failure, and recovery state.

## Current State

The existing implementation has several relevant behaviors and gaps:

- `BenchUpdater.migrate_sites()` already visits sites sequentially and stops when a migration raises, but the failed `UpdateTask` is the only record of what happened.
- `MigrateTask` shells out to `bench migrate` directly, while CLI updates use `Site.migrate()` and `SiteCommands.migrate()`. These paths can diverge in backup, failure handling, and command options.
- No safeguard backup is taken by either migration path. The standalone migration dialog warns users that they must create one themselves.
- Migration output is streamed without being retained by `SiteCommands`, so failures collapse into a generic `MigrateError` and cannot drive contextual actions.
- Backup creation and database restore mechanics exist, but restoring an existing site from a backup belonging to the failed update is not exposed as a coordinated recovery action.
- Frappe Agent already optimizes failed-migration recovery with table-wise backups, `touched_tables.json`, selective table import, and removal of tables created during migration. Pilot should reuse this model rather than defaulting to a full database restore.
- Task statuses are limited to queued, running, success, failed, and killed. A task cannot safely remain paused while waiting hours or days for user input.
- The "Update available" button is mounted independently by several pages instead of being a single global update-status indicator.
- Frappe provides broad `--skip-failing` behavior and a specific `bypass-patch` command. Recovery should use the specific, confirmed bypass action rather than enabling broad patch skipping from the initial update dialog.

## Goals

- Create table-wise database recovery snapshots and site-configuration recovery points before changing app code, unless the user explicitly opts out.
- Preserve the exact pre-update app revisions needed for whole-update restore.
- Stop on the first migration failure and retain a durable record of completed, failed, and pending sites.
- Surface unresolved update failures globally in the navbar.
- Support manual repairs and targeted guided fixes. For every detected patch failure, present **Retry** and **Skip this patch** as explicit user choices; never select or execute either automatically.
- Retry the failed site and then continue through remaining sites.
- Restore every site and selected app to its pre-update logical state.
- Keep task execution, logging, and cancellation separate from user-facing recovery state.

## Non-goals

- Generic live-database browsing or an unrestricted row editor.
- Public/private file archives as part of the default update safeguard.
- Automatic recovery without a safeguard through MariaDB flashback or binlog replay.
- Pre-install validation of hooks, imports, Python dependencies, or Node versions. Those remain follow-up work for preventing faulty apps from being installed.
- Making the existing broad `--skip-failing` option the normal admin recovery path.

## Migration Operation

Introduce a persisted `MigrationOperation` domain class independent of task records:

```text
preparing | updating | migrating | needs_attention |
retrying | restoring | completed | restored | restore_failed
```

An operation owns:

- Operation kind (`update` or `site_migrate`), creation/start/finish timestamps, and duration.
- Selected apps and their exact pre-update commit SHAs.
- Ordered site list and original site configuration snapshots.
- Per-site snapshot status, pre-migration table inventory, cumulative touched-table set, and migration status.
- Current phase, failed site, structured failure diagnosis, and available actions.
- Root task and subsequent retry, restore, bypass, and fix task IDs.
- Whether safeguards were explicitly disabled.
- Checkpoints needed to retry a partially failed restore.

Persist operations atomically and validate every state transition. Keep completed, restored, and failed operation summaries as migration history even after task logs and recovery artifacts are cleaned up.

Tasks become thin background adapters:

```text
UpdateTask          -> operation.execute_update()
MigrateTask         -> operation.execute_site_migrate()
RetryUpdateTask     -> operation.retry()
RestoreMigrationTask  -> operation.restore()
MigrationFixTask    -> operation.apply_fix()
BypassPatchTask     -> operation.bypass_patch()
```

A task ending does not resolve the operation. When migration fails, the task becomes `FAILED` and exits, while the operation becomes `needs_attention`. No paused task status or occupied worker is introduced.

## Migration Workflow

1. Create the `MigrationOperation` before queueing its first task.
2. Record selected apps, exact current SHAs, ordered sites, and original site configurations.
3. Create protected Git references for the old SHAs so shallow-clone cleanup cannot remove the captured pre-update commits.
4. Put every affected site in maintenance mode to prevent writes during backup and partial migration.
5. Unless safeguards were opted out, clear and recreate each `<site>/.migrate/` directory after acquiring the exclusive migration lock, record the pre-migration table inventory, dump every existing table as `.migrate/<table_name>.sql.gz`, and retain the original configuration snapshot. If any safeguard fails, restore maintenance settings and abort before changing code.
6. Keep `.migrate/` intact while the operation is unresolved. The exclusive lock and unresolved-operation check prevent another migration from clearing it.
7. Update selected apps, reinstall dependencies, and rebuild assets.
8. Migrate sites in the recorded order, persisting success after each site.
9. After every migration attempt, read `touched_tables.json` in a `finally` path and union it into the operation record. At the first failure, capture the diagnosis, mark that site failed, leave remaining sites pending, and transition to `needs_attention`.
10. On complete success, restart services, restore each site's original maintenance setting, mark the operation `completed`, and release safeguard and Git-reference pins.

Standalone site migration reuses the same orchestration as a one-site operation. Because it does not update app code, its Restore action selectively restores that site's touched tables, removes migration-created tables, and restores its original configuration.

## Failure Diagnosis and Fixes

Run migration through the shared `Site.migrate()` path. Replace migrate-only command execution with a tee that preserves live task output while returning the complete output for classification.

Persist a structured diagnosis:

```text
phase, patch, table, column, database_engine,
failure_kind, message, output_excerpt, resolver_id
```

- Track the latest Frappe `Executing <patch> ...` line to identify a failing patch.
- In the migration `finally` path, persist the current touched-table list into the operation before the task exits. Retry attempts add to the existing set rather than replacing it.
- Classify known database conversion errors and retain unknown failures without guessing at a fix.
- Unknown failures still show logs, manual repair guidance, Retry, and Restore.
- Implement a resolver registry so each guided repair has its own matching, read, validation, and write behavior.
- The first resolver supports MariaDB string-to-number conversion failures. It lists only affected rows and permits replacing the diagnosed value with a valid number or `NULL`.
- Resolver writes must confirm that the operation is still in the same failed state, validate table/column identifiers against `information_schema`, restrict writes to the diagnosed column and returned row names, validate the target type, and bind all values as query parameters.
- PostgreSQL, unresolved row identities, and other failure classes fall back to manual repair in v1.

For every identified patch failure, always show two independent actions: **Retry** reruns migration without changing patch state, while **Skip this patch** permanently records only that exact patch as completed for the failed site. Pilot must never choose either action automatically. Skip requires a strong permanence warning and explicit confirmation, then queues Frappe `bypass-patch <patch> --yes`, records an audit event, and returns the operation to `needs_attention`; the user must explicitly choose Retry to continue migration.

## Selective Table Recovery
### Ownership

Add a dedicated `SiteMigrationSnapshot` service under the site core. `MigrationOperation` creates one snapshot service per site and calls it while the update or migrate task is running; no separate backup task is queued.

`SiteMigrationSnapshot` owns:

- Creating the private `<site>/.migrate/` directory with restrictive permissions and writing each table directly to `.migrate/<table_name>.sql.gz`.
- Clearing and recreating `.migrate/` only at the start of a new migration, after the exclusive migration lock is held and no unresolved operation exists.
- Recording `previous_tables.json`, original site configuration, and one compressed dump per table for MariaDB.
- Reading `touched_tables.json` after every attempt and updating the operation cumulative touched-table set.
- Restoring selected or fallback tables, dropping migration-created tables, and checkpointing restore progress.
- Removing recovery artifacts only after successful completion or Restore or an explicit future discard action.

The regular `SiteBackups` service remains responsible for user-visible scheduled/manual backups, retention, and offsite upload. Migration snapshots are private recovery artifacts and must not appear in the Backups UI or be pruned by normal backup retention. PostgreSQL and SQLite implementations use the same service interface with their full-database fallback.

### Restore Algorithm

Follow the recovery pattern already used by [Frappe Agent server recovery](https://github.com/frappe/agent/blob/develop/agent/server.py#L619-L641) and [the Agent site implementation](https://github.com/frappe/agent/blob/develop/agent/site.py):

- Before updates, record `previous_tables.json` and create `<site>/.migrate/<table_name>.sql.gz` for every existing table. The safeguard still covers every table, but recovery can import only the affected subset.
- Maintain a cumulative touched-table set for each site. Union the output from every failed or successful migration attempt so a later Retry cannot erase knowledge from an earlier attempt.
- Add tables changed by Pilot actions to the same set: patch bypass adds `tabPatch Log`, and a guided resolver adds the table it edits.
- For Restore, restore only the cumulative touched tables for sites whose migration ran. Sites that remained pending require no database import.
- After importing backed-up tables, calculate `current_tables - previous_tables` and drop those migration-created tables.
- If `touched_tables.json` is missing, unreadable, empty despite a database-changing failure, or otherwise untrusted, fall back to importing every table listed in `previous_tables.json`.
- MariaDB uses the selective table path in v1. PostgreSQL and SQLite retain full-database safeguard and restore behavior until equivalent engine-specific implementations exist.
- Checkpoint each table import and new-table drop. A failure leaves the operation in `restore_failed` and can be retried without repeating completed work unnecessarily.

## Retry and Restore

**Retry**

- Allowed only from `needs_attention` after no operation action is running.
- Migrate the failed site again with normal patch behavior; Retry never bypasses or marks a patch as completed.
- If successful, continue through remaining pending sites.
- Do not rerun migrations for sites already marked successful.
- Restart services, restore maintenance settings, and complete the operation after all sites succeed.
- If migration fails again, replace the diagnosis and return to `needs_attention`.

**Restore**

- Available only when all operation safeguards were created successfully.
- Restore selected apps to their captured revisions.
- Reinstall dependencies and rebuild assets from the restored code.
- For each site whose migration ran, selectively restore its cumulative touched tables and drop migration-created tables. Pending sites require no database restore.
- Keep maintenance mode forced during restore and restore original maintenance settings only after every step succeeds.
- Restart services and mark the operation `restored`.
- Checkpoint restore steps. On failure, persist `restore_failed`, keep sites in maintenance mode, and allow Restore to continue from the failed checkpoint.

## Migration UI and APIs

Add two dedicated routes:

```text
/migrations
/migrations/:operationId
```

The **Migrations** index is the permanent place to browse and track migrations:

- Pin the current active or unresolved migration at the top and update its phase/site progress live.
- List older migrations newest first with cursor pagination and filters for status, kind, and site.
- Show start time, duration, kind (app update or standalone site migration), affected apps/sites, final status, failed site/phase, retry/restore outcome, and safeguard availability.
- Retain operation summaries indefinitely unless a future explicit history-retention policy is added. Recovery dumps and task logs keep their own cleanup policies and may no longer be available for an old entry.
- Include retained pre-feature `update` and `migrate` tasks as read-only legacy rows with the limited metadata available from task history; these link to Task Detail and do not expose operation recovery actions.

The migration detail page is used for both live tracking and historical inspection:

- During an active run, show overall phase, current site, completed/failed/pending sites, elapsed time, step progress, and the active task output stream.
- Refresh operation state when task stream status changes or completes, with polling as a reconnect fallback.
- For completed history, show the final per-site result, app revisions, timings, diagnosis, user decisions such as patch skips, restore outcome, and links to task logs when still retained.
- For an unresolved failure, show manual repair guidance, supported guided fixes, Retry, Restore, and both Retry and confirmed **Skip this patch** for patch failures.
- Starting an update or standalone migration navigates here instead of Task Detail. Task pages remain secondary execution-log views and never determine valid recovery actions.

Mount one `MigrationStatusButton` in `MainLayout` and remove `UpdatesAvailableButton` from individual pages. Its priority is:

1. `Migration failed` or `Update failed` for unresolved operations.
2. `Updating`, `Migrating`, `Retrying`, or `Rescuing` for an active operation.
3. `Update available` only when no unresolved operation exists.

The global migration composable loads the current operation, polls while it is active, and refreshes after task completion and every recovery action. The navbar opens the active/unresolved migration detail; when none exists, its update-available state opens the existing update dialog. Add a Migrations navigation entry that always opens history.

Expose:

```text
POST /updates
GET  /migrations?cursor=&limit=&status=&kind=&site=
GET  /migrations/current
GET  /migrations/<operation_id>
POST /migrations/<operation_id>/actions/retry
POST /migrations/<operation_id>/actions/restore
POST /migrations/<operation_id>/actions/bypass-patch
POST /migrations/<operation_id>/actions/apply-fix
```

`POST /updates` creates a `MigrationOperation(kind="update")`; the existing standalone site-migrate endpoint creates `kind="site_migrate"`. Both return operation and task IDs. List/detail responses expose durable operation summaries and nullable task-log links. Action endpoints validate operation state, queue a background task, attach its ID to the operation, and return both IDs.

Extend task resource locking to support multiple resource keys. Update, retry, and restore tasks claim the bench update resource plus every affected site resource, preventing concurrent update, migrate, backup, restore, or fix operations on those sites.

## Test Plan

- Operation creation, atomic persistence, valid transitions, task-history attachment, and survival after task cleanup.
- Global navbar priority, labels, polling, and routing across available, active, failed, completed, and restored states.
- Migration history orders operations newest first, paginates by cursor, filters by status/kind/site, and pins the active or unresolved operation.
- Active detail combines durable operation progress with the current task stream and recovers through polling after an SSE disconnect.
- Completed details remain readable after task logs and recovery artifacts are removed, with unavailable log links omitted.
- Retained legacy update/migrate tasks appear as limited read-only history entries without recovery actions.
- Update creation navigates to the operation page instead of Task Detail.
- All sites enter maintenance mode before safeguards and remain protected through failure and recovery.
- All safeguards complete before code changes; a backup failure restores maintenance settings and aborts unchanged.
- Snapshot creation writes tables directly to `<site>/.migrate/<table_name>.sql.gz`; an active or unresolved migration prevents that directory from being cleared by another run.
- Safeguard and Git-reference pins survive `needs_attention` and are released after completion or restore.
- Site migrations run sequentially and stop at the first failure.
- Retry starts at the failed site, skips successful sites, and continues remaining sites.
- Restore selectively imports cumulative touched tables and removes newly created tables for migrated sites, skips untouched pending sites, and falls back to all pre-migration tables when selective metadata is unavailable or untrusted.
- Opting out removes Restore without removing manual repair or Retry.
- Live output remains visible while patch, conversion, hook, and unknown failures are classified.
- Every patch failure offers user-controlled Retry and Skip actions; neither runs automatically. Skip validates the exact site and patch, requires confirmation, audits the action, and does not auto-retry.
- The MariaDB resolver rejects stale diagnoses, unknown identifiers, injection attempts, unrelated rows/columns, and invalid replacement values.
- Multiple resource locking rejects conflicting site and bench operations.
- Existing CLI `--skip-failing` behavior remains compatible but is not exposed in the new admin update workflow.

## Assumptions

- `MigrationOperation` is the user-facing workflow identity; task IDs are secondary execution details.
- MariaDB safeguards contain compressed per-table dumps plus a pre-migration table inventory and original site configuration. Other supported engines use a full database backup until selective restore is implemented for them. Public and private files remain excluded.
- Whole-update Restore means restoring selected app revisions, selectively reversing database changes on sites whose migration ran, leaving untouched pending sites alone, restoring original configurations, and rebuilding dependencies/assets from restored code.
- Frappe versions supported by the one-click action provide `bypass-patch`; otherwise Pilot shows the equivalent manual command and disables that action.

## Implementation Plan (TODO)

Two milestones. **Milestone 1** makes migration safe and recoverable with *manual* repair (stop-on-failure, snapshots, Retry/Restore, Skip-patch, manual guidance). **Milestone 2** adds the *interactive guided fixing* discussed separately — classify each failure, find the exact offending rows, and give the user an interface to edit/fix them in place. Milestone 2 is fully separable: everything in Milestone 1 ships and is useful without it, and unknown/unresolved failures always fall back to manual repair. Do not start Milestone 2 until Milestone 1 is merged.

Ordered by dependency. Each phase is a single landable, tested commit. Do not start a phase until the ones it depends on are merged. Every phase runs `uv run ruff check admin pilot tests` and its targeted tests before commit.

---

## Milestone 1 — Safe & recoverable migration (manual repair)

### Phase 0 — Groundwork (no behavior change)
- [x] Route the CLI/`SiteCommands.migrate()` and `MigrateTask` through one shared `Site.migrate()` path. Make it tee output: stream live to the task while returning the full captured output for later classification. (Plan §Failure Diagnosis)
- [x] Add a `resource_keys: list[str]` path to task queueing/locking alongside the existing single `resource_key` (union of keys), so update/retry/restore can claim bench + every affected site. Keep single-key callers working. (Plan §Migration UI and APIs)
- [ ] Tests: shared migrate path returns captured output; multi-key lock rejects a conflicting site op.

### Phase 1 — MigrationOperation domain object (foundation, everything depends on this)
- [x] New `pilot/core/bench/migration/` package: `MigrationOperation` dataclass + a JSON store persisted atomically via `pilot.internal.atomic_file`, one file per operation under the bench dir. One owner for the state; no task writes it directly.
- [x] State machine: `preparing | updating | migrating | needs_attention | retrying | restoring | completed | restored | restore_failed`. A single `transition(to)` validates every edge and rejects illegal ones loudly.
- [x] Fields per plan §Migration Operation: kind, timestamps/duration, selected apps + pre-update SHAs, ordered sites + original config snapshots, per-site snapshot/table/migration status, cumulative touched-table set, current phase, failed site, structured diagnosis, available actions, root + child task IDs, safeguards-disabled flag, restore checkpoints.
- [x] `execute_update()` / `execute_site_migrate()` orchestration methods (called by tasks). For now they wrap the *existing* update/migrate flow: create op → maintenance mode → update code → migrate sites sequentially, persisting after each → stop at first failure → `needs_attention` (task exits FAILED, op unresolved). Snapshots stubbed/skipped this phase.
- [x] Read `touched_tables.json` in a `finally` after each migrate attempt; union into the op.
- [x] Retain completed/failed op summaries independent of task logs.
- [ ] Tests: creation, atomic persistence, every valid transition + rejected invalid ones, stop-on-first-failure record, survival after task cleanup, touched-table union across attempts.

### Phase 2 — Wire tasks as thin adapters
- [x] `UpdateTask -> operation.execute_update()`, `MigrateTask -> operation.execute_site_migrate()`. Task ending never resolves the op.
- [x] Add `RetryUpdateTask`, `RestoreMigrationTask` as thin task shells delegating to op methods (added in Phase 4 with their op methods). `MigrationFixTask`/`BypassPatchTask` land with Phases 5/8.
- [ ] Tests: task FAILED + op `needs_attention` on migration failure; no paused status introduced.

### Phase 3 — Selective table safeguards (SiteMigrationSnapshot)
- [x] `SiteMigrationSnapshot` service under the site core (MariaDB first). Owns `<site>/.migrate/` (restrictive perms), per-table `.migrate/<table>.sql.gz`, `previous_tables.json`, original config snapshot.
- [x] Clear/recreate `.migrate/` only under the exclusive migration lock with no unresolved op present. Keep artifacts until completion/restore/explicit discard. Not shown in Backups UI, not pruned by retention.
- [x] Wire into `execute_update`: maintenance mode → snapshot every site → abort+restore maintenance if any safeguard fails, before any code change. Record safeguards-disabled when opted out.
- [ ] Protected Git refs pinning old SHAs so shallow-clone cleanup can't drop them; release on completion/restore.  _(deferred: restore refetches the SHA from origin; local ref pinning only needed for pruned/force-pushed origins)_
- [ ] PostgreSQL/SQLite: same interface, full-DB fallback. _(v1: raises SnapshotUnsupported and disables Restore for non-MariaDB; full-DB fallback still to build)_
- [ ] Tests: writes to `.migrate/<table>.sql.gz`; unresolved op blocks clearing; safeguard failure aborts unchanged; pins survive `needs_attention`, released after finish.

### Phase 4 — Retry & Restore
- [x] `retry()`: only from `needs_attention`, no action running. Re-migrate failed site with normal patch behavior, skip successful sites, continue pending; on total success restart services + restore maintenance + `completed`; on failure replace diagnosis, back to `needs_attention`.
- [x] `restore()`: only when all safeguards created. Restore selected apps to captured SHAs, reinstall deps + rebuild assets; per migrated site selectively import cumulative touched tables and drop `current - previous` tables; skip pending sites; keep maintenance forced until all steps succeed → `restored`. Checkpoint each table import/drop; on failure `restore_failed`, resume from checkpoint. Fallback to all `previous_tables` when touched set is missing/untrusted.
- [ ] Tests: retry resume semantics; restore selective import + created-table drop + fallback; opt-out removes Restore but keeps Retry.

### Phase 5 — Patch-failure diagnosis & Skip-this-patch
- [ ] Structured diagnosis (`phase, patch, table, column, database_engine, failure_kind, message, output_excerpt, resolver_id`) from captured output; track latest `Executing <patch> ...` line to name the failing patch; classify known failure kinds, retain unknown without guessing. (`resolver_id` populated in Milestone 2; null here.)
- [ ] `bypass_patch()`: explicit only, strong permanence warning + confirm, queues `bypass-patch <patch> --yes`, records audit, adds `tabPatch Log` to touched set, returns to `needs_attention`. Never auto-run; Retry and Skip-this-patch always both offered, never automatic. Disable/hide when the Frappe version lacks `bypass-patch` (show manual command).
- [ ] Tests: patch identified from output; bypass validates exact site+patch, audits, no auto-retry; version-gated fallback.

### Phase 6 — APIs (recovery core)
- [ ] `POST /updates` (creates `kind="update"` op + task), standalone migrate endpoint creates `kind="site_migrate"`. Both return op + task IDs.
- [ ] `GET /migrations?cursor=&limit=&status=&kind=&site=`, `GET /migrations/current`, `GET /migrations/<id>`.
- [ ] Action endpoints `retry`, `restore`, `bypass-patch`: validate op state, queue task, attach task ID, return both IDs. Claim bench + affected-site resource keys (Phase 0). (`apply-fix` lands in Milestone 2.)
- [ ] Responses expose durable op summaries + nullable task-log links; legacy `update`/`migrate` tasks surface as read-only rows.
- [ ] Tests: state validation, cursor pagination, filters, legacy rows, nav-to-operation on create.

### Phase 7 — Admin UI (recovery core)
- [ ] One `MigrationStatusButton` in `MainLayout`; remove `UpdatesAvailableButton` from individual pages. Priority: failed > active > update-available.
- [ ] Global migration composable: load current op, poll while active, refresh after task completion + each action.
- [ ] Routes `/migrations` (index: pinned active op, cursor list, filters) and `/migrations/:operationId` (live + historical detail; Retry/Restore/confirmed Skip-this-patch actions + manual-repair guidance; SSE stream with polling reconnect fallback).
- [ ] Starting update/migrate navigates here, not Task Detail. Add a Migrations nav entry that always opens history.
- [ ] Frontend tests per plan §Test Plan UI rows.

---

## Milestone 2 — Interactive guided fixing (per-issue row editing)

Depends on all of Milestone 1. Adds the guided path for known, machine-fixable failures on top of the existing manual/Retry/Skip flow. Unknown or unresolved failures keep falling back to Milestone 1's manual repair.

### Phase 8 — Resolver registry & first resolver
- [ ] Resolver registry (match/read/validate/write per resolver); populate `diagnosis.resolver_id` in Phase 5's classifier when a failure matches a registered resolver.
- [ ] First resolver: MariaDB string→number conversion. Read step lists *only* the affected rows; user replaces the diagnosed value with a valid number or `NULL`.
- [ ] Write guards: op still in the same failed state, table/column validated against `information_schema`, writes restricted to the diagnosed column + returned row names, target type validated, all values bound as query params. Adds the edited table to the cumulative touched set.
- [ ] `apply_fix()` op method + `MigrationFixTask` body; returns op to `needs_attention` (user still chooses Retry).
- [ ] PostgreSQL, unresolved row identities, other classes → manual repair (no resolver).
- [ ] Tests: resolver rejects stale diagnosis, unknown identifiers, injection, unrelated rows/cols, invalid replacement values; touched-set update.

### Phase 9 — Guided-fix API & UI
- [ ] `POST /migrations/<id>/actions/apply-fix`: validate op state, queue `MigrationFixTask`, attach task ID.
- [ ] Detail page: when `resolver_id` is set, render the affected-row list with inline edit (value / NULL) and an apply action, alongside the existing Retry/Skip/manual options.
- [ ] Tests: affected-row listing, edit + apply round-trip, rejects invalid input at the boundary, no-op when diagnosis is stale.

### Cross-cutting
- Keep task execution/logging/cancellation strictly separate from op recovery state.
- MariaDB is the only engine with selective restore in v1; Postgres/SQLite use full-DB fallback throughout.
- Update `SPEC.md` and memory index when the domain object lands.
- Add tests at the end, no need to do on every iteration
