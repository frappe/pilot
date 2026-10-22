# App Processes

An app can declare processes in its own `pyproject.toml`. The bench runs them next to web, workers and socketio while a site uses the app, for example a mail server for the Mail app.

```toml
[tool.pilot.processes.stalwart]
command = ["./bin/stalwart", "--config", "config.toml"]
pre_run = ["./scripts/install_stalwart.sh"]
post_run = ["rm", "-f", "stalwart.sock"]
working_dir = "server"
env = { PORT = 8080 }
restart_on_failure = true
stop_timeout = 30

[tool.pilot.processes.server]
frappe_command = ["mail", "serve"]
```

A process runs as `<app>-<name>`, here `mail-stalwart`, and logs to `logs/mail-stalwart.log`.

## Fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `command` | list of strings | one of the two | Executable and arguments. Never a shell string. |
| `frappe_command` | list of strings | one of the two | A Frappe CLI command, used instead of `command`. |
| `pre_run` | list of strings | none | Runs before each start. |
| `post_run` | list of strings | none | Runs after each stop, and after a failed `pre_run`. |
| `working_dir` | string | app directory | Directory a `command` runs in. |
| `env` | table of strings or numbers | empty | Environment variables, over the defaults below. |
| `restart_on_failure` | bool | `true` | Restart after a non-zero exit. |
| `stop_timeout` | int | manager default | Seconds to wait for a graceful stop. |

The table key must match `^[a-z0-9][a-z0-9_]{0,31}$`, with no hyphen so `<app>-<name>` stays unique, and env keys must match `^[A-Z_][A-Z0-9_]*$`. Values must not contain control characters.

## Environment

Every app process gets the bench venv first on `PATH`, so `python` and the commands the app installs resolve to the bench's. It also gets `VIRTUAL_ENV`, `FRAPPE_BENCH_ROOT` and `PYTHONUNBUFFERED=1`. A key in `env` replaces the default.

## Paths

A `command` runs in the app directory, `apps/<app>`. A relative `working_dir` is read from there. The first entry of `command`, `pre_run` and `post_run` resolves from the working directory when it contains a slash (`./stalwart`, `bin/stalwart`). An absolute path is used as given, and a bare name (`flow`) is looked up on `PATH`. Paths are not checked on disk, so `pre_run` can download the executable.

## Frappe commands

`frappe_command` runs a command the app adds to the Frappe CLI, the same as `bench <command>`. Frappe finds the sites from its working directory, so it runs in `sites` and takes no `working_dir`.

## Hooks

`pre_run` runs on every start, so keep it idempotent. A shell line needs an explicit shell: `pre_run = ["bash", "-c", "test -x ./stalwart || ./scripts/install.sh"]`. On systemd, `pre_run` counts toward the 90 second start timeout.

systemd runs the hooks as `ExecStartPre` and `ExecStopPost`. Supervisor and the dev runner run a process with hooks inside `sh`, which runs `post_run` after the process exits and keeps the exit code of the process, or of `pre_run` when it fails.

## When they run

An app's processes run while at least one site has the app installed and enabled. On a production bench Pilot applies the change at once:

| Event | App processes |
|---|---|
| `get-app` | Nothing changes. |
| `install-app` or `new-site` installs the app on its first site | They start. |
| `uninstall-app`, `remove-app` or `drop-site` removes the app from its last site | They stop. |
| The last site using the app disables it, or a site enables it again | They stop, or start again. |
| An app update or revert | Pilot applies its declarations, then restarts the workload. |
| `pilot restart` | Pilot applies the declarations, then restarts the workload. |
| `pilot processes disable` or `enable` | They stop, or start again. |

The dev runner picks up changes on the next `pilot start`. It does not restart a declared process, and a declared process that exits or cannot start does not stop the bench.

## Commands

```text
pilot processes ls [--app APP]       # every process with its state; --app checks one app's declarations
pilot processes restart NAME...      # a process, such as mail-stalwart, or an app for all of its processes
pilot processes disable NAME...      # keep app processes off, saved in [processes] disabled
pilot processes enable NAME...
```

## Failures

A malformed declaration stops the bench from writing its process configuration, so the running processes stay as they are until the app is fixed. `pilot processes ls --app APP` shows the error with the field that caused it.
