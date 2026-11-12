# Sandbox

All benches on a host run as one Linux user. A web worker is a normal process of that user. If someone gets code to run in it, through a bug in a site or through a bad app, that code can do what the user can do. It can read `~/.ssh`, read the other benches, read the admin secrets in `bench.toml`, and use the sudo rules that Pilot installs.

The sandbox makes that process see less. systemd starts each workload process with its own view of the machine. The process is still the same user, but its home directory is an empty folder, the other benches are not there, the system is read-only, and sudo fails. Pilot asks systemd for this with extra lines in the unit file of each workload process.

The sandbox limits the damage when a running workload is compromised. It does not separate one bench from another, and it does not make an app that you install safe. See [What the sandbox does not do](#what-the-sandbox-does-not-do).

## What a workload can see

| Place | In the sandbox |
|---|---|
| Home directory | Empty, so `~/.ssh`, the other dotfiles and the user bus are not there. |
| Other benches and `common_config.toml` | Not there. |
| `bench.toml`, `.credentials` and the admin's `tasks/` | Not there. |
| `sites`, `config`, `logs`, `pids` and `chromium` in the bench | There, and writable. |
| `apps` and `env` in the bench | There, and read-only, because the Admin and the CLI run this code outside the sandbox. |
| Pilot's own files in `config/`, and `sites/common_site_config.json` | Read-only. |
| The rest of the system | Read-only. Host sockets in `/run`, such as `docker.sock`, are hidden. |
| `/tmp` | Each process has its own. |
| sudo and setuid programs | Fail. |
| Network | Works as before. |

The systemd directives behind each row are in [Production](production.md#sandbox).

nginx runs outside the sandbox as the bench user and serves `sites/` from disk, so a link that a workload makes there could reach a file the workload cannot read. For that reason nginx follows no symlinks below a sandboxed bench. A bench without the sandbox keeps its links, because its workload can already read those files. See [Nginx And TLS](production.md#nginx-and-tls).

## Which processes run in it

Pilot adds the sandbox to every workload unit that it writes for a systemd bench.

| In the sandbox | Not in the sandbox |
|---|---|
| web | The Admin and its task worker |
| socketio | The central bootstrap unit |
| Worker pools | The database units, `pilot-mariadb` and `pilot-postgres` |
| redis cache and redis queue | Pilot's host units, such as the monitor and the certificate renewal timer |
| App processes, with their `pre_run` and `post_run` hooks | Anything that you run with the `pilot` CLI |
| | A Supervisor bench and the dev runner |

The Admin stays out because it runs the nginx and certbot commands with sudo. `get-app`, `install-app`, `migrate` and asset builds run from the CLI or as Admin tasks, so the app code that they run is outside the sandbox too.

## Turn it on or off

```toml
[production]
process_manager = "systemd"
sandbox = true # or false
```

- A new systemd bench has no `sandbox` setting and runs in the sandbox.
- A bench that existed before the sandbox became the default got `sandbox = false` from a patch, so an update does not change how it runs. Set `sandbox = true` to opt in. `pilot restart` applies it.
- `sandbox = true` with Supervisor is a config error.

Before Pilot writes the units, it starts a trial unit with the same directives. If the host cannot start it, a bench with no setting runs without the sandbox and Pilot logs the reason. A bench with `sandbox = true` fails with the systemd error and keeps its current units.

The trial needs unprivileged user namespaces. Ubuntu 24.04 blocks them through AppArmor (`kernel.apparmor_restrict_unprivileged_userns = 1`), so there the trial fails with exit code 226.

## Give an app or a process more paths

An empty home breaks things that a bench needs. So Pilot puts back, read-only, what its own workloads use: the Python that the venv links to, Node, nvm, the database socket folder and linked apps. An app that needs more says so in its own `pyproject.toml`. Pilot does not need to know the app.

```toml
# For every workload unit of the bench, while a site uses the app.
[tool.pilot.sandbox]
read_paths = ["~/.cache/models"]
write_paths = ["flow-server"]

# For this process only.
[tool.pilot.processes.stalwart]
command = ["./bin/stalwart"]
write_paths = ["/var/lib/stalwart"]
```

A process can read a path in `read_paths`. It can read and write a path in `write_paths`. A path is a folder in the bench (`flow-server`), in the home directory (`~/...`) or on the system (`/...`). Pilot accepts a home or system path only below a short list of roots, and it refuses some reserved names. The rules are in [App Processes](app-processes.md#sandbox-paths).

## What the sandbox does not do

The sandbox does not separate one bench from another. All benches run as one user, so code in one bench can read the files and environment of another bench's processes through `/proc/<pid>`, signal them, and reach their redis and other ports on `127.0.0.1`. Put workloads that must not reach each other under separate host users. Outbound network access also stays open.

The sandbox does not protect you from an app that you install. An app chooses its own `read_paths` and `write_paths`, and the ones under `[tool.pilot.sandbox]` apply to every workload unit of the bench. Pilot checks that a path is below an allowed root, but it cannot know what the path is for. So an app can ask for the data of another program that the host user can read, such as `~/.local/share/containers`. App code also runs without the sandbox during `get-app`, `install-app`, `migrate` and asset builds. Install only apps that you trust.

File permissions still apply in the sandbox, so a process never gets more than the host user has.

The [Trust Model](../SPEC.md#trust-model) lists what follows from one shared host user.
