# Commands

Commands are a user interface over the core object model. Keep command classes small: parse arguments, resolve a bench, and call a core object or task.

Use `bench --help` and `bench <command> --help` for exact flags.

## Bench Commands

- `bench new NAME`: create a new bench.
- `bench init`: initialize a bench from `bench.toml`.
- `bench ls`: list benches in the fixed benches directory.
- `bench drop --bench NAME`: remove a bench.

Bench commands with `--bench NAME` can run from outside the bench directory. `Bench("name")` resolves the same fixed benches directory in Python code.

## Runtime Commands

- `bench start`: start bench processes.
- `bench stop`: stop bench processes.
- `bench restart`: restart the production workload.
- `bench build`: build assets or download prebuilt assets when available.
- `bench update`: pull app code and migrate sites.
- `bench upgrade`: update bench-cli and admin frontend assets.
- `bench frappe -- ...`: pass through to Frappe's bench helper.

Some runtime commands support all benches when invoked with the CLI option for all-bench execution.

## App Commands

- `bench new-app APP`: scaffold a new Frappe app under `apps/` and install it. Prompts for title, description, publisher, email, license, GitHub workflow, and branch; pass any of `--title/--description/--publisher/--email/--license/--branch/--github-workflow` to skip prompts (branch defaults to `develop`).
- `bench get-app REPO_OR_NAME`: clone and install an app into the bench.
- `bench list-apps`: list apps present in the bench.
- `bench install-app APP --site SITE`: install apps on a site.
- `bench uninstall-app APP --site SITE`: uninstall apps from a site.
- `bench remove-app APP`: remove an app from the bench when no site needs it.

Long app operations should use task classes from `pilot.tasks`.

## Site Commands

- `bench new-site SITE`: create a site and add it to bench config.
- `bench rename-site OLD NEW`: rename a site.
- `bench list-site-apps SITE`: list apps installed on a site.
- `bench set-admin-password SITE`: update the site Administrator password.

Site behavior belongs on `Site` or a module under `pilot/core/site`.

## Setup Commands

- `bench setup requirements`: install Python and JS requirements.
- `bench setup config`: regenerate config files from `bench.toml`.
- `bench setup nginx`: render nginx config.
- `bench setup letsencrypt`: issue or refresh TLS certificates.
- `bench setup production`: deploy process manager and nginx integration.
- `bench remove production`: remove production deployment files and services.

Production setup uses the bench config and system managers. The command should not duplicate nginx, process manager, or certificate logic.

## Task Worker Commands

- `bench tasks status`: show Admin task worker state.
- `bench tasks start`: allow queued Admin tasks to run.
- `bench tasks stop`: drain the worker and leave queued tasks waiting.

These commands control the task worker, not individual Frappe workers.

## Admin Commands

- `bench build-admin`: build Admin frontend assets from source.
- `bench set-central-config`: store Central endpoint and Pilot auth token.
- `bench generate-admin-session`: create an Admin session token.
- `bench issue-site-token`: issue a scoped site-to-bench API token.

Admin commands live in `pilot/commands/admin`. Backend route behavior lives under `admin/backend/api/v1`.

## Adding A Command

1. Add a `Command` subclass under the closest command group. 2. Define `name`, `help`, and `group` when needed. 3. Keep argument definitions close to the command. 4. Delegate work to `Server`, `Bench`, `Site`, `App`, or a task class. 5. Add tests for argument handling and the delegated behavior.
