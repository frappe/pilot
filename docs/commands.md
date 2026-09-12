# Commands

Commands are a user interface over the core object model. Keep command classes small: parse arguments, resolve a bench, and call a core object or task.

Use `pilot --help` and `pilot <command> --help` for exact flags.

## Bench Commands

- `pilot new NAME`: create a new bench. Sets the Admin password from `--admin-password`, else prompts on a terminal, else generates and prints one.
- `pilot start` on an uninitialized bench serves the setup wizard and prints a one-hour `?sid=` sign-in link for it.
- `pilot init`: initialize a bench from `bench.toml`. This is what the setup wizard runs.
- `pilot ls`: list benches in the fixed benches directory.
- `pilot drop --bench NAME`: remove a bench.

Bench commands with `--bench NAME` can run from outside the bench directory. `Bench("name")` resolves the same fixed benches directory in Python code.

## Runtime Commands

- `pilot start`: start bench processes.
- `pilot stop`: stop bench processes.
- `pilot restart`: restart the production workload.
- `pilot build`: build assets or download prebuilt assets when available.
- `pilot frappe -- ...`: pass through to Frappe's bench helper.

Some runtime commands support all benches when invoked with the CLI option for all-bench execution.

## App Commands

- `pilot new-app APP`: scaffold a new Frappe app under `apps/` and install it. Prompts for title, description, publisher, email, license, GitHub workflow, and branch; pass any of `--title/--description/--publisher/--email/--license/--branch/--github-workflow` to skip prompts (branch defaults to `develop`).
- `pilot get-app REPO_OR_NAME [--branch BRANCH_OR_COMMIT]`: clone and install an app into the bench. Without `--branch`, a fresh clone uses the remote default branch and an existing install is reused at its current revision. An explicit branch or commit is a revision constraint, so a mismatch with an existing checkout fails instead of reusing it.
- `pilot list-apps`: list apps present in the bench.
- `pilot install-app APP --site SITE`: install apps on a site. An app the site only has disabled is enabled instead, bringing back anything it requires first.
- `pilot uninstall-app APP --site SITE`: uninstall apps from a site, dropping their data.
- `pilot remove-app APP`: remove an app from the bench when no site needs it.

Long app operations should use task classes from `pilot.tasks`.

### Disabled Apps

A disabled app keeps its schema and records on the site but stops taking effect, and Pilot reports it as uninstalled: `list-site-apps` and the Admin API leave it out. Disable state is read from Frappe's `disabled_apps` global rather than the `Installed Application` mirror column, which can drift.

Disabling needs a Frappe that supports it and is exposed through the Admin UI only. The CLI can bring a disabled app back, through `install-app`, but not take one out of use.

## Site Commands

- `pilot new-site SITE`: create a site and add it to bench config.
- `pilot rename-site OLD NEW [--release-old-hostname]`: rename a site, without dropping a request.
- `pilot list-site-apps SITE`: list the apps in use on a site, disabled ones excluded.
- `pilot set-admin-password`: set the Admin panel password in `bench.toml`; prompts when `--password` is omitted. The password must meet the same rules the dashboard enforces.
- `pilot set-admin-domain DOMAIN [--tls]`: move the Admin panel to another hostname, reissuing its certificate and republishing nginx. The old hostname is released once the switch has committed.

### Renaming Without Downtime

A rename never drops traffic already on the site: requests keep arriving on the old hostname and keep being answered throughout. The new hostname follows a moment later - `systemctl reload` returns before nginx has taken the signal, and the workers still running answer until it has - so a caller that redirects straight to the new URL should expect to retry once. Two things would otherwise break in the gap, and both are handled:

- The site directory moves while nginx is still sending the old name in `X-Frappe-Site-Name`. The move leaves the old path behind as a symlink until nginx has reloaded, then removes it, so that header always resolves. Both steps are `rename(2)`, so the old path is absent only between two consecutive syscalls.
- The old hostname would stop being served. It stays on the site as a domain instead, so anyone already on that URL is served rather than dropped. Pass `--release-old-hostname` to give the name up - a pooled hostname a fleet reuses. The domain provider is asked to route the new hostname before anything moves, and a released one is handed back only once the switch has committed.

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
- `pilot remove production`: remove production deployment files and services.

Production setup uses the bench config and system managers. The command should not duplicate nginx, process manager, or certificate logic.

## Task Worker Commands

- `pilot tasks status`: show Admin task worker state.
- `pilot tasks start`: allow queued Admin tasks to run.
- `pilot tasks stop`: drain the worker and leave queued tasks waiting.

These commands control the task worker, not individual Frappe workers.

## Admin Commands

- `pilot admin build`: rebuild Admin frontend assets from source.
- `pilot admin upgrade`: update Pilot to the latest version, run pending upgrade patches (pre_update before, post_update after), and restart the admin service.
- `pilot admin issue-site-token`: issue a scoped site-to-bench API token.
- `pilot admin run-patches [--phase pre_update|post_update|all]`: run pending Pilot upgrade patches by hand (see [Configuration](configuration.md#common-config)); `pilot admin upgrade` already runs both phases automatically.

Admin commands live in `pilot/commands/admin`. Backend route behavior lives under `admin/backend/api/v1`.

## Adding A Command

1. Add a `Command` subclass under the closest command group. 2. Define `name`, `help`, and `group` when needed. 3. Keep argument definitions close to the command. 4. Delegate work to `Server`, `Bench`, `Site`, `App`, or a task class. 5. Add tests for argument handling and the delegated behavior.
