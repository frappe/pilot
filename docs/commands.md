# Commands

Commands are a user interface over the core object model. Keep command classes small: parse arguments, resolve a bench, and call a core object or task.

Use `pilot --help` and `pilot <command> --help` for exact flags. `pilot --version` prints the Pilot version (`dev` for a source checkout).

## Bench Commands

- `pilot new NAME`: create a new bench. Sets the Admin password from `--admin-password`, else prompts on a terminal, else generates and prints one.
- `pilot start` on an uninitialized bench serves the setup wizard and prints a one-hour `?sid=` sign-in link for it.
- `pilot init`: initialize a bench from `bench.toml`. This is what the setup wizard runs. `--no-dev` skips apps' `dev` extras. `--commit <sha>` checks out the framework app at that commit and keeps it on the branch that `bench.toml` names, so updates and prebuilt assets still find the branch. When an app's `dev` extra does not install, for example because its dependencies conflict, Pilot warns and installs the app without it. Init enables Pilot's MariaDB or PostgreSQL user unit at boot on every run, so a retry after a failed run is safe. It also builds the framework app's assets, or downloads its prebuilt archive, so `get-app` can come before the first `new-site`.
- `pilot ls`: list benches in the fixed benches directory.
- `pilot drop --bench NAME`: remove a bench. The bench must have no sites. A development bench must be stopped first (`pilot -b NAME stop`), including a setup wizard that is still open.

Bench commands with `--bench NAME` can run from outside the bench directory. `Bench("name")` resolves the same fixed benches directory in Python code.

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
- `pilot install-app SITE APP [APP ...]`: install apps on a site. An app the site only has disabled is enabled instead, bringing back anything it requires first.
- `pilot uninstall-app SITE APP [APP ...]`: uninstall apps from a site, dropping their data. An app that no site uses afterwards is also removed from the bench; `--keep-on-bench` keeps it. Use `pilot remove-app` to remove an app from the bench.
- `pilot remove-app APP`: remove an app from the bench when no site needs it.

Long app operations should use task classes from `pilot.tasks`.

### Disabled Apps

A disabled app keeps its schema and records on the site but stops taking effect, and Pilot reports it as uninstalled: `list-site-apps` and the Admin API leave it out. Disable state is read from Frappe's `disabled_apps` global rather than the `Installed Application` mirror column, which can drift.

Disabling needs a Frappe that supports it and is exposed through the Admin UI only. The CLI can bring a disabled app back, through `install-app`, but not take one out of use.

## Site Commands

- `pilot new-site SITE`: create a site and add it to bench config.
- `pilot drop-site SITE [--no-backup] [--force] [--archived-sites-path PATH]`: drop a site. Frappe takes a backup first unless you pass `--no-backup`. Pilot also removes the site from `bench.toml`, nginx, its domains and the backup schedule. `pilot --site SITE drop-site SITE` and `pilot frappe drop-site SITE` run the same drop and accept Frappe's options. Pilot always uses the bench's own database credentials, so it does not use the root user and password options.
- `pilot rename-site OLD NEW [--release-old-hostname]`: rename a site, without dropping a request.
- `pilot list-site-apps SITE`: list the apps in use on a site, disabled ones excluded. An app the site lists but the bench does not have gets a warning on stderr; `pilot uninstall-app SITE APP` then removes only the site's listing.
- `pilot set-admin-password`: set the Admin panel password in `bench.toml`; prompts when `--password` is omitted. The password must meet the same rules the dashboard enforces.
- `pilot set-admin-domain DOMAIN [--tls]`: move the Admin panel to another hostname, reissuing its certificate and republishing nginx. The old hostname is released once the switch has committed.

### Renaming Without Downtime

A rename never drops traffic already on the site: requests keep arriving on the old hostname and keep being answered throughout. The new hostname follows a moment later - `systemctl reload` returns before nginx has taken the signal, and the workers still running answer until it has - so a caller that redirects straight to the new URL should expect to retry once. Two things would otherwise break in the gap, and both are handled:

- The site directory moves while nginx is still sending the old name in `X-Frappe-Site-Name`. The move leaves the old path behind as a symlink until nginx has reloaded, then removes it, so that header always resolves. Both steps are `rename(2)`, so the old path is absent only between two consecutive syscalls.
- The old hostname would stop being served. It stays on the site as a domain instead, so anyone already on that URL is served rather than dropped. Pass `--release-old-hostname` to give the name up - a pooled hostname a fleet reuses. The domain provider is asked to route the new hostname before anything moves, and a released one is handed back only once the switch has committed.
- The site's long-lived Admin API token names the site. The rename replaces it with a token scoped to the new name, so the in-app Cloud Settings embed keeps working without letting the old token follow a hostname that is later assigned to another site.

The site keeps whatever redirect policy it had: a canonical `host_name` naming the old site moves with it, and one naming another domain is left alone. A rename never makes the renamed site canonical, which would start redirecting the site's other custom domains to it.

HTTPS survives the move as well. A certbot lineage is named after the site it was first issued for and lives in the host-wide `/etc/letsencrypt`, so a rename would otherwise orphan the certificate: nginx would look under the new name, find nothing, and drop every one of the site's TLS domains to HTTP until a fresh one was issued. The rename pins the site's `cert_name` to the existing lineage instead, so the certificate keeps serving the hostnames it already covers, and the reissue expands that same lineage to the new name. A pinned lineage claims its hostname exactly as a domain does - no site on any bench, and not the admin, can take it while the pin stands - so releasing a hostname whose certificate still serves the site's other TLS domains completes in two steps: the rename keeps the pin so those domains stay on HTTPS, and the next `pilot setup letsencrypt` issues under the new name, drops the pin, and frees the old one. `cert_name` is protected from the site configuration API; only a rename writes it.

A rename that fails before nginx has switched is rolled back: the site directory, its config, `common_site_config.json`, `bench.toml` and the hostname aliases are put back under the old name, and the new provider route is released. nginx was still naming the old site throughout, so it keeps serving, and the rename can simply be run again. Each rollback step is reported if it cannot be completed rather than hiding the original failure.

Only the certificate is refreshed afterwards, never the full production deploy - that restarts the workload, which is the downtime this is avoiding. A domain the certificate does not yet name is served over HTTP until it does, rather than taking the rest of the site down with it.

Site behavior belongs on `Site` or a module under `pilot/core/site`.

## Setup Commands

- `pilot setup requirements`: install Python and JS requirements.
- `pilot setup config`: regenerate config files from `bench.toml`.
- `pilot setup nginx`: render nginx config.
- `pilot setup letsencrypt`: issue or refresh TLS certificates.
- `pilot setup production`: deploy process manager and nginx integration.
- `pilot setup central`: hand the host to Central and alias the VM hostnames it serves.
- `pilot setup telemetry`: write the metrics credential to `common_config.toml` and install Fluent Bit.
- `pilot remove production`: remove production deployment files and services.
- `pilot remove logs`: stop and disable the Fluent Bit log shipper.

`pilot setup central` writes the shared `[central]` settings: it enables Central management and, given `--admin-pattern` or `--site-pattern`, aliases those VM hostname globs to this bench's admin domain and its site. Central manages a host holding one bench, so the command refuses a second one rather than guess which bench a VM hostname belongs to, and `--site-pattern` aliases the bench's only site, skipping the alias while the bench has no site and refusing to guess when it has more than one. The credential itself is never passed in - it arrives through instance metadata, and `--rebootstrap` asks for it to be applied again. Because the bootstrap unit is written only while Central is enabled, the command rebuilds the process set of a bench already in production, restarting its workload.

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
- `pilot admin upgrade`: update Pilot to the latest version, run pending upgrade patches (pre_update before, post_update after), and restart the admin service.
- `pilot admin issue-site-token`: issue a scoped site-to-bench API token.
- `pilot admin revoke-totp --device-name NAME`: revoke a device's TOTP credential, for a lost authenticator.
- `pilot admin run-patches [--phase pre_update|post_update|all]`: run pending Pilot upgrade patches by hand (see [Configuration](configuration.md#common-config)); `pilot admin upgrade` already runs both phases automatically.

Admin commands live in `pilot/commands/admin`. Backend route behavior lives under `admin/backend/api/v1`.

## Adding A Command

1. Add a `Command` subclass under the closest command group. 2. Define `name`, `help`, and `group` when needed. 3. Keep argument definitions close to the command. 4. Delegate work to `Server`, `Bench`, `Site`, `App`, or a task class. 5. Add tests for argument handling and the delegated behavior.
