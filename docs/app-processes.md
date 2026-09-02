# App Processes

An app can declare background processes it needs, and the bench runs them
alongside the web, worker and socketio processes. The Mail app needs a mail
server; a Flow app needs its own server. Declaring them here replaces
installing and starting that software by hand on every server.

Declarations live in the app's own `pyproject.toml`, not in `bench.toml`.

## Minimal Example

```toml
[tool.pilot.background_processes.flow_server]
cmd = ["flow", "serve"]
```

## Full Example

```toml
[tool.pilot.background_processes.stalwart]
cmd = ["./stalwart", "--config", "config.toml"]
restart_on_failure = true
pre_run = ["./scripts/install_stalwart.sh"]
post_run = ["rm", "-f", "stalwart.sock"]
working_dir = "server"
stop_timeout = 30
env = { STALWART_PATH = "/opt/stalwart" }
```

## Fields

The table key is the process name. It must match `^[a-z0-9][a-z0-9_-]{0,31}$`.
The bench prefixes it with the app name, so the process above runs as
`mail-stalwart` and logs to `logs/mail-stalwart.log`.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `cmd` | list of strings | required | Executable plus arguments. Never a shell string. |
| `pre_run` | list of strings | none | Runs before the process starts. |
| `post_run` | list of strings | none | Runs after the process stops. |
| `restart_on_failure` | bool | `true` | Restart the process when it exits non-zero. |
| `working_dir` | string | the app directory | Directory the process runs in. |
| `stop_timeout` | int | manager default | Seconds to wait for a graceful stop. |
| `env` | table | empty | Environment variables. Keys must match `^[A-Z_][A-Z0-9_]*$`. |

## Paths

`working_dir` defaults to the app's own directory. A relative `working_dir` is
read from there, so `working_dir = "server"` means `apps/<app>/server`.

The first entry of `cmd`, `pre_run` and `post_run` is resolved the same way:

- `./stalwart` or `bin/stalwart` is read from `working_dir`
- `/usr/bin/stalwart` is used as given
- `flow`, with no slash in it, is looked up on `PATH`

Resolution happens without touching the disk, so a `pre_run` hook can be what
downloads the binary in the first place.

## Hooks

`pre_run` is the place to fetch a binary or write a config file. It runs on
every start, not once at install time, so keep it idempotent - check for the
file before downloading it again.

`post_run` runs after the process stops, including between restarts. Use it to
release a socket or a lock file.

Both are argv lists, so a shell line needs an explicit shell:

```toml
pre_run = ["bash", "-c", "test -x ./stalwart || ./scripts/install.sh"]
```

## How They Run

Declared processes join the bench's normal process set, so all three managers
pick them up:

- **systemd**: one unit per process. Hooks become `ExecStartPre` and
  `ExecStopPost`. A bare command runs through `/usr/bin/env`, because systemd
  requires an absolute `ExecStart`.
- **supervisor** and the **dev runner**: neither has hooks of its own, so a
  process with hooks runs as a single shell line that preserves the exit code.

## Failure Handling

Every value here can come from a third-party app, so each field is validated
when it is read and rejected if it is wrong. Control characters are refused
outright - without that an app could inject extra directives into a unit file.
Nothing runs as root.

A malformed declaration fails the whole process-definition build rather than
being skipped. A skipped app would look removed to systemd and supervisor, and
they would stop its running services. Failing instead leaves the bench as it is
until the app is fixed.
