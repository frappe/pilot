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
- the selected apps, repository URLs, original and updated commit SHAs, and whether each app needs a migrate;
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
| `UpdateTask` | Update apps, validate them, reinstall dependencies, and rebuild assets |
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

A standalone site migration uses the same workflow but skips the app-update step. It runs in place: it takes no safeguard backup, so it cannot be reverted. A branch switch also uses this workflow, and it does take the safeguard backups.

Before the first task is queued, Pilot:

1. records each migrating site's maintenance and scheduler settings;
2. puts those sites into maintenance mode;
3. saves the operation;
4. queues the first task.

Putting the sites into maintenance mode first prevents writes while the bench is partly updated.

Maintenance mode also stops each site's scheduler, but jobs that are already queued or running continue. Before a site's backup, before the app update, and before a site's migrate, Pilot runs Frappe's `ready-for-migration` for the site until it reports no queued or running job, for up to 300 seconds. This is what Frappe Cloud does before it migrates a site. If the jobs do not finish in time, the operation goes to `needs_attention` before the step starts, so a retry runs the same step again. Pilot skips the wait while the workload is stopped, because then no worker runs jobs.

Frappe reads a site's config again only after 60 seconds. So before the first step, Pilot restarts web once, and web stops serving the sites at once. When Pilot turns maintenance mode off, it restarts web again, so the sites answer at once instead of up to 60 seconds later. Under systemd, the web socket stays open during restart and queues new requests. Supervisor and older lite-mode runners briefly refuse requests while web starts.

If either web restart fails, the task fails and the operation pauses for Retry. A failed restart cannot be treated as a completed maintenance change.

### Which Sites Are Migrated

An update backs up and migrates sites only when an app change needs a migrate. When the update is created, Pilot compares each app's installed commit with its target commit. An app needs a migrate when the diff changes any of these, the same rule Frappe Cloud uses:

- `<app>/patches.txt`
- `<app>/hooks.py`
- `<app>/fixtures/`
- `<app>/<module>/custom/`
- a doctype-style JSON file, `<app>/<module>/<type>/<name>/<name>.json`
- `frappe/geo/languages.json`

When Pilot cannot read the diff, it assumes a migrate is needed. When any selected app needs a migrate, every affected site is backed up and migrated. When none does, the update only updates the apps, rebuilds the assets, and restarts the services, and the update dialog hides the backup option. Each app records the result in `has_migrate_changes`. The rule is in `AppRepository.has_migrate_changes`.

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

Pilot stops before updating code, records the failed site, and restores every site's original maintenance and scheduler settings. Retry takes fresh backups of every site because the sites can receive new writes while the operation is paused.

### Update or migration failure

Pilot moves the operation to `needs_attention`. Sites remain protected while the user investigates or chooses a recovery action.

A backup, update or migrate task that is cancelled, or whose process dies, has the same result: its failure callback moves the operation to `needs_attention` with the diagnosis "The task stopped before it finished", so the user can retry or revert. A stopped backup also restores the sites' maintenance settings. The callback does nothing when the task had already queued the next task.

Every app is checked out to its target revision first, then validated as a group, before anything is installed or built.
A failure there means no app has been reinstalled yet, so `RevertAppsTask` only has commits to check back out. See
[App Dependencies](app-dependencies.md) for which checks an update runs.

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

Retry changes `needs_attention` to `retrying` and returns to the failed phase. It resets every backup to pending after a backup-phase failure; otherwise it resets only the failed unit.

Successful sites are not migrated again. If the retry succeeds, Pilot continues with the remaining pending sites.

### Skip This Patch

This action is available only when Pilot identified a failing patch.

The API and operation both verify that the requested patch exactly matches the current diagnosis. Pilot then runs Frappe's `bypass-patch` command and records the decision.

`bypass_patch` re-arms the chain before returning: the failed site goes back to pending and the operation returns to `migrating`, so `BypassPatchTask` queues the next step itself. The migration resumes on its own — the user does not choose Retry afterwards.

The normal update form never enables broad `--skip-failing` behavior.

### Restore

The Admin API calls this action **Restore**. Internally, the current task and state names use **revert**. Restore runs as its own task chain, one task per unit of work, the same way the forward update/migrate flow does:

1. `RevertMigrationTask` checks that every required safeguard exists, then arms the chain.
2. `RevertAppsTask` returns selected apps to their captured commits, reinstalls dependencies, and rebuilds assets.
3. `RevertSiteTask` runs once per migrated site: selectively restores MariaDB tables (or restores the whole SQLite/PostgreSQL database) and removes tables created by the migration, then clears that site's cache.
4. `RestartServicesTask` restores maintenance and scheduler settings, restarts services, and marks the operation `reverted`.

Each step is checkpointed in `revert_checkpoints` (`apps`, `site:<name>` per site, `restarted`). A failure moves the operation to `revert_failed`; a later Restore resumes from whichever checkpoint is still unset instead of repeating finished work.

Before importing MariaDB tables, Restore checks that the saved table inventory is valid and every required table dump exists. A missing inventory or dump stops Restore before database changes. Repair the recovery files, then use Restore again.

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

The list page filters by status through five tabs — All, Running, Attention, Completed, Reverted — held in the `status` query parameter so a filtered view survives a reload. Running covers every in-flight state and Attention covers both `needs_attention` and `revert_failed`, groupings the `status` API parameter cannot express because it matches one state exactly; the list therefore filters client-side over the page it already fetched.

The detail page groups the chain into a Server section and a Sites section, split on whether a `task_logs` entry names a site: bench-level tasks (`update`, `revert-apps`, `restart-services`) on one side, per-site ones under their site row on the other. Every entry carries the task's own `status`, which is how the UI marks the attempt that failed; it is `null` only when the task is no longer readable. The Target Apps card shows the original and updated commit and offers a GitHub comparison link when the app uses a GitHub repository.

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

admin/frontend/dashboard/src/pages/migrations/
  migration history and detail pages
```

When debugging, start with the operation JSON and its current state. Then follow the latest task ID for execution output.
