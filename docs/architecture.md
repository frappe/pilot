# Architecture Specification

---

## Package layout

```
pilot/
├── pyproject.toml               # installs the `bench` CLI entry point
│
└── pilot/                         # Python package
    ├── __init__.py
    ├── cli.py                   # thin entry point — global flags + Frappe passthrough
    ├── registry.py              # auto-discovers commands, builds parser, dispatches
    ├── loader.py                # find_bench_root / load_bench — bench resolution
    ├── platform.py              # OS detection and system package manager abstraction
    ├── utils.py                 # write_toml — stdlib TOML serialiser
    │
    ├── config/                  # Data classes that model bench.toml
    │   ├── __init__.py
    │   ├── bench_config.py      # BenchConfig — top-level config object
    │   ├── app_config.py        # AppConfig
    │   ├── site_config.py       # SiteConfig (includes domains, ssl)
    │   ├── mariadb_config.py    # MariaDBConfig
    │   ├── redis_config.py      # RedisConfig
    │   ├── worker_config.py     # WorkerConfig
    │   ├── nginx_config.py      # NginxConfig
    │   └── letsencrypt_config.py # LetsEncryptConfig
    │
    ├── core/                    # Domain objects that own real state on disk
    │   ├── __init__.py
    │   ├── bench.py             # Bench — root object, owns path resolution
    │   ├── app.py               # App — one git repo + pip package
    │   └── site.py              # Site — one Frappe site directory
    │
    ├── managers/                # System-level concerns (install, configure)
    │   ├── __init__.py
    │   ├── mariadb_manager.py        # MariaDBManager
    │   ├── redis_manager.py          # RedisManager
    │   ├── python_env_manager.py     # PythonEnvManager
    │   ├── process_manager.py        # HonchoProcessManager — built-in Procfile runner
    │   ├── nginx_manager.py          # NginxManager — config generation and reload
    │   └── letsencrypt_manager.py    # LetsEncryptManager — cert obtain and renew
    │
    ├── commands/                # One self-registering Command subclass per file
    │   ├── __init__.py
    │   ├── base.py              # Command    — base class all commands subclass
    │   ├── new.py               # NewCommand     — scaffold a starter bench.toml
    │   ├── init.py              # InitCommand    — install deps, clone framework app
    │   ├── start.py             # RunCommand     — run Procfile processes in foreground
    │   ├── build.py             # BuildCommand
    │   ├── update.py            # UpdateCommand
    │   └── setup/               # commands with group = "setup"
    │       ├── __init__.py
    │       ├── nginx.py         # SetupNginxCommand
    │       ├── letsencrypt.py   # SetupLetsEncryptCommand
    │       └── production.py    # SetupProductionCommand
    │
    ├── tasks/                   # Task execution and tracking (see specs/tasks.md)
    │   ├── __init__.py
    │   ├── models.py            # TaskInfo dataclass
    │   ├── task_runner.py       # TaskRunner — forks child, writes task directory
    │   ├── task_reader.py       # TaskReader — reads task directory (stateless)
    │   └── wrapper.py           # entry point for the forked child (stdlib only)
    │
    └── admin/                   # Flask admin interface (see specs/admin.md)
        ├── __init__.py
        ├── app.py               # create_app(bench_root) factory
        ├── readers/             # Stateless filesystem/DB readers
        └── views/               # Flask blueprints (tasks.py replaces commands.py)
```

---

## Bench directory layout (what gets created on disk)

```
bench-cli/
└── benches/
    └── my-bench/               # bench root — all benches under benches/<name>/
        ├── bench.toml          # infra config (python, db, redis, workers)
        ├── apps/               # git-cloned app source trees
        │   ├── frappe/
        │   └── erpnext/
        ├── sites/              # site data directories
        │   ├── assets/         # built JS/CSS assets served by the web process
        │   ├── apps.txt        # installed app list read by frappe
        │   ├── common_site_config.json
        │   └── site1.localhost/
        │       ├── site_config.json
        │       ├── private/
        │       └── public/
        ├── env/                # shared Python virtualenv (managed by uv)
        ├── logs/               # per-process log files
        │   ├── web.log
        │   ├── worker.default.1.log
        │   └── ...
        ├── config/             # generated service config files
        │   ├── redis_cache.conf
        │   ├── redis_queue.conf
        │   ├── redis_socketio.conf
        │   ├── Procfile        # built-in process runner input
        │   └── nginx/          # written by bench setup nginx (production.enabled = true)
        │       ├── include.conf    # single include directive — symlinked into nginx config_dir
        │       ├── site1.example.com.conf
        │       └── site2.example.com.conf
        ├── pids/               # PID files (bench.pid, per-process <name>.pid)
        └── tasks/              # one sub-directory per admin-triggered task
    └── 20250521-143022-a1b2c3/
        ├── meta.json       # command, args, started_at, finished_at, exit_code
        ├── pid             # integer PID of the forked child
        ├── output.log      # combined stdout + stderr
        └── status          # running | success | failed | killed
```

---

## Config layer (`pilot/config/`)

Config classes are pure data holders. They are constructed by parsing `bench.toml` (via `tomllib` from the Python 3.11+ stdlib) and expose no side effects. They are the only objects that know the shape of the TOML file.

### `BenchConfig`

```python
@dataclass
class BenchConfig:
    name: str
    python_version: str
    apps: List[AppConfig]       # framework app(s) to clone on bench init
    mariadb: MariaDBConfig
    redis: RedisConfig
    workers: WorkerConfig
    nginx: NginxConfig = field(default_factory=NginxConfig)
    letsencrypt: LetsEncryptConfig = field(default_factory=LetsEncryptConfig)

    @classmethod
    def from_file(cls, path: Path) -> 'BenchConfig':
        """Load and validate bench.toml. Raises ConfigError on any violation."""

    def validate(self) -> None:
        """Run all validation rules defined in config.md. Raises ConfigError."""

    @property
    def framework_app(self) -> AppConfig:
        """The first app in the list (or a default frappe AppConfig if none listed)."""
```

`apps` is used only during `bench init` to clone the framework app. After init, apps are discovered from the filesystem via `Bench.apps()`. Sites are never stored in `BenchConfig` — `Bench.sites()` always scans the filesystem.

### `AppConfig`

```python
@dataclass
class AppConfig:
    name: str
    repo: str
    branch: str
```

### `SiteConfig`

```python
@dataclass
class SiteConfig:
    name: str
    db_name: str
    db_password: str
    apps: List[str]          # ordered list of app names
```

### `MariaDBConfig`

```python
@dataclass
class MariaDBConfig:
    host: str = 'localhost'
    port: int = 3306
    root_password: str = ''
    admin_user: str = 'root'
    socket_path: str = ''
    version: Optional[str] = None   # e.g. "10.6", "11.4"
    instance: str = ''              # '' = shared server; else this bench's own mariadb@<instance>
    data_dir: str = ''              # instance datadir; defaults to /var/lib/mysql-<instance>
```

When `instance` is set the bench runs its own MariaDB server rather than the
shared one — see [Per-bench MariaDB instances](#per-bench-mariadb-instances).

### `RedisConfig`

```python
@dataclass
class RedisConfig:
    cache_port: int = 13000
    queue_port: int = 11000
    socketio_port: int = 12000
    version: Optional[str] = None   # e.g. "7", "7.0"
```

### `WorkerConfig`

```python
@dataclass
class WorkerConfig:
    default: int = 2
    short: int = 1
    long: int = 1
```

---

## Core layer (`pilot/core/`)

Core objects represent things that exist (or will exist) on disk. They receive the relevant config and the parent `Bench` object so they can resolve paths without knowing where the bench root is.

### `Bench`

The root object. All commands construct a `Bench` from a `BenchConfig` and a root path, then call methods on it.

```python
class Bench:
    def __init__(self, config: BenchConfig, path: Path): ...

    # Path helpers
    @property
    def apps_path(self) -> Path: ...      # <root>/apps/
    @property
    def sites_path(self) -> Path: ...     # <root>/sites/
    @property
    def env_path(self) -> Path: ...       # <root>/env/
    @property
    def logs_path(self) -> Path: ...      # <root>/logs/
    @property
    def config_path(self) -> Path: ...    # <root>/config/
    @property
    def pids_path(self) -> Path: ...      # <root>/pids/
    @property
    def python(self) -> Path: ...         # <root>/env/bin/python
    @property
    def pip(self) -> Path: ...            # <root>/env/bin/pip

    # Domain object accessors (both scan the filesystem, not bench.toml)
    def apps(self) -> List[App]: ...       # scans apps/ for dirs with .git
    def init_apps(self) -> List[App]: ... # reads bench.toml [[apps]] — used only during bench init
    def sites(self) -> List[Site]: ...    # scans sites/ for dirs with site_config.json

    def create_directories(self) -> None:
        """Create apps/, sites/, logs/, config/, pids/ if they do not exist."""
```

### `App`

```python
class App:
    def __init__(self, config: AppConfig, bench: Bench): ...

    @property
    def path(self) -> Path: ...           # bench.apps_path / config.name

    @property
    def is_cloned(self) -> bool: ...      # True if path exists and is a git repo

    def clone(self) -> None:
        """git clone config.repo --branch config.branch into apps/."""

    def install(self) -> None:
        """pip install -e . inside the bench virtualenv."""

    def update(self) -> None:
        """git fetch + git merge origin/<branch>. Raises on merge conflicts."""

    def build_assets(self) -> None:
        """Run the app's asset build command if it defines one (e.g. yarn build)."""
```

### `Site`

```python
class Site:
    def __init__(self, config: SiteConfig, bench: Bench): ...

    @property
    def path(self) -> Path: ...           # bench.sites_path / config.name

    @property
    def exists(self) -> bool: ...         # True if path/site_config.json is present

    def create(self, mariadb: MariaDBConfig) -> None:
        """
        Run `bench new-site` via the framework app CLI.
        Creates the MariaDB database, user, and site_config.json.
        """

    def install_app(self, app_name: str) -> None:
        """Run `bench --site <name> install-app <app_name>`."""

    def migrate(self) -> None:
        """Run `bench --site <name> migrate`."""
```

---

## Managers layer (`pilot/managers/`)

Managers handle interactions with system services and tools. They do not know about Sites or Apps directly — they receive only what they need.

### `MariaDBManager`

```python
class MariaDBManager:
    def __init__(self, config: MariaDBConfig): ...

    def install(self) -> None:
        """
        Install MariaDB via the system package manager.
        Ubuntu: apt-get install mariadb-server[-<version>]  e.g. mariadb-server-10.6
        macOS:  brew install mariadb[@<version>]            e.g. mariadb@10.6
        When config.version is None, the package manager's default is used.
        """

    def is_installed(self) -> bool: ...

    def is_running(self) -> bool: ...

    def start(self) -> None:
        """
        Start the MariaDB service (service_unit(): "mariadb" shared, or
        "mariadb@<instance>" when the bench owns an instance).
        Ubuntu: systemctl start <service_unit>
        macOS:  brew services start mariadb[@<version>]  — uses the versioned formula name
                so the correct service is started when a non-default version is installed.
        """

    def is_dedicated(self) -> bool:
        """True when config.instance is set (this bench runs its own server)."""

    def provision_instance(self, staging_dir: Path) -> None:
        """Linux only. Create/start/secure this bench's own MariaDB instance:
        stage the [mariadbd.<instance>] option group into mariadb.conf.d/, create
        the mysql-owned datadir, then `systemctl enable --now mariadb@<instance>`
        (the packaged template runs mariadb-install-db), and set the root
        password. See Per-bench MariaDB instances below.
        """

    def _connect(self) -> 'MySQLConnection':
        """Return an authenticated root connection (via the instance socket/port
        when dedicated, otherwise the shared server)."""
```

---

## Per-bench MariaDB instances

By default a bench uses the **shared system MariaDB** — one server on `:3306`
that every bench and site connects to. This is the legacy behaviour and remains
the default for any bench whose `bench.toml` does not set `mariadb.instance`.

`bench new` (on Linux) instead provisions each new bench its **own** MariaDB
server: `mariadb.instance = <bench-name>`, run as the systemd template unit
`mariadb@<instance>`, with its own datadir, socket, and TCP port.

### Why a server per bench, not just a database per bench

The shared server already gives each *site* its own database and user, so the
motivation is isolation at the **server** level:

- **Independent snapshots & rollback.** ZFS snapshots and rollbacks operate on a
  bench's own datadir. With one shared datadir under `/var/lib/mysql`, rolling
  one bench's database back to a snapshot would roll back *every* bench's data.
  A datadir per instance (`/var/lib/mysql-<instance>`) makes snapshot/restore
  truly per-bench — the reason the datadir is a sibling path, never nested.
- **Blast-radius containment.** A runaway query, a crash, a corrupted table, or a
  `DROP`/restore in one bench cannot affect another. Resource limits
  (buffer pool, connections) are per server.
- **Independent lifecycle & config.** Each bench can run a different server
  config, be started/stopped/upgraded on its own, and be torn down by simply
  removing its instance — without coordinating with other tenants of a shared
  server.
- **Parity with production multitenancy.** Each bench is a self-contained unit
  (apps, sites, redis, processes — and now its database), which matches how
  benches are otherwise isolated on disk and in the process manager.

The trade-off is higher memory use (each server has its own InnoDB buffer pool),
so the shared mode stays fully supported for small or single-bench hosts.

### How it works (Linux)

1. **Config group.** `_write_instance_config` writes
   `[mariadbd.<instance>]` (datadir, socket, port, pid-file, bind-address) to
   `/etc/mysql/mariadb.conf.d/99-bench-<instance>.cnf`. The packaged
   `mariadb@.service` starts `mariadbd --defaults-group-suffix=.<instance>`, so
   only that instance reads this group; the shared server ignores it. The
   `99-` prefix / `mariadb.conf.d/` location matters: that directory is included
   **after** `conf.d/` and after `50-server.cnf`, whose base `[mariadbd]` sets a
   `pid-file`. Read any earlier, the instance's `pid-file` would be silently
   overridden back to the shared default and collide.
2. **Datadir.** A mysql-owned datadir is created at `/var/lib/mysql-<instance>`
   (a sibling of `/var/lib/mysql`, never nested — see above). When the bench's
   `[volume]` is enabled, its ZFS `mariadb` dataset is mounted here first, so
   the database lives on ZFS; otherwise it is a plain directory.
3. **Start & secure.** `systemctl enable --now mariadb@<instance>` starts the
   server (its `ExecStartPre` runs `mariadb-install-db` to initialise the
   datadir), then the root password from `bench.toml` is set the same way a
   fresh shared install is secured.

During `bench init` the instance is provisioned **after** volume setup so a
ZFS-backed datadir is mounted before initialisation; sites then connect to the
instance over its socket, and the admin UI over its port.

### Platform support

Per-bench instances are **Linux only** — they rely on systemd template units.
On macOS (a development-only platform with no systemd) `bench new` leaves
`mariadb.instance` empty, so macOS benches use the shared Homebrew MariaDB;
per-site database isolation still applies.

### `RedisManager`

```python
class RedisManager:
    def __init__(self, config: RedisConfig, bench: Bench): ...

    def install(self) -> None:
        """
        Install Redis via the system package manager.
        Ubuntu: apt-get install redis-server  (apt has no versioned redis package names;
                use the official Redis apt repo for version pinning before running bench init)
        macOS:  brew install redis[@<version>]  e.g. redis@7
        Redis is not started as a system service; bench run launches it
        directly from the Procfile/supervisor config with a custom port.
        """

    def is_installed(self) -> bool: ...

    def generate_configs(self) -> None:
        """
        Write three minimal redis.conf files to bench.config_path:
          redis_cache.conf, redis_queue.conf, redis_socketio.conf.
        Each binds to 127.0.0.1 on its configured port.
        """
```

### `PythonEnvManager`

All apps share a single virtualenv at `env/`. This is intentional: every app installed in the bench can be installed on any site. Because Frappe loads all installed apps into the same Python process, they must all live in the same environment. Dependency conflicts between apps are resolved at the app level, not by isolating environments.

```python
class PythonEnvManager:
    def __init__(self, bench: Bench): ...

    def ensure_python(self) -> None:
        """
        Check that the configured Python version is available.
        Ubuntu: install via the deadsnakes PPA (python3-<version>-venv).
        macOS:  install via Homebrew (brew install python@<version>).
               Prints a hint to use pyenv if the version is unavailable via brew.
        """

    def create_venv(self) -> None:
        """uv venv --python <version> bench.env_path if it does not already exist. uv is auto-installed."""

    def install_app(self, app: App) -> None:
        """pip install -e app.path using bench.pip."""

    def install_node(self) -> None:
        """
        Install Node.js 18 LTS if not present (required for asset builds).
        Ubuntu: download and run the NodeSource setup script, then apt-get install nodejs.
        macOS:  brew install node.
        Yarn is installed globally afterward via: npm install -g yarn.
        """
```

### `HonchoProcessManager`

The built-in Procfile runner. No external process manager required.

```python
class HonchoProcessManager:
    def __init__(self, bench: Bench): ...

    def generate_config(self) -> None:
        """Write config/Procfile from _process_definitions()."""

    def start(self) -> None:
        """
        Read config/Procfile and spawn each process with subprocess.Popen.
        A thread per process streams output to stdout with '<name> |' prefix
        and writes to logs/<name>.log. Per-process PID files written to pids/<name>.pid.
        Blocks until SIGINT/SIGTERM; sends SIGTERM to all children, then waits.
        """

    def stop(self) -> None:
        """Send SIGTERM to the process group via pids/bench.pid."""

    def is_running(self) -> bool:
        """True if pids/bench.pid exists and the process is alive."""

    def _process_definitions(self) -> List[ProcessDefinition]:
        """
        Build the ordered list of processes from bench config:
          web, socketio, N×default worker, M×short worker, K×long worker,
          redis (single) or redis_cache/redis_queue/redis_socketio (multi).
        """
```

`ProcessDefinition` is a small dataclass:

```python
@dataclass
class ProcessDefinition:
    name: str           # e.g. "worker_default_1"
    command: str        # full shell command string with absolute paths
    log_file: Path      # bench.logs_path / f"{name}.log"
```

---

## Commands layer (`pilot/commands/`)

Each command class receives a `Bench` object. It orchestrates managers and core objects in the correct order. Commands are the only layer that produces user-visible console output.

```python
class NewCommand:
    def __init__(self, bench_name: str): ...
    def run(self) -> None: ...          # create benches/<name>/ and write bench.toml

class InitCommand:
    def __init__(self, bench: Bench): ...
    def run(self) -> None: ...          # install deps, venv, clone framework app, generate Procfile

class StartCommand:
    def __init__(self, bench: Bench): ...
    def run(self) -> None: ...          # start Procfile processes in foreground

class BuildCommand:
    def __init__(self, bench: Bench): ...
    def run(self) -> None: ...

class UpdateCommand:
    def __init__(self, bench: Bench): ...
    def run(self) -> None: ...          # git pull all apps, reinstall, migrate all sites
```

---

## CLI entry point and command registry

Built with `argparse` (stdlib). Zero Python dependencies. The wiring is split into
three small modules so that **a command owns everything about itself in one file** —
adding or changing a command never touches the CLI layer.

### `commands/base.py` — the `Command` base class

Every command subclasses `Command` and declares its own metadata, arguments, and
execution. Subclasses keep their own `__init__` (used directly in tests and by other
commands); the registry builds an instance through `from_args`.

```python
class Command:
    name: ClassVar[str]                  # CLI name, e.g. "remove-app"
    help: ClassVar[str] = ""
    group: ClassVar[str | None] = None   # subcommand group: "setup" | "volume" | None
    requires_bench: ClassVar[bool] = True # registry loads the Bench and passes it in

    def __init__(self, bench=None): ...

    @classmethod
    def add_arguments(cls, parser): ...           # declare argparse arguments

    @classmethod
    def from_args(cls, args, bench): ...          # map parsed args → constructor

    def run(self) -> None: ...                    # do the work
```

### `registry.py` — discovery, parser, dispatch

1. **Discover** — imports every module under `commands/` and collects all `Command`
   subclasses that set a `name`. No hand-maintained list.
2. **`build_parser()`** — adds the global flags (`--verbose`, `--yes`, `--bench`) once,
   then one sub-parser per command (and a parent parser per `group`). Each command's
   `add_arguments()` populates its own sub-parser, and `set_defaults(_command_cls=…)`
   records which class owns it.
3. **`dispatch(args)`** — reads `_command_cls`, loads the `Bench` when
   `requires_bench` is set, then runs `cls.from_args(args, bench).run()`. No `elif`
   chain.

### `cli.py` — the thin entry point

Resolves global flags, then either forwards to the registry or handles the one special
case: `bench frappe …` / unknown sub-commands are passed through to `env/bin/bench`
inside the active bench (handled before argparse so flags like `--site` aren't consumed).

### Adding a command

Create one file under `commands/` — nothing else:

```python
# pilot/commands/list_apps.py
from pilot.commands.base import Command


class ListAppsCommand(Command):
    name = "list-apps"
    help = "List apps installed in the bench."

    def run(self) -> None:
        for line in (self.bench.sites_path / "apps.txt").read_text().splitlines():
            print(line)
```

For arguments and grouping:

```python
class RemoveAppCommand(Command):
    name = "remove-app"
    help = "Remove an app from the bench."

    @classmethod
    def add_arguments(cls, parser):
        parser.add_argument("app", help="App name to remove.")

    @classmethod
    def from_args(cls, args, bench):
        return cls(bench, args.app, skip_confirm=args.yes)
```

Set `group = "setup"` (or `"volume"`) on the class to nest it as `bench setup <name>`.
Set `requires_bench = False` for commands that don't operate on a bench (e.g. `new`,
`build-admin`).

---

## Platform detection (`pilot/platform.py`)

All OS-specific branching lives in one module. Every other module imports from here rather than calling `platform.system()` or `shutil.which()` inline.

```python
from enum import Enum

class Platform(Enum):
    LINUX = 'linux'
    MACOS = 'macos'

def detect() -> Platform:
    """Return Platform.MACOS on Darwin, Platform.LINUX otherwise."""

def is_macos() -> bool: ...
def is_linux() -> bool: ...
```

### `SystemPackageManager` — abstract base

```python
class SystemPackageManager(ABC):
    @abstractmethod
    def install(self, *packages: str) -> None:
        """Install one or more system packages."""

    @abstractmethod
    def is_installed(self, package: str) -> bool:
        """Return True if the package is already installed."""
```

#### `AptPackageManager`

Used on Ubuntu/Debian. Calls `sudo apt-get install -y <packages>`.

```python
class AptPackageManager(SystemPackageManager):
    def install(self, *packages: str) -> None: ...    # sudo apt-get install -y
    def is_installed(self, package: str) -> bool: ... # dpkg -l <package>
```

#### `BrewPackageManager`

Used on macOS. Requires Homebrew to be present (`brew` in `$PATH`).

```python
class BrewPackageManager(SystemPackageManager):
    def install(self, *packages: str) -> None: ...    # brew install
    def is_installed(self, package: str) -> bool: ... # brew list <package>
```

#### `get_package_manager() -> SystemPackageManager`

Factory function — returns `BrewPackageManager()` on macOS, `AptPackageManager()` on Linux. Called once per command run, not per method call.

---

## Error handling

- All config errors raise `pilot.exceptions.ConfigError`.
- All command errors raise `pilot.exceptions.BenchError`.
- The CLI catches these at the top level and prints a clean error message without a traceback (unless `--verbose` is passed).
- Subprocess failures (git, pip, mysql) raise `pilot.exceptions.CommandError` with the captured stderr.

---

## Dependencies

bench-cli has **zero Python dependencies** — it uses only the Python 3.11+ standard library:

| stdlib module | Purpose |
|--------------|---------|
| `tomllib` | Parse `bench.toml` |
| `argparse` | CLI argument parsing |
| `subprocess` | Spawn system commands (git, uv, mariadb, etc.) |
| `threading` | Per-process output streaming in `HonchoProcessManager` |
| `signal` | Handle SIGINT/SIGTERM for graceful shutdown |
| `pathlib` | All filesystem path operations |

The admin interface uses `flask` and `frappe-ui` (declared separately in `admin/`). The bench CLI itself imports nothing outside stdlib.

`bench setup nginx` and `bench setup letsencrypt` install the `nginx` and `certbot` system packages if not already present (via apt on Ubuntu, via Homebrew on macOS). These are managed by their respective managers, not Python dependencies.

**Production setup targets Ubuntu/Linux servers.** macOS is a development platform; run `bench start` there instead.
