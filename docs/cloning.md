# Bench and site cloning

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

Both sites then share app code and the bench's runtime, while their databases and uploads are independent. Clone the bench first when you need different code or runtime settings. Destination benches must contain the source site's apps and have its database engine configured. Cloning preserves the source site's actual engine, even when it differs from either bench's default. Code changes that require a schema update need a migration on the copied site before use; copying data does not make incompatible app versions compatible.

Bench cloning defaults to each app's origin default branch, discovered live rather than assuming `main`, `master` or `develop`. It fetches the selected branch in the destination and creates a clean checkout, excluding feature-branch commits and local changes. A reachable origin is required for default and named branches. Override with `--branch current` to copy the checked-out branch including staged, uncommitted and untracked files, or `--branch NAME` to select a clean origin branch for all apps. Use `--app-branches frappe=develop,my_app=feature/example` to override individual apps; `default` and `current` also work per app. In Admin, expand App branches in Clone Bench to see a dropdown for each app. Each dropdown lists origin branch names with its actual default branch selected; choosing another branch overrides that app only.

```bash
pilot clone-bench uat                                      # each app's origin default
pilot clone-bench task-a --branch current                   # include your working changes
pilot clone-bench uat --app-branches my_app=feature/example # override one app
```

Current-checkout copying uses `cp -a --reflink=auto` on Linux; macOS tries native copy-on-write cloning and falls back to ordinary copying when unavailable. Copies have independent writes in either case. Performance depends on filesystem support. Git repositories are independent, including when the source is a linked worktree. You can inspect changes with ordinary `git diff` and commit or push without keeping the source worktree available.

Python environments are recreated from uv's package cache so editable imports, entrypoints and shebangs use destination paths. All selected apps are installed together in one uv invocation, resolving their dependencies jointly and preserving each app's configured development extras. When every app uses Current checkout, JS dependencies and built assets are copied, and asset symlinks are relocated into the destination bench; rebuild the source first if its compiled assets need to reflect recent frontend edits. Default or named branches reuse matching installed Node dependencies and completed asset builds, installing or rebuilding when their inputs differ. Sites, logs, production services, domain routes and source Admin credentials are not copied into the bench clone. Start and deploy the new bench through Pilot's usual commands. When cloning site data onto a different code version, ensure its apps support that database and migrate as appropriate.

Full Pilot builds save standard Frappe assets, translations and frontend `dist` outputs under the benches directory's `.pilot/asset-cache`. Cache keys include app file contents, dirty and untracked code, build mode, environment, Node/Yarn/Python versions and installed Python package versions. A default-branch bench clone can reuse a previous build even when the source is now on a feature branch. Custom build hooks and submodules use the normal build path. Dependency copying requires a healthy source install and matching install configuration, environment and local toolchain. Matching package and lockfile contents skip Yarn installation; changed contents seed the destination's `node_modules`, then Yarn reconciles it with the destination lockfile before assets are reused or built. Local/workspace dependencies and install hooks in either checkout use the normal install path. Cached outputs and dependencies are copied into each destination, with links relocated, rather than shared as writable files. The cache contains build outputs only; deleting it while no build or clone is running makes the next clone rebuild. Site cloning overlaps database import and upload copying. Bench cloning overlaps Python installation and dependency copying. All workers finish before site finalization or rollback.

Completed portable Node installs from branch clones are cached under the benches directory's `.pilot/node-cache`. A matching entry is copied before source dependency seeding, so Yarn can skip reconciliation. Keys include package and lockfile contents, install configuration, environment and toolchain. Workspaces, local dependencies, install hooks and links outside the dependency tree are excluded. Each destination has independent writable files. Filling a new entry requires one extra dependency copy; later matching bench clones reuse it. Delete the cache only when no clone is running to make the next clone use source seeding or a normal install.

App repositories are copied or cloned in parallel, with at most four workers. Clean repository clones copy Git objects with reflinks when supported, or ordinary independent copies otherwise. Shallow repositories retain their history boundary. Git metadata is created separately, and connectivity is checked before the copy is accepted. Repositories with alternate, partial or linked object stores use the normal Git clone path, as do copies that fail validation. Each destination retains independent Git files and does not depend on the source object store. All copy workers finish before configuration is saved or a failure is returned.

For MariaDB schemas with at least 16 ordinary InnoDB or MyISAM tables, cloning creates table schemas with four parallel streams, then imports rows with a single data dump and restores sequence settings. Foreign keys, triggers, views, sequence defaults and other engines use the regular full dump, as does a failed or timed-out schema probe. Both paths stream directly into the destination database without writing SQL dump files. PostgreSQL retains its regular dump and restore path.

Site cloning supports MariaDB and PostgreSQL. MariaDB streams `mariadb-dump --single-transaction --quick --skip-lock-tables` into `mariadb`; PostgreSQL streams `pg_dump` into `psql` with import errors stopping the operation. No intermediate SQL dump is written to disk, and failure of either process fails the operation. Credentials are passed through child process environments rather than password arguments. The destination database still consumes storage. The host database server is shared, but each copied site has its own database and user.

The document encryption key is preserved so encrypted fields remain readable. Database credentials and Pilot tokens are regenerated, deployment-specific site configuration is not copied, and the destination Administrator password is generated unless explicitly supplied with `--admin-password` on the CLI. Clones start with the scheduler paused and outgoing mail disabled. Use Pilot's login action to access a cloned site.

The database snapshot is transaction-consistent for transactional tables. Database and upload copies are separate operations; pause source writes when you need them to represent exactly the same instant. Stop schema migrations during copying. Failed site clones roll back their database, login, files and acquired routes. If cleanup fails, Pilot preserves recovery files and reports the cleanup failure alongside the original error. Failed bench clones retain their partial destination for inspection. A site stays in maintenance mode until finalization succeeds. A clone does not mutate source app files or site data.

In Admin, open **Switch Bench**, use a bench's action menu, and choose **Clone bench**. Enter a new bench name; the existing task page reports progress and errors. From **Sites**, use a site's action menu and choose **Clone site**. Pick the destination bench and enter the new hostname. The dialog explains whether app code will be shared and shows the UAT defaults. Both cloning actions require an authenticated bench session because they create resources outside the source site's scope; site-only sessions cannot clone. Cross-bench operations also require bench management to be enabled.

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
