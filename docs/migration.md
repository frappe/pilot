# Migrations

Pilot treats an update or site migration as a recoverable workflow. It does not treat it as one large background task that is forgotten when the task exits.

The main idea is simple:

- A **migration operation** remembers the whole workflow.
- A **task** performs one piece of work.
- The **state machine** decides which piece should run next.

This separation lets Pilot stop safely, show a useful failure later, and continue after a person chooses what to do.

## The Two Main Objects

### MigrationOperation

`MigrationOperation` is the durable source of truth. It records:

- whether this is an app update or a standalone site migration;
- the selected apps, repository URLs, and original and updated commit SHAs;
- the sites, in migration order;
- each site's backup and migration status;
- the current workflow state;
- the failed site and failure diagnosis;
- task IDs, recovery decisions, and restore checkpoints;
- the site's original maintenance and scheduler settings.

The operation is saved after every important change. Its JSON file lives at:

```text
<bench>/migrations/<operation-id>.json
```

This file remains useful even if old task logs are removed.

### Tasks

Tasks are small workers. Each task performs one job and then stops:

| Task | Job |
| --- | --- |
| `MigrationBackupTask` | Create a recovery snapshot for one site |
| `UpdateTask` | Update apps, reinstall dependencies, and rebuild assets |
| `MigrateTask` | Migrate one site |
| `RetryUpdateTask` | Restart the workflow from the failed step |
| `RevertMigrationTask` | Re-arm a failed operation for restore and start the revert chain |
| `RevertAppsTask` | Roll back app revisions, reinstall dependencies, and rebuild assets |
| `RevertSiteTask` | Restore one site's database and clear its cache |
| `RestartServicesTask` | Restart services to finish a restore |
| `BypassPatchTask` | Mark one confirmed failing patch as completed |

A task owns execution details such as output, cancellation, and success or failure. It does not own the overall migration state.

## Normal Workflow

An app update normally follows this path:

```text
preparing
    |
    v
backing_up  -- one task per site
    |
    v
updating    -- one app-update task
    |
    v
migrating   -- one task per site, in order
    |
    v
completed
```

A standalone site migration uses the same workflow but skips the app-update step.


Before the first task is queued, Pilot:

1. records every site's maintenance and scheduler settings;
2. puts every affected site into maintenance mode;
3. saves the operation;
4. queues the first backup task.

Putting all sites into maintenance mode first prevents writes while the bench is partly updated.

## How the State Machine Works

The current state is an object from `pilot/core/bench/migration/state.py`. Each state owns two decisions:

1. Which state transitions are allowed?
2. Which task should run next?

For example:

- `backing_up` chooses the next site whose backup is still pending;
- `updating` chooses the app-update task if apps have not been updated;
- `migrating` chooses the next site whose migration is pending;
- `needs_attention` queues nothing and waits for a person.

An illegal transition fails immediately. Pilot will reject a jump such as:

```text
preparing -> completed
```

This keeps persisted operations from quietly entering an impossible state.

### Recovery States

When work fails, the normal chain pauses:

```text
backing_up / updating / migrating
                 |
                 v
          needs_attention
             /       \
            v         v
       retrying    reverting_apps -> reverting_sites -> restarting
            |         |                    |                 |
            |         v                    v                 v
            |    revert_failed  <----------+-----------------+
            |         |
            |         +----> reverting_apps / reverting_sites / restarting
            |                              |
            |                              v
            |                          reverted
            |
            +----> backing_up / updating / migrating
```

`reverting_apps`, `reverting_sites`, and `restarting` are a chain, one task
each, mirroring `backing_up`/`updating`/`migrating` in the forward flow. A
failure in any of them moves to `revert_failed`; resuming re-enters whichever
sub-phase has unfinished checkpointed work.

`completed` and `reverted` are terminal states. They no longer appear as the current unresolved migration.

## Per-Site Progress

The operation also tracks progress for each site.

Backup status:

```text
pending -> backing_up -> backed_up
                 \
                  -> failed
```

Migration status:

```text
pending -> running -> success
              \
               -> failed -> recovering -> recovered
```

This is why retry can skip sites that already succeeded and continue from the failed site.

## Passing Work to the Next Task

Each chain task asks the operation for the next step after its own work succeeds. The operation reads its current state and queues the selected task.

Migration tasks lock these resources:

```text
bench:update
site:<first-site>
site:<second-site>
```

These locks prevent another update, migration, backup, or restore from changing the same bench or sites at the same time.

When a chain task queues its successor, it explicitly hands its resources to that successor. The task store allows only that exact handoff. Other tasks still receive a resource-conflict error.

## Recovery Snapshots

`SiteMigrationBackup` creates a private recovery directory:

```text
<bench>/sites/<site>/.migrate/
```

MariaDB safeguards contain:

```text
previous_tables.json
site_config.json
<table-name>.sql.gz
```

SQLite and PostgreSQL safeguards instead contain a complete Frappe database backup:

```text
database.sql.gz
site_config.json
```

Frappe creates these full backups with explicit paths inside `.migrate/`. Backup include/exclude settings are ignored so the safeguard always covers the whole database. Files remain excluded.

The snapshot service checks that the requesting operation is the only unresolved operation that owns the site. This prevents a later workflow from erasing an earlier workflow's recovery data.

Migration snapshots are separate from normal user backups. They are not shown in the Backups UI and are not managed by normal backup retention.

## What Happens on Failure

### Backup failure

Pilot stops before updating code, records the failed site, and restores every site's original maintenance and scheduler settings.

### Update or migration failure

Pilot moves the operation to `needs_attention`. Sites remain protected while the user investigates or chooses a recovery action.

For migration failures, Pilot stores useful details when available:

- the failed phase and site;
- the latest executing patch;
- the failure message and captured output;
- the affected database column or table;
- the cumulative set of touched tables.

For MariaDB, if touched-table metadata is missing or unreadable, Pilot marks it as untrusted. Restore then uses every table in the snapshot instead of risking an incomplete selective restore. SQLite and PostgreSQL always restore the complete database backup.

## Adding a Diagnostic

`pilot/core/bench/migration/diagnosis.py` turns a failed `MigrateTask` output into a
structured `diagnosis` dict (`failure_kind`, `patch`, `column`, `resolver_id`, ...).
To recognize a new failure:

1. Add a `(failure_kind, substrings)` entry to `_SIGNATURES`, ordered most-specific
   first — `_classify` returns the first match.
2. If the diagnosis needs a new field (e.g. a table name), extend `diagnose()` and
   add a small `_extract` helper the same way `_column` works.

`resolver_id` is reserved for a future suggestion lookup: once populated, it will key
into a table of human-readable remediation text per `failure_kind`, shown next to
`diagnosis.message` in `MigrationDetail.vue`. It is not read anywhere yet, so setting
it today has no visible effect — leave it `None` until that lookup exists.

## Recovery Actions

### Retry

Retry changes `needs_attention` to `retrying`, resets only the failed unit to pending, and returns to the failed phase.

Successful sites are not migrated again. If the retry succeeds, Pilot continues with the remaining pending sites.

### Skip This Patch

This action is available only when Pilot identified a failing patch.

The API and operation both verify that the requested patch exactly matches the current diagnosis. Pilot then runs Frappe's `bypass-patch` command and records the decision. It does not retry automatically; the user must still choose Retry.

The normal update form never enables broad `--skip-failing` behavior.

### Restore

The Admin API calls this action **Restore**. Internally, the current task and state names use **revert**. Restore runs as its own task chain, one task per unit of work, the same way the forward update/migrate flow does:

1. `RevertMigrationTask` checks that every required safeguard exists, then arms the chain.
2. `RevertAppsTask` returns selected apps to their captured commits, reinstalls dependencies, and rebuilds assets.
3. `RevertSiteTask` runs once per migrated site: selectively restores MariaDB tables (or restores the whole SQLite/PostgreSQL database) and removes tables created by the migration, then clears that site's cache.
4. `RestartServicesTask` restores maintenance and scheduler settings, restarts services, and marks the operation `reverted`.

Each step is checkpointed in `revert_checkpoints` (`apps`, `site:<name>` per site, `restarted`). A failure moves the operation to `revert_failed`; a later Restore resumes from whichever checkpoint is still unset instead of repeating finished work.

## Admin API and UI

The main endpoints are:

```text
POST /updates
POST /sites/<site>/actions/migrate

GET  /migrations
GET  /migrations/current
GET  /migrations/<operation-id>

POST /migrations/<operation-id>/actions/retry
POST /migrations/<operation-id>/actions/restore
POST /migrations/<operation-id>/actions/bypass-patch
```

Starting an update or standalone migration returns both an operation identity and a task ID. The Admin UI navigates to the migration detail page because the operation—not the first task—is the long-lived workflow.

The global migration status button shows unresolved failures before active work, and active work before ordinary update availability.

Each site row links to its retained backup and migration task attempts. The Target Apps card shows the original and updated commit and offers a GitHub comparison link when the app uses a GitHub repository.

## Code Map

```text
pilot/core/bench/migration/
  operation.py    workflow behavior and durable operation data
  state.py        states, allowed transitions, and next-step selection
  store.py        atomic JSON persistence and operation lookup
  diagnosis.py    migration-output classification

pilot/core/site/migration_backup.py
  private per-table snapshot and restore service

pilot/tasks/
  migration_backup.py
  update.py
  migrate.py
  retry_update.py
  revert_migration.py
  revert_apps.py
  revert_site.py
  restart_services.py
  bypass_patch.py

admin/backend/api/v1/migrations.py
  migration history, detail, and recovery endpoints

admin/frontend/src/pages/migrations/
  migration history and detail pages
```

When debugging, start with the operation JSON and its current state. Then follow the latest task ID for execution output.
