# Development forks

Clone bench code and site data separately. A bench clone is an independent development environment with app code, Git repositories, dependencies, assets and new runtime ports. A site clone has a fresh database, credentials, configuration and independent public/private uploads; it runs the destination bench's code. Nothing is taken from a pool of prepared destinations.

```bash
cd benches/dev
pilot clone-bench uat
pilot clone-site production.localhost uat.localhost --target-bench uat
pilot -b uat start
```

Pilot infers the source bench from the current directory, including app and site subdirectories. From elsewhere, use `pilot -b dev clone-bench uat` and `pilot -b dev clone-site production.localhost uat.localhost --target-bench uat`.

For UAT with the same code, clone just the site inside its existing bench:

```bash
pilot clone-site production.localhost uat.localhost
```

Both sites then share app code and the bench's runtime, while their databases and uploads are independent. Clone the bench first when you need different code or runtime settings. Destination benches must contain the source site's apps and use the same database engine. Code changes that require a schema update need a migration on the copied site before use; copying data does not make incompatible app versions compatible.

For a complete development copy with one command:

```bash
pilot fork task-a --site fixture.localhost
```

This composes bench cloning and fresh site cloning, creating `task-a.localhost`. `--site` is optional when the source has exactly one site. From elsewhere, use `pilot fork dev task-a` or `pilot -b dev fork task-a`.

Bench cloning defaults to each app's origin default branch, discovered live rather than assuming `main`, `master` or `develop`. It fetches the selected branch in the destination and creates a clean checkout, excluding feature-branch commits and local changes. A reachable origin is required for default and named branches. Override with `--branch current` to copy the checked-out branch including staged, uncommitted and untracked files, or `--branch NAME` to select a clean origin branch for all apps. Use `--app-branches frappe=develop,my_app=feature/example` to override individual apps; `default` and `current` also work per app. Fresh `pilot fork` accepts the same options. In Admin, expand App branches in Clone Bench to see a dropdown for each app. Each dropdown lists origin branch names with its actual default branch selected; choosing another branch overrides that app only.

```bash
pilot clone-bench uat                                      # each app's origin default
pilot clone-bench task-a --branch current                   # include your working changes
pilot clone-bench uat --app-branches my_app=feature/example # override one app
pilot fork task-a --site fixture.localhost --branch current
```

Current-checkout copying uses `cp -a --reflink=auto` on Linux; macOS tries native copy-on-write cloning and falls back to ordinary copying when unavailable. Copies have independent writes in either case. Performance depends on filesystem support. Git repositories are independent, including when the source is a linked worktree. You can inspect changes with ordinary `git diff` and commit or push without keeping the source worktree available.

Python environments are recreated from uv's package cache so editable imports, entrypoints and shebangs use destination paths. When every app uses Current checkout, JS dependencies and built assets are copied, and asset symlinks are relocated into the destination bench; rebuild the source first if its compiled assets need to reflect recent frontend edits. Default or named branches reuse matching installed Node dependencies and completed asset builds, installing or rebuilding when their inputs differ. Sites, logs, production services, domain routes and source Admin credentials are not copied into the bench clone. Start and deploy the new bench through Pilot's usual commands. When cloning site data onto a different code version, ensure its apps support that database and migrate as appropriate.

Full Pilot builds save standard Frappe assets, translations and frontend `dist` outputs under the benches directory's `.pilot/asset-cache`. Cache keys include app file contents, dirty and untracked code, build mode, environment, Node/Yarn/Python versions and installed Python package versions. A default-branch fork can reuse a previous build even when the source is now on a feature branch. Custom build hooks and submodules use the normal build path. Dependency copies require matching package and lockfile contents, install configuration, environment and local toolchain; local/workspace dependencies and install hooks use the normal install path. Cached outputs and dependencies are copied into each destination, with links relocated, rather than shared as writable files. The cache contains build outputs only; deleting it while no build or clone is running makes the next clone rebuild. `pilot fork` overlaps fresh database import, upload copying and environment preparation; Python installation also overlaps dependency copying. All workers finish before site finalization or rollback.

For MariaDB schemas with at least 16 ordinary InnoDB or MyISAM tables, cloning creates table schemas with four parallel streams, then imports rows with a single data dump and restores sequence settings. Foreign keys, triggers, views, sequence defaults and other engines use the regular full dump. Both paths stream directly into the destination database without writing SQL dump files. PostgreSQL retains its regular dump and restore path.

Site cloning supports MariaDB and PostgreSQL. MariaDB streams `mariadb-dump --single-transaction --quick --skip-lock-tables` into `mariadb`; PostgreSQL streams `pg_dump` into `psql` with import errors stopping the operation. No intermediate SQL dump is written to disk, and failure of either process fails the operation. Credentials are passed through child process environments rather than password arguments. The destination database still consumes storage. The host database server is shared, but each copied site has its own database and user.

The document encryption key is preserved so encrypted fields remain readable. Database credentials and Pilot tokens are regenerated, deployment-specific site configuration is not copied, and the destination Administrator password is reset (`admin` by default on the CLI; a generated password through Admin). Clones start with the scheduler paused and outgoing mail disabled. Use Pilot's Admin login action to access a site created through the UI.

The database snapshot is transaction-consistent for transactional tables. Database and upload copies are separate operations; pause source writes when you need them to represent exactly the same instant. Stop schema migrations during copying. Failed site clones roll back their database, login, files and acquired routes. If cleanup fails, Pilot preserves recovery files and reports the cleanup failure alongside the original error. Failed bench clones retain their partial destination for inspection. A site stays in maintenance mode until finalization succeeds. A clone does not mutate source app files or site data.

In Admin, open **Switch Bench**, use a bench's action menu, and choose **Clone bench**. Enter a new bench name; the existing task page reports progress and errors. From **Sites**, use a site's action menu and choose **Clone site**. Pick the destination bench and enter the new hostname. The dialog explains whether app code will be shared and shows the UAT defaults. Cross-bench operations require bench management to be enabled and an authenticated bench session.

After editing copied code, migrate and build only the destination:

```bash
pilot -b uat frappe -- --site uat.localhost migrate
pilot -b uat build
```

Stop the copied bench, drop its sites, then drop the bench when done. Preserve any code changes you want to keep before dropping it.

```bash
pilot -b uat stop
pilot -b uat drop-site uat.localhost
pilot -b uat drop
```

Programmatic callers use `source.clone("uat")` and `source.site("production.localhost").clone("uat.localhost", destination)`. CLI and Admin tasks call these same core operations. Admin queues `CloneBenchTask` and `CloneSiteTask` with task progress, idempotency and resource reservations.

## Optional prepared snapshots and worktrees

The explicit template path remains available for workflows that need a fixed fixture and recorded app commits:

```bash
pilot -b dev prepare-template fixture.localhost /absolute/path/to/templates/base
pilot fork dev task-a --site-template /absolute/path/to/templates/base
```

Template preparation builds assets and saves the database and file backups. Passing `--site-template` explicitly selects the existing worktree implementation: independent app worktrees on `agent/<name>-<suffix>` branches, a new Python environment and fresh site restoration. Each restored site gets a generated Administrator password; use Pilot's Admin login action to access it. `fork.json` records its status and phase timings. Keep source repositories available while these optional worktrees use their objects. Normal bench and site cloning does not require a template or linked worktrees.
