# Commands

Commands are a user interface over the core object model. Keep command classes small: parse arguments, resolve a bench, and call a core object or task.

Use `pilot --help` and `pilot <command> --help` for exact flags. `pilot --version` prints the Pilot version (`dev` for a source checkout).

## Bench Commands

- `pilot clone-bench TARGET`: infer the source bench and create an independent development bench without sites, using each app's origin default branch. Override with `--branch current` to include local changes, `--branch NAME` for a clean named branch, or `--app-branches frappe=develop,my_app=feature/example` per app. Use `-b SOURCE` from elsewhere.
- `pilot clone-site SOURCE_SITE TARGET_SITE [--target-bench NAME]`: stream a fresh database copy and copy uploads into a new site in the current or selected bench. Scheduler and outgoing mail start disabled.

See [Bench and site cloning](cloning.md) for independent environments and cross-bench data copies.

- `pilot new NAME`: create a new bench. `--frappe-branch BRANCH` selects the Frappe branch saved in `bench.toml` (default: `version-16`). Sets the Admin password from `--admin-password`, else prompts on a terminal, else generates and prints one.
- `pilot start` on an uninitialized bench serves the setup wizard and prints a one-hour `?sid=` sign-in link for it.
- An interrupted first initialization stays in setup mode. Retry from the wizard or run `pilot init`. Failed initialization preserves directories that existed before the attempt.
- `pilot init`: initialize a bench from `bench.toml`. This is what the setup wizard runs. `--no-dev` skips apps' `dev` extras. `--skip-frontend` is for test jobs that never serve web pages: it skips building assets and only downloads prebuilt ones when published. Refused on production benches. `--country NAME` picks the bench's country (see [Country](configuration.md#country)); it is fixed once the bench is initialised. `--commit <sha>` checks out the framework app at that commit and keeps it on the branch that `bench.toml` names, so updates and prebuilt assets still find the branch. When an app's `dev` extra does not install, for example because its dependencies conflict, Pilot warns and installs the app without it. Init enables Pilot's MariaDB or PostgreSQL user unit at boot on every run, so a retry after a failed run is safe. It also builds the framework app's assets, or downloads its prebuilt archive, so `get-app` can come before the first `new-site`.
- `pilot ls`: list benches in the fixed benches directory.
- `pilot set-bench-config KEY VALUE`: set a `bench.toml` value by key (booleans, numbers, or strings). Regenerates runtime configs on an initialized bench; a fresh bench picks the value up at `pilot init`. `pilot set-bench-config production_sandbox true|false|default` turns the [sandbox](sandbox.md) on or off, or clears the setting; `pilot restart` applies it.
- `pilot drop --bench NAME`: remove a bench. The bench must have no sites. A development bench must be stopped first (`pilot -b NAME stop`), including a setup wizard that is still open.

To create and initialize a bench tracking Frappe `develop`:

```bash
pilot new frappe-bench --frappe-branch develop
pilot -b frappe-bench init
```

Bench commands with `--bench NAME` can run from outside the bench directory. `Bench("name")` resolves the same fixed benches directory in Python code.

## Dev Commands

Run these inside a cloned app repository. See [App Development](development.md).

- `pilot dev setup`: create a bench for the app and a site with the app installed. Safe to run again. See [Setup](development.md#setup) for the options, their defaults, and what a re-run does.
- `pilot dev start`: run the bench in the foreground, with the app's own `dev` script if `package.json` has one. It runs the same processes as `pilot start`, after checking that setup finished.
- `pilot dev info`: show the bench, site URL, database, and Frappe branch.
- `pilot dev drop`: delete the bench and its site. The repository is not touched. It fails while `pilot dev start` runs.

Inside the repository, other bench commands such as `pilot frappe -- console` use its bench without `-b`.

## Runtime Commands

- `pilot start`: start bench processes. On a development bench, it stays in the foreground and its children receive no standard input, so they cannot consume a launcher's piped script. Use a service manager for unattended development operation.
- `pilot stop`: stop bench processes.
- `pilot restart`: restart the production workload. It exits with an error on a development bench or an incomplete production deployment, because nothing was restarted.
- `pilot build`: build assets, or download [prebuilt assets](prebuilt-assets.md) for the checked-out commit when the app publishes them. The queued build task (`pilot.tasks.build.BuildTask`) always forces a full rebuild, since a queued/CLI-triggered build is expected to reflect current source rather than reuse a prebuilt bundle.
- `pilot -b NAME frappe --site SITE COMMAND`: pass through to Frappe's bench helper without a `--` separator. For example: `pilot -b dev frappe --site test.local migrate`. Use `pilot frappe --help` for this syntax, or append `--help` to a Frappe command for its options.

Some runtime commands support all benches when invoked with the CLI option for all-bench execution.

When a classic development bench has multiple sites, `pilot start` warns if `FRAPPE_SITE` or `default_site` fixes routing to one site. It keeps the setting and starts the bench.
For public development URLs, see [Development sites behind a proxy](development-proxies.md).

## App Commands

- `pilot new-app APP`: scaffold a new Frappe app under `apps/` and install it. Prompts for title, description, publisher, email, license, GitHub workflow, and branch; pass any of `--title/--description/--publisher/--email/--license/--branch/--github-workflow` to skip prompts (branch defaults to `develop`).
- `pilot get-app REPO_OR_NAME`: clone and install an app into the bench. `--skip-assets` skips asset setup for the app and its missing dependencies. `--commit SHA` installs that commit and keeps `--branch` checked out, so Pilot can download the [prebuilt assets](prebuilt-assets.md) of that branch. Example: `pilot get-app https://github.com/frappe/crm --branch main --commit <sha>`.
- `pilot list-apps`: list apps present in the bench.
- `pilot git APP ARGS...`: run git in an app's checkout with the bench's stored Git credential, for scripts that fetch private repos. The token stays in the environment, out of argv and `.git/config`. Exits with git's exit code. Example: `pilot -b prod git frappe fetch origin`. From Python, use `bench.app("frappe").run_git("fetch", "origin")`.
- `pilot install-app SITE APP [APP ...]`: install apps on a site. An app the site only has disabled is enabled instead, bringing back anything it requires first.
- `pilot uninstall-app SITE APP [APP ...]`: uninstall apps from a site, dropping their data. An app that no site uses afterwards is also removed from the bench; `--keep-on-bench` keeps it. Use `pilot remove-app` to remove an app from the bench.
- `pilot remove-app APP`: remove an app from the bench when no site needs it.

Long app operations should use task classes from `pilot.tasks`.

### Disabled Apps

A disabled app keeps its schema and records on the site but stops taking effect, and Pilot reports it as uninstalled: `list-site-apps` and the Admin API leave it out. Disable state is read from Frappe's `disabled_apps` global rather than the `Installed Application` mirror column, which can drift.

Disabling needs a Frappe that supports it and is exposed through the Admin UI only. The CLI can bring a disabled app back, through `install-app`, but not take one out of use.

## Site Commands

- `pilot new-site SITE`: create a site and add it to bench config. `--skip-assets` is for test jobs that never serve web pages: it skips building missing app assets. Refused on production benches.
- `pilot -b NAME drop-site SITE [--no-backup] [--force] [--archived-sites-path PATH]`: drop a site. It asks for confirmation unless you pass `pilot --yes`, needs `-b` or a bench working directory, and refuses while a task uses the site. Frappe takes a backup first unless you pass `--no-backup`. Pilot also removes the site from `bench.toml`, nginx, its domains and the backup schedule. `pilot --site SITE drop-site SITE` and `pilot frappe drop-site SITE` run the same drop and accept Frappe's options. Pilot always uses the bench's own database credentials, so it does not use the root user and password options.
- `pilot rename-site OLD NEW [--release-old-hostname]`: rename a site, without dropping a request.
- `pilot list-site-apps SITE`: list the apps in use on a site, disabled ones excluded. An app the site lists but the bench does not have gets a warning on stderr; `pilot uninstall-app SITE APP` then removes only the site's listing.
- `pilot set-admin-password`: set the Admin panel password in `bench.toml`; prompts when `--password` is omitted. The password must meet the same rules the dashboard enforces.
- `pilot set-admin-domain DOMAIN [--tls]`: move the Admin panel to another hostname, reissuing its certificate and republishing nginx. The old hostname is released once the switch has committed.

### Renaming Without Downtime

A rename keeps the old path available while publishing the new name. The new hostname follows a moment later: nginx reload returns before its workers have switched, so a caller that redirects immediately to the new URL may need to retry. Pilot protects the directory, hostname, and Admin token during this transition:

- The directory and a compatibility symlink exchange places atomically. The old path remains a symlink after nginx reload returns, so old workers and queued jobs can still read the site. Site inventory excludes this link. Pending renames retain their old path and reserve both names until recovery completes. After completion, the old name stays reserved for at least 150 seconds. Workload restart keeps the link while the old hostname belongs to a site. Explicit name reuse can remove it after the hold. If the old hostname is released, a workload restart can remove its expired link. Older compatibility links point directly to the latest directory instead of forming chains. Other symlinks stay. The filesystem must support atomic directory exchange, or the rename fails before moving the site.
- The old hostname would stop being served. It stays on the site as a domain instead, so anyone already on that URL is served rather than dropped. Pass `--release-old-hostname` to give the name up - a pooled hostname a fleet reuses. The domain provider is asked to route the new hostname before anything moves, and a released one is handed back only once the switch has committed.
- Stored files on S3 (Frappe storage v2) keep keys under the old name. After the rename, Pilot queues a `relocate-renamed-blobs` task in which Frappe copies them to the new name's prefix inside the same bucket and repoints them, so new uploads and old files share one prefix. The task runs outside the rename's timeout and lock; if it fails, retry the task. The old objects stay, so backups taken before the rename still restore. Sites on other storage, and benches whose Frappe cannot relocate yet, skip this step.
- The site's long-lived Admin API token names the site. The rename replaces it with a token scoped to the new name, so the in-app Cloud Settings embed keeps working without letting the old token follow a hostname that is later assigned to another site.

The site keeps whatever redirect policy it had: a canonical `host_name` naming the old site moves with it, and one naming another domain is left alone. A rename never makes the renamed site canonical, which would start redirecting the site's other custom domains to it.

HTTPS survives the move as well. A certbot lineage is named after the site it was first issued for and lives in the host-wide `/etc/letsencrypt`, so a rename would otherwise orphan the certificate: nginx would look under the new name, find nothing, and drop every one of the site's TLS domains to HTTP until a fresh one was issued. The rename pins the site's `cert_name` to the existing lineage instead, so the certificate keeps serving the hostnames it already covers, and the reissue expands that same lineage to the new name. A pinned lineage claims its hostname exactly as a domain does - no site on any bench, and not the admin, can take it while the pin stands - so releasing a hostname whose certificate still serves the site's other TLS domains completes in two steps: the rename keeps the pin so those domains stay on HTTPS, and the next `pilot setup letsencrypt` issues under the new name, drops the pin, and frees the old one. `cert_name` is protected from the site configuration API; only a rename writes it.

Pilot saves private recovery state under `config/site-renames/` before moving the directory. Once the directory moves, failure never reverses that exchange. The old symlink and new route stay available. Retry the failed task, or repeat the same CLI rename with the same options, to finish the saved operation. Retry reuses the new Admin token and revokes the original token only after publishing nginx. Another rename cannot move the destination while this operation is pending. A process crash leaves the recovery state and compatibility link intact; recovery requires a retry and does not run automatically. Recovery files contain the original site config and have private directory and file permissions.

Only the certificate is refreshed afterwards, never the full production deploy - that restarts the workload, which is the downtime this is avoiding. A domain the certificate does not yet name is served over HTTP until it does, rather than taking the rest of the site down with it.

Site behavior belongs on `Site` or a module under `pilot/core/site`.

## Setup Commands

- `pilot setup requirements`: install Python and JS requirements.
- `pilot setup config`: regenerate config files from `bench.toml`.
- `pilot setup nginx`: render nginx config.
- `pilot setup letsencrypt`: issue or refresh TLS certificates.
- `pilot setup production`: deploy process manager and nginx integration.
- `pilot setup central`: hand the host to Central and alias the VM hostnames it serves.
- `pilot setup telemetry`: write the Datum credential to `common_config.toml`.
- `pilot setup database-server <client_address>`: make this VM the dedicated MariaDB server of one app VM.
- `pilot setup remote-database <host> [--migrate] [--delete-local-data]`: move every bench of the host to the MariaDB on a database VM and remove the local one. `--migrate` copies the databases of existing sites first.
- `pilot remove production`: remove production deployment files and services.
- `pilot remove central`: turn Central management off and remove its VM hostname aliases.

`pilot setup central` writes the shared `[central]` settings: it enables Central management and, given `--admin-pattern` or `--site-pattern`, aliases those VM hostname globs to this bench's admin domain and its site. Central manages a host holding one bench, so the command refuses a second one rather than guess which bench a VM hostname belongs to, and `--site-pattern` aliases the bench's only site, skipping the alias while the bench has no site and refusing to guess when it has more than one. The credential itself is never passed in - it arrives through instance metadata, and `--rebootstrap` asks for it to be applied again. Because the bootstrap unit is written only while Central is enabled, the command rebuilds the process set of a bench already in production, restarting its workload.

`pilot setup database-server` runs on a VM without benches. It stops the package's `mariadb.service`, sets up Pilot's MariaDB user unit for the whole VM on `--port` (default 3306), and starts the database agent on `--agent-port` (default 7781) for `client_address`, the app VM's address. It prints the agent's token once and keeps only its SHA-256. A second run keeps the data and passwords and replaces the token.

`pilot setup remote-database` reads that token from the terminal, or from standard input when it is not a terminal, so the token stays off the command line. It creates an admin account for this host through the agent, saves the server in the shared `[mariadb]` table with a new admin password, rewrites each bench's `common_site_config.json` and production units, and then stops and deletes Pilot's local server and its data. It refuses before any change when the database VM is not set up or runs MariaDB older than 11.8, when the host already uses a remote server, or, without `--migrate`, when a bench of the host has sites, because their databases are on the local server. `--port` (default 3306), `--agent-port` (default 7781) and `--admin-user` (default `pilot`) set the remote server's port, the agent's port and the admin account Pilot creates. See [Remote MariaDB](configuration.md#remote-mariadb).

`pilot setup remote-database --migrate` moves a host that has sites. Before any change it refuses when `pilot start` runs a bench, when a bench has queued or running tasks, when Pilot cannot log in to the local server, when a site's `site_config.json` cannot be read or names another server, when the remote server runs an older MariaDB than the local one, when a site database has a view or routine with `SQL SECURITY DEFINER`, a trigger or an event, because its definer is an account of the local server, and when the database VM's free disk is less than 1.5 times the data and index length of the site databases plus 1 GiB. After the takeover it refuses when the remote server already has a database or an account with the name of a site database or login. Then it stops each running production workload, and for each site creates the database and the site's login from `site_config.json` at the host the admin connects from, pipes `mariadb-dump --single-transaction --routines --triggers --events` of the local server into the remote server with no file in between, and compares the tables, views, sequences, routines, triggers and events of both. Only after every copy matches it saves the remote server, sets each copied site's `db_host` and `db_port` to it, rewrites each bench's `common_site_config.json` and production units, and starts the workloads. A failure before that drops only the databases and logins this run created, starts the workloads on the local server and leaves every config file as it was, so a second run starts clean. The local server's units are removed, and its data stays in `databases/mariadb` so the host can go back to it. `--delete-local-data` deletes it after the copy. Sites on SQLite keep their database files. `managed.cnf` is not copied, so variable changes made on the local server must be made again on the remote server.

`pilot remove central` clears the shared `[central]` table, including its hostname aliases, so the Admin stops waiting for a Central credential. Use it on a host that is not a Central VM. On a bench in production it rebuilds the process set, which removes the bootstrap unit and regenerates nginx.

Production setup uses the bench config and system managers. The command should not duplicate nginx, process manager, or certificate logic.

## Process Commands

- `pilot processes ls [--app APP]`: list the bench and app processes with their state, pid and command. `ls` is also `list`. With `--app`, list only that app's declared processes, which also checks its `pyproject.toml` before any site uses it.
- `pilot processes restart NAME...`: restart processes on a production bench. A name is a process, such as `web` or `mail-stalwart`, or an app, which stands for every process of that app.
- `pilot processes disable NAME...`: stop app processes and keep them off. Pilot saves them in `[processes] disabled` in `bench.toml`.
- `pilot processes enable NAME...`: turn disabled app processes back on. They run while a site uses their app.

## Task Worker Commands

- `pilot tasks status`: show Admin task worker state.
- `pilot tasks start`: allow queued Admin tasks to run.
- `pilot tasks stop`: drain the worker and leave queued tasks waiting.

These commands control the task worker, not individual Frappe workers.

## Admin Commands

- `pilot admin build`: rebuild Admin frontend assets from source.
- `pilot admin api-reference`: write the [Admin API reference](admin-api-reference.md) from the routes.
- `pilot admin upgrade`: update Pilot to the latest version, run pending upgrade patches (pre_update before, post_update after), and restart the admin service.
- `pilot admin auto-update on|off|status`: turn daily automatic Pilot updates on or off for the host, or show the setting (see [Configuration](configuration.md#automatic-updates)).
- `pilot admin issue-site-token`: issue a scoped site-to-bench API token.
- `pilot admin revoke-totp --device-name NAME`: revoke a device's TOTP credential, for a lost authenticator.
- `pilot admin run-patches [--phase pre_update|post_update|all]`: run pending Pilot upgrade patches by hand (see [Configuration](configuration.md#common-config)); `pilot admin upgrade` already runs both phases automatically.

Admin commands live in `pilot/commands/admin`. Backend route behavior lives under `admin/backend/api/v1`.

## Adding A Command

1. Add a `Command` subclass under the closest command group. 2. Define `name`, `help`, and `group` when needed. 3. Keep argument definitions close to the command. 4. Delegate work to `Server`, `Bench`, `Site`, `App`, or a task class. 5. Add tests for argument handling and the delegated behavior.
