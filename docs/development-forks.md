# Development forks

Create a fresh copy of a development bench and its current site with one command. Commit source changes first; each copy records exact commits for Frappe and every app. MariaDB and PostgreSQL are supported. Use unencrypted backups for these local snapshots.

```bash
cd benches/dev
pilot fork task-a
```

The source bench is inferred from the current directory, including app and site subdirectories. From elsewhere, use `pilot fork dev task-a` or `pilot -b dev fork task-a`. The command takes a new database and file backup, creates independent app worktrees and a Python environment, and restores into a newly created database with new credentials. It never assigns an existing destination. If the source has multiple sites, pass `--site fixture.localhost`. Existing built assets and JS dependencies are copied directly from the source, without an intermediate asset cache or rebuild. Rebuild the source first if you want its compiled assets to reflect recent frontend edits; forks copy the current source's built files. The source can be running or stopped; only temporary Redis processes started by the fork command are stopped afterwards. Database server availability is required.

For a fixed fixture snapshot reused across fresh destinations, prepare a template explicitly:

```bash
pilot -b dev prepare-template fixture.localhost /absolute/path/to/templates/base
pilot fork dev task-a --site-template /absolute/path/to/templates/base
pilot fork dev task-b --site-template /absolute/path/to/templates/base
pilot -b task-a start
```

Preparation builds assets once, saves the database and public/private files, and caches JS dependencies and assets. Each fork checks out the recorded commits as independent Git worktrees on new `agent/<name>-<suffix>` branches. Python environments are created independently using uv's shared package cache. JS dependencies and assets are copied with copy-on-write where supported, with independent copies elsewhere. Asset links point into the destination bench.

After installing Python dependencies, forks restore the database while copying JS dependencies and assets in parallel. Prepared JS dependencies skip Yarn installation; missing dependencies still use the normal install path. `fork.json` reports Python, dependency copy and restore timings separately; overlapping phases do not add up to total elapsed time.

Forks share the host database server but have separate databases, database users, Redis instances, ports, site files, configs and logs. The fixture's document encryption key is preserved so encrypted fields remain readable; database credentials and Pilot tokens are generated for the destination. Domains and other deployment settings are not copied. The fixture Administrator password is reset to `admin`, and its scheduler remains paused. Starting the fork uses Pilot's normal development runtime, including lite mode when its Frappe checkout supports it.

The fork command prints progress followed by a JSON object containing its path, site URL and start command. `fork.json` records whether provisioning succeeded and elapsed seconds for each phase. A failure leaves the destination available for inspection; it does not reset source repositories or remove the destination database.

Edit both Frappe and apps inside `benches/task-a/apps/`. After schema or patch changes, migrate only that fork; after frontend changes, build only that fork:

```bash
pilot -b task-a frappe -- --site task-a.localhost migrate
pilot -b task-a build
```

Create a new template when the fixture or dependency baseline changes. Existing templates keep their recorded commits even when the source bench advances. Keep the source repositories available while forks use their Git objects. Pilot refuses to remove a repository that still owns linked worktrees.

Commit or stash agent edits before cleanup. Stop the fork, drop its site using Pilot's usual site command, then drop the bench. Worktree removal unregisters it from Git; its branch and committed changes remain available in the source repository. A new fork can reuse the bench name with a fresh branch.

```bash
pilot -b task-a stop
pilot -b task-a drop-site task-a.localhost
pilot -b task-a drop
```

Programmatic callers use `source.site("fixture.localhost").prepare_template(path)` and `source.fork("task-a", path)`. Admin callers can queue `ForkBenchTask.queue(source, target="task-a", site_template=str(path))`; the task uses the same implementation and reports phase progress.

Without a template, Python callers use `source.fork("task-a", site="fixture.localhost")` to snapshot the current site and create a fresh destination. Automatic snapshots are retained under `benches/.fork-templates/` for inspection; they contain database and file backups, while built artifacts are copied directly from their source bench during that operation.
