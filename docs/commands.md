# Commands Specification

---

## `bench new`

Scaffolds a starter `bench.toml` inside a new bench directory.

**Pre-conditions:** No bench directory with the given name exists under `benches/`.

**Steps:**
1. Check that `benches/<name>/` does not already exist. If it does, print an error and exit.
2. Create `benches/<name>/`.
3. Write a minimal `bench.toml` with placeholder values to `benches/<name>/bench.toml`. Ports are auto-offset so the bench doesn't collide with existing ones. On Linux the bench is also given its **own** MariaDB instance (`mariadb.instance = <name>`, plus a per-instance socket/datadir) — see [Per-bench MariaDB instances](architecture.md#per-bench-mariadb-instances). On macOS it stays on the shared Homebrew MariaDB.
4. Print a message telling the user to edit the file and then run `bench init -b <name>`.

**Does not** touch the filesystem beyond creating the directory and writing `bench.toml`.

---

## `bench init`

Installs and configures the entire environment described in `bench.toml`. Safe to re-run — each step checks whether it has already been done.

### Pre-conditions

- A bench is named explicitly: pass `-b <name>` or run from inside the bench dir (no auto-pick).
- `bench.toml` exists and is valid.
- **Ubuntu:** The process has `sudo` access (required for `apt-get`).
- **macOS:** Homebrew is installed (`brew` is in `$PATH`). No `sudo` required — Homebrew installs to user-owned directories.

### Passwordless sudo setup (optional)

```
bench init --sudo-password <password>
```

Passing `--sudo-password` writes a sudoers drop-in at `/etc/sudoers.d/<user>` so that `apt-get`, `nginx`, `systemctl`, `loginctl`, `ln`, `unlink`, `zpool`, `zfs`, and `rsync` can all run without a password prompt during and after setup.

**The password is never stored.** It is forwarded directly to `sudo -S tee` in a single subprocess call and discarded immediately. Nothing is written to disk, logged, or retained in memory beyond that call.

The sudoers file grants `NOPASSWD` only for the specific commands bench manages:

```
<user> ALL=(ALL) NOPASSWD: /usr/bin/apt-get
<user> ALL=(ALL) NOPASSWD: /usr/sbin/nginx
<user> ALL=(ALL) NOPASSWD: /usr/bin/systemctl
<user> ALL=(ALL) NOPASSWD: /usr/bin/loginctl
<user> ALL=(ALL) NOPASSWD: /usr/bin/ln
<user> ALL=(ALL) NOPASSWD: /usr/bin/unlink
<user> ALL=(ALL) NOPASSWD: /usr/sbin/zpool
<user> ALL=(ALL) NOPASSWD: /usr/sbin/zfs
<user> ALL=(ALL) NOPASSWD: /usr/bin/rsync
```

The write is idempotent — if all of these rules are already present in the file, the step is skipped entirely.

If the `IS_SUDOERS_SETUP` environment variable is set, `bench init` assumes the sudoers file is already in place and skips the step without asking for a password. This is the expected state in CI and managed deployments where the file is provisioned externally.

### Steps

```
0.  Configure passwordless sudo (only when --sudo-password is given and IS_SUDOERS_SETUP is unset)
1.  Validate bench.toml
2.  Install system packages
2b. Set up ZFS volumes (Linux only — mandatory) — resolves backing = "auto"
    by discovering an unused disk (or falling back to a disk image) and persists the
    resolved values to bench.toml; see docs/volume.md
3.  Create bench directory structure
4.  Create Python virtualenv
5.  Clone and install framework app
6.  Install Node.js
7.  Install Node.js dependencies
8.  Configure Redis
9.  Generate Procfile
```

#### Step 1 — Validate bench.toml

`BenchConfig.from_file('bench.toml')` runs all validation rules. On failure, print the error and exit with code 1. No filesystem changes have occurred at this point.

#### Step 2 — Install system packages

`MariaDBManager.install()` and `RedisManager.install()` each check `is_installed()` first and skip if already present. The package manager is selected by `get_package_manager()` from `bench_cli.platform`.

**Ubuntu (apt):**
- `mariadb-server`
- `redis-server`
- `python3-<version>` and `python3-<version>-venv` (from deadsnakes PPA if needed)
- `git`

**macOS (Homebrew):**
- `mariadb`
- `redis`
- `python@<version>` (if the requested version is not already available)
- `git` (usually pre-installed via Xcode CLT)

`libmysqlclient-dev` is **not** needed on either platform — bench uses `PyMySQL`, which is pure Python and requires no C extension.

After installation, MariaDB is started:
- **Shared server** (no `mariadb.instance`): `MariaDBManager.start()` runs `systemctl start mariadb` (Ubuntu) / `brew services start mariadb` (macOS), then sets the root password if the install is fresh.
- **Own instance** (`mariadb.instance` set, Linux): `MariaDBManager.provision_instance()` stages the `[mariadbd.<instance>]` config, creates the datadir, runs `systemctl enable --now mariadb@<instance>`, and secures it. This runs **after** ZFS volume setup so a volume-backed datadir is mounted first. See [Per-bench MariaDB instances](architecture.md#per-bench-mariadb-instances).

#### Step 3 — Create bench directory structure

`Bench.create_directories()` creates:
- `apps/`
- `sites/`
- `sites/assets/`
- `logs/`
- `config/`
- `pids/`

All created with `exist_ok=True`.

#### Step 4 — Create Python virtualenv

`PythonEnvManager.create_venv()` runs `uv venv` with the requested Python version:
```
uv venv --python <version> env/
```
`uv` is auto-installed if not present. Skipped if `env/bin/python` already exists.

#### Step 5 — Clone and install framework app

For each `AppConfig` in `bench.init_apps()` (reads from `bench.toml [[apps]]`):
- Skip if `App.is_cloned` is already `True`.
- `App.clone()` runs:
  ```
  git clone <repo> --branch <branch> --depth 1 apps/<name>
  ```
- `PythonEnvManager.install_app(app)` runs:
  ```
  uv pip install -e apps/<name>
  ```

This installs the framework app and all its dependencies. After `bench init`, additional apps are added via `bench get-app`.

#### Step 6 — Install Node.js

`PythonEnvManager.install_node()` checks if `node` is present. If not, installs Node.js 24 via the NodeSource setup script.

Yarn is installed globally afterward: `npm install -g yarn`.

#### Step 7 — Install Node.js dependencies

`PythonEnvManager.install_node_dependencies()` runs `yarn install` for each app in `apps/` that has a `package.json`.

#### Step 8 — Configure Redis

`RedisManager.generate_configs()` writes config files to `config/`. The output depends on whether single-instance or multi-instance mode is used.

**Single-instance mode** (`redis.port` is set):

**`redis.conf`**
```
port 13000
bind 127.0.0.1
```

**Multi-instance mode** (`cache_port`/`queue_port`):

**`redis_cache.conf`** / **`redis_queue.conf`**
```
port <N>
bind 127.0.0.1
```

There is no dedicated socketio Redis — socketio shares the cache instance, so
`common_site_config.json` sets `redis_socketio` equal to `redis_cache`.

Existing files are overwritten.

#### Step 9 — Generate Procfile

Writes `config/Procfile` with one line per process: web server, socketio, admin UI, workers, and Redis.

Single-instance Redis:
```
web: cd sites && env/bin/bench frappe serve --port 8000 --noreload
socketio: env/bin/python -m frappe.realtime.server  # python backend (default); runs from bench root
admin: PYTHONPATH=<cli_root> .admin-venv/bin/python -m admin.backend.server --bench-root <bench> --port 8002
worker_default_1: cd sites && env/bin/bench frappe worker --queue default
worker_default_2: cd sites && env/bin/bench frappe worker --queue default
worker_short_1: cd sites && env/bin/bench frappe worker --queue short
worker_long_1: cd sites && env/bin/bench frappe worker --queue long
redis: redis-server config/redis.conf
```

Multi-instance Redis:
```
...
redis_cache: redis-server config/redis_cache.conf
redis_queue: redis-server config/redis_queue.conf
```

On completion, prints:
```
bench init complete. Next steps:
  bench new-site site1.localhost  # create your first site
  bench start                     # start all processes
```

---

## `bench get-app`

Clones an app from a git repository and installs it into the virtualenv.

```bash
bench get-app https://github.com/frappe/erpnext --branch version-16
```

### Steps

```
1.  Clone the app
2.  Install Python dependencies
3.  Update apps.txt
```

#### Step 1 — Clone the app

`App.clone()` runs `git clone <repo> --branch <branch> --depth 1 apps/<name>`. The app name is inferred from the repository URL (last path component, without `.git`). Skipped if already cloned.

#### Step 2 — Install Python dependencies

`PythonEnvManager.install_app(app)` runs `uv pip install -e apps/<name>`.

#### Step 3 — Update apps.txt

Appends the app name to `sites/apps.txt`. Does **not** modify `bench.toml`.

---

## `bench new-site`

Creates a new Frappe site.

```bash
bench new-site site1.localhost
bench new-site site1.localhost --admin-password admin
```

**Pre-conditions:**
- The site doesn't already exist.
- The name is free across all benches — not a sibling bench's site/alias or admin domain (`host_owner`), and not this bench's own admin domain.
- If this bench has wildcard domains configured (see [docs/production.md](production.md#custom-domain-management)), the name matches one of them.

### Steps

```
1.  Check site does not already exist
2.  Create the site
3.  Update common_site_config.json
```

#### Step 2 — Create the site

`Site.create(mariadb_config)` runs the framework app's `new-site` command:
```
env/bin/bench new-site <site.name>
    --mariadb-root-password <root_password>
    --admin-password <admin_password>
    --no-mariadb-socket
```

frappe generates and manages the database name and credentials internally; they are written into `sites/<name>/site_config.json`. The site directory is created on disk — it is **not** written to `bench.toml`.

#### Step 3 — Update common_site_config.json

`Bench.write_common_site_config()` rewrites `sites/common_site_config.json` with Redis URLs and the default site. Sites are discovered from the filesystem (`sites/` directory), not from `bench.toml`.

---

## `bench rename-site`

Renames a site within the current bench.

```bash
bench rename-site old.localhost new.localhost
```

**Pre-conditions:**
- The old site exists in this bench.
- The new hostname is free across **all** benches — not already a site (or alias) of any sibling bench, not a sibling's admin domain (via `host_owner`), and not this bench's own admin domain. All benches share one nginx, so hostnames must be unique.

**Steps:**
1. Move `sites/<old>` → `sites/<new>` (the DB is untouched — `db_name` lives in `site_config.json`).
2. Update `default_site` in `common_site_config.json` if it pointed at the old name, and rename any `[[sites]]` entry in `bench.toml`.
3. Drop the stale `config/nginx/sites/<old>.conf`; in dev mode add the new host to `/etc/hosts`; if production is enabled, regenerate and reload nginx.

**After renaming**, the applicable follow-up is run automatically for the new domain (if it fails, the manual command to run is printed):
- If the bench runs in production: `bench setup production` (refreshes services/nginx and reissues certs for SSL sites).
- Else if the site had TLS enabled (`ssl = true`): `bench setup letsencrypt`.

---

## `bench start`

Starts all bench processes using the built-in Procfile runner.

### Pre-conditions

- `bench init` has been run at least once (`config/Procfile` exists).
- MariaDB service is running on the host.

### Steps

```
1.  Check Procfile exists
2.  Start processes
```

#### Step 1 — Check Procfile exists

If `config/Procfile` is missing, print a message telling the user to run `bench init` first and exit with code 1.

#### Step 2 — Start processes

`ProcessManager.start()` reads `config/Procfile` and spawns each process with `subprocess.Popen`. A dedicated thread per process streams output to stdout with a color-coded `[<name>]` prefix — each process name gets a distinct ANSI color so concurrent output is easy to read. Per-process PID files are written to `pids/<name>.pid`.

The `admin:` entry in the Procfile means the admin UI is always available at `http://localhost:8002` while the bench is running.

`bench start` **blocks** — it stays in the foreground until `SIGINT` (Ctrl-C). On `SIGINT`, all child processes receive `SIGTERM` and are waited on before the parent exits.

---

## `bench ls`

Lists every bench found under `benches/`, with its runtime status (running/stopped), production state, and admin URL. Reads each bench's `bench.toml` and probes liveness — no PID file is required.

---

## `bench stop`

Stops a running bench. `ProcessManagerFactory.detect_running()` picks the right manager (dev runner, systemd, or supervisor), then stops the workload and the admin service. Does **not** rely on `pids/bench.pid` — a dev bench with no PID file is stopped by killing the port-bound processes. Works across terminal sessions.

---

## `bench restart`

Restarts the production workload. **Production mode only** — requires `production.enabled = true` in `bench.toml` and a process-manager deployment generated by `bench setup production`.

In development mode it prints a notice; use `bench stop` followed by `bench start` instead.

### Steps

1. If `production.enabled` is false, print a dev-mode notice and return.
2. If the process-manager deployment is incomplete, print a repair hint (`bench setup production`) and return.
3. Regenerate the process-manager config, reload it, and restart all processes (works for both systemd and supervisor).

---

## `bench build`

Builds JavaScript and CSS assets for all installed apps.

```bash
bench build          # download pre-built assets from GitHub releases
bench build --force  # skip download, rebuild from source
```

### Pre-conditions

- `bench init` has been run (apps are cloned, Node.js is installed, virtualenv exists).

### Steps

```
1.  Try to download pre-built assets from the app's latest GitHub release
    (skipped if --force is passed, or if no release asset is found)
2.  For each app with a frontend/, install frontend JS dependencies
3.  Run bench frappe build --force
```

#### Step 1 — Pre-built asset download

`BuildCommand.run()` first attempts to download bundled frontend assets from the app's GitHub release for the current branch. If a matching release asset is found, it is extracted into `sites/assets/` and the build is skipped for that app. Pass `--force` to skip this step and always rebuild from source.

#### Step 2 — Frontend installs

`BuildCommand._install_frontend_deps()` walks `apps/` and runs `yarn install` in any `app/frontend/` directory that has a `package.json`. App-root JS dependencies are handled per-app during `bench get-app`.

#### Step 3 — Asset build

`bench frappe build --force` symlinks each app's `public/` into `sites/assets/`, runs esbuild for root-level JS, and executes each app's `build` script.

---

## `bench update`

Pulls the latest commits for all apps, reinstalls Python packages, rebuilds assets, and migrates all sites. Fails fast on the first error. When the bench sits on a ZFS volume, a snapshot is taken before any changes are made and the bench is automatically rolled back if anything goes wrong.

```
bench update [--yes] [--apps <app> ...]
```

| Flag | Description |
|------|-------------|
| `--yes` / `-y` | Skip the "processes are running" confirmation prompt. |
| `--apps <name> ...` | Limit git pull + reinstall to the named apps (default: all). |

### Steps

```
[pre]     Take a snapshot           (ZFS benches only)
[fetch]   Fetch latest code         git pull for each app
[install] Install dependencies      uv pip install -e for each app
[assets]  Build assets              bench build for each app
[migrate] Migrate sites             bench migrate for each site
[restart] Restart services          reload_web
[done]    Done
```

Each step emits a `##[step:KEY,TIMESTAMP] Label` marker that the admin UI uses to display a live progress timeline with per-step status and duration.

### ZFS snapshot and automatic rollback

When `volume.enabled = true` in `bench.toml`, the update command:

1. Enters maintenance mode before touching anything.
2. Takes a timestamped snapshot (`YYYYMMDD-HHMMSS`) covering both the bench dataset and the MariaDB dataset (via the snapshot orchestrator).
3. Runs the normal fetch → install → assets → migrate → restart sequence.
4. **On failure:** marks the failed step, prints the full traceback, then rolls back to the snapshot and reloads the web service so the bench returns to a known-good state.
5. Exits maintenance mode in the `finally` block whether or not the update succeeded.

#### Log preservation across rollback

Because the task directory lives inside the ZFS dataset, a pool revert would erase `output.log` along with everything else. To keep the complete log:

1. The current log is copied to `/tmp/bench-update-rollback-<tag>.log` before the rollback runs.
2. The rollback step's own output is appended to that `/tmp` file (via `redirect_stdout`/`redirect_stderr`).
3. After the revert, a fresh `output.log` is written from the `/tmp` copy and `sys.stdout`/`sys.stderr` are redirected there so subsequent steps (restart, maintenance-mode release) are also captured.

### Pre-conditions

- `bench init` has been run.
- All processes are stopped (a warning is printed if any Procfile processes are detected running; use `--yes` to skip the confirmation).

### Failure behaviour

- Fails fast on the first error in any step (app pull, install, asset build, or site migration).
- Raises `MigrateError`, which `UpdateTask` catches to set the exit code to 1.
- On ZFS benches the bench is rolled back before exiting; on non-ZFS benches the process exits immediately after printing the traceback.

---

## `bench upgrade`

Upgrades bench-cli itself to the latest version.

### Steps

```
1.  git pull in the bench-cli directory
2.  Download latest pre-built admin frontend assets
```

If the admin frontend download fails, prints a message suggesting `bench build-admin` to build from source.

---

## `bench setup config`

Regenerates all derived config files from `bench.toml` without running a full `bench init`. Use this after editing `bench.toml` to update ports, worker counts, or Redis settings.

**Files regenerated:**
- `config/redis.conf` (single-instance) or `config/redis_cache.conf`, `config/redis_queue.conf`, `config/redis_socketio.conf` (multi-instance)
- `config/Procfile`
- `sites/common_site_config.json`
- `config/nginx/*.conf` — only if `production.enabled = true`

**Does not:** restart processes, reload nginx, or touch apps/sites. Run `bench start` after to pick up process changes. Run `bench setup nginx` to reload nginx.

---

## `bench build-admin`

Rebuilds the admin UI frontend assets. Run this after pulling admin UI changes.

```bash
bench build-admin
```

The admin server starts automatically as part of `bench start` (via the `admin:` entry in the Procfile) and is always available at `http://localhost:8002` while the bench is running. This command only rebuilds the static assets — it does not start or stop the server.

See [docs/admin.md](admin.md) for the full interface specification.

---

## `bench setup nginx`

See [docs/production.md](production.md) for the full step-by-step.

**Summary:** Installs nginx if absent, generates per-site config files into `config/nginx/`, symlinks `include.conf` into `nginx.config_dir`, validates with `nginx -t`, and reloads nginx. Sites are discovered from the filesystem.

Each vhost serves minimal 404/502/503 pages (`config/nginx/error_pages/`) for nginx-generated errors. It also installs a server-wide catch-all `default_server` (`/etc/nginx/conf.d/00-bench-default.conf`, pages in `/usr/share/nginx/bench-error-pages/`) so requests for unknown hosts get a 404 instead of nginx's stock welcome page — this removes the distro's default site (`/etc/nginx/sites-enabled/default`). Re-running this command (or `bench setup production`) regenerates all of it.

Pre-conditions: `production.enabled = true` in `bench.toml`, `bench init` has been run, process has `sudo` (Ubuntu) or Homebrew (macOS).

> **macOS note:** This command works on macOS with Homebrew nginx for local testing, but its primary use case is production deployment on Ubuntu/Linux servers. The `config_dir` default (`/etc/nginx/conf.d`) does not exist on macOS — set it to `/opt/homebrew/etc/nginx/servers/` (Apple Silicon) or `/usr/local/etc/nginx/servers/` (Intel) in `bench.toml`.

---

## `bench setup letsencrypt`

See [docs/production.md](production.md) for the full step-by-step.

**Summary:** Installs certbot if absent, ensures the webroot directory exists, runs `certbot certonly --webroot` for each site with `ssl = true` in `site_config.json` (plus the admin domain, with all domains as `-d` arguments), then regenerates nginx config with HTTPS blocks and reloads nginx. A certbot failure for one domain is skipped rather than aborting the rest; if any failed, the command still attempts every domain before reporting which ones need a rerun.

**Requires `admin.tls = true`.** TLS is a server-wide opt-in: when `admin.tls` is `false` the bench is HTTP-only (a central proxy may terminate TLS upstream), so this command obtains no certificates and nginx emits no 443 blocks. Enable it via `bench setup production --tls`, the Settings → HTTPS toggle, or by setting `[admin] tls = true` in `bench.toml`.

Pre-conditions: `bench setup nginx` has run, nginx is serving port 80, DNS records for all SSL sites point to this server, and `letsencrypt.email` is set.

> **macOS note:** Let's Encrypt certificates require a publicly reachable server with real DNS records. This command is intended for Ubuntu/Linux production servers only.

---

## `bench setup production`

See [docs/production.md](production.md) for the full step-by-step. **Linux only.**

```bash
bench setup production
bench setup production --process-manager supervisord --admin-domain admin.example.com --tls
```

**Flags:**
- `--process-manager systemd|supervisord|openrc` — which process manager to deploy with (defaults to `production.process_manager` in `bench.toml`, or `systemd` — `openrc` on Alpine). Switching between them on a re-run migrates the existing deployment.
- `--admin-domain` — admin domain (defaults to `admin.domain` in `bench.toml`). Must match one of this bench's wildcard domains if any are configured (see [docs/production.md](production.md#custom-domain-management)).
- `--tls` — terminate TLS via Let's Encrypt for the admin and SSL-enabled sites. Omit to serve plain HTTP (a central proxy may terminate TLS upstream).

**Summary:** Writes `dns_multitenant: 1`, deploys the workload under the chosen process manager, enables and serves the admin behind its domain, runs `bench setup nginx`, issues certs when TLS is requested, then persists `production.enabled = true` and `production.process_manager` to `bench.toml`.

---

## `bench remove production`

Tears down a production deployment, returning the bench to development mode. Removes the process-manager units/programs and nginx site config, and sets `production.enabled = false`. **Keeps** logs, Let's Encrypt certificates, and the admin domain so the bench can be redeployed later.

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Configuration error or expected failure (printed to stderr) |
| `2` | Unexpected error (printed with traceback if `--verbose`) |

---

## Common flags

| Flag | Description |
|------|-------------|
| `-b/--bench NAME` | Specify which bench to operate on. Required when multiple benches exist. |
| `--verbose` | Print full tracebacks on error. |
| `--yes` | Skip confirmation prompts (useful in CI). |
