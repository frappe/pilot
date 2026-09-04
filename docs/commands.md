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
- `pilot get-app REPO_OR_NAME`: clone and install an app into the bench.
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
- `pilot rename-site OLD NEW`: rename a site.
- `pilot list-site-apps SITE`: list the apps in use on a site, disabled ones excluded.
- `pilot set-admin-password`: set the Admin panel password in `bench.toml`; prompts when `--password` is omitted. The password must meet the same rules the dashboard enforces.

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
- `pilot admin upgrade`: update Pilot to the latest version, run pending upgrade patches (pre_update before, post_update after), and restart the admin service. A source checkout on a branch pulls that branch. A source checkout pinned to a tag checks out the newest release tag. An already-current tag skips dependency installation and the frontend build.
- `pilot admin enroll`: exchange the bootstrap token for this bench's Central credential.
- `pilot admin set-central-config`: store Central endpoint and Pilot auth token.
- `pilot admin issue-site-token`: issue a scoped site-to-bench API token.
- `pilot admin run-patches [--phase pre_update|post_update|all]`: run pending Pilot upgrade patches by hand (see [Configuration](configuration.md#common-config)); `pilot admin upgrade` already runs both phases automatically.

Admin commands live in `pilot/commands/admin`. Backend route behavior lives under `admin/backend/api/v1`.

## Adding A Command

1. Add a `Command` subclass under the closest command group. 2. Define `name`, `help`, and `group` when needed. 3. Keep argument definitions close to the command. 4. Delegate work to `Server`, `Bench`, `Site`, `App`, or a task class. 5. Add tests for argument handling and the delegated behavior.
