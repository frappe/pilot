# App Development

Develop a Frappe app the way most repositories work: clone it, set it up, start it.

```sh
git clone https://github.com/frappe/gameplan && cd gameplan
pilot dev setup     # bench, Frappe, site, app installed. Safe to run again.
pilot dev start     # foreground dev server. Ctrl-C stops everything.
```

The repository stays your own checkout. Pilot creates a bench for it in the benches directory and links the repository into that bench's `apps/`:

```text
~/Projects/gameplan/               your repository, your git
benches/dev-gameplan/              created by pilot dev setup
  bench.toml                       [[apps]] name = "gameplan", path = "~/Projects/gameplan"
  apps/frappe                      cloned by Pilot
  apps/gameplan  ->  ~/Projects/gameplan
  sites/dev-gameplan.localhost/    developer_mode on, Administrator / admin, no scheduled backups
```

## Setup

`pilot dev setup` finds the app from the current directory, so it works from any folder inside the repository.

- `--database sqlite|mariadb|postgres`: the database for the site. SQLite is the default because it needs no server. MariaDB and PostgreSQL use the host's shared server from `common_config.toml`.
- `--frappe-branch BRANCH`: the Frappe branch. The default is the checked-out branch when it is named `version-N`, otherwise `develop`.

Both are fixed once the bench exists. Run `pilot dev drop` to start over with different ones.

Setup records the bench's name in `pilot-bench` inside the repository's git directory. Pilot commands run inside the repository read it to find the bench. If the record is missing or names a bench that no longer links the repository, Pilot looks through every bench's `bench.toml` instead. `pilot dev drop` removes the record. When git cannot read the repository's git directory, setup writes no record.

Run `pilot dev setup` again after pulling. It installs new Python and JavaScript requirements, migrates the site, and builds the app's assets again. If an earlier run stopped before the app was installed on the site, it installs the app first.

If an earlier run stopped before the site was created, for example with Ctrl-C, setup resumes an unfinished initialization of the bench and then creates the site. Steps that already finished, such as the Frappe clone, are skipped. If the bench cannot be resumed, setup says so: run `pilot dev drop`, then `pilot dev setup`.

## Start

`pilot dev start` runs web, socketio, workers, Redis, and the asset watcher. The bench has the Admin disabled, so the Admin does not run. If the app's `package.json` has a `dev` script, it runs that too, with `FRAPPE_WEB_SERVER_PORT` set so frappe-ui's Vite proxy reaches this bench.

Both follow the bench, not the command, so `pilot start` inside the repository runs the same processes. `pilot dev start` also checks that setup finished and prints the site URL and login.

The repository's real path is outside the bench, so tools that look for the bench by walking up from the app cannot find it. Pilot sets `FRAPPE_BENCH_PATH` for asset builds and the `dev` script instead. Apps built with frappe-ui need a frappe-ui version that reads it. Older versions fail to build the frontend and cannot resolve imports such as `../../../../sites/common_site_config.json`.

The site is served at `http://dev-<folder>.localhost:<port>`. Browsers resolve every `*.localhost` name to this machine, and each checkout gets its own name, so logins in two checkouts do not overwrite each other's cookies.

## Worktrees

Each checkout gets its own bench, named after its folder. A second checkout of the same app in a folder with the same name gets `dev-gameplan-2`.

```sh
git worktree add ../gameplan-search search
cd ../gameplan-search && pilot dev setup && pilot dev start
```

## Other commands

See [Dev Commands](commands.md#dev-commands) for `pilot dev info`, `pilot dev drop`, and how other bench commands find this bench.

Pilot never pulls, switches branches, or checks out commits in a linked app. `pilot update` skips it, and `pilot remove-app` only removes the symlink in `apps/`. The next `pilot dev setup` links and installs the app again.
