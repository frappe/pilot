# Performance

Pilot's performance baseline and the hot spots worth optimizing. Fresh numbers come from the [benchmark harness](../scripts/benchmark/README.md): `uv run python scripts/benchmark/run.py`. Absolute times are machine-specific, so save a baseline (`--save-baseline`) and compare (`--compare`) on the same machine.

## Baseline

Measured 2026-11-10 on Python 3.14 / Linux with the synthetic fixture (3 apps, 3 sites, 12 tasks). Wall time is what a user or request waits; CPU is `time.process_time`. The CPU column counts only the measured process: the `pilot ...` rows run the CLI as a child process, so their CPU reads near zero while Wall shows the real duration. Subprocess-heavy steps vary run to run - run at least the default iterations and read the median and p90.

| Step | Wall | CPU | Why it costs |
|---|---:|---:|---|
| `pilot new` | ~440ms | ~1ms | CLI startup (~120ms), PBKDF2 password hash (~255ms), port probes |
| `pilot --version` | ~130ms | ~1ms | Interpreter startup plus importing every command module |
| `App.validate()` | ~200ms | ~22ms | 21 subprocesses: AST checks in the bench Python plus git metadata |
| `GET /api/v1/sites/<site>/apps` | ~65ms | ~20ms | 30 git subprocesses (about 10 per app) from `BenchInventory` and `AppProvider` |
| `create_app()` | ~65ms | ~65ms | Flask blueprint registration and werkzeug URL-rule compilation |
| `pilot processes ls` | ~200ms | ~1ms | CLI startup plus 18 git subprocesses for definitions and app processes |
| `systemctl show` state read | ~35ms | ~6ms | One subprocess per call |
| `GET /api/v1/logs` | ~14ms | ~4ms | `wc -l` subprocess per log file |

## Optimizations applied

**CLI startup: every command now pays for one command, not for fifty.**

Three changes, all behavior-preserving (`pilot --help`, `pilot ls --help`, `pilot setup nginx --help` and the usage line in error messages are byte-identical):

1. `pilot/internal/cli/fields.py` no longer runs a field's `default_factory` while building the parser. The one factory with a heavy import (`issue-site-token --ttl`, which read `Session.DEFAULT_TTL`) pulled the admin backend and the config layer into every CLI invocation. The factory now runs when the dataclass is built, i.e. only for the command that actually runs.
2. `pilot/internal/cli/registry.py` builds the parser for the command the user asked for instead of for all of them. `pilot/internal/cli/dispatch.py` reads that command out of the arguments first. An unknown name still builds every command, so argparse's error keeps listing them all, and the usage line names every command either way.
3. `pilot/internal/tasks/authoring.py` applies the same deferral on the task path, which shares the field helpers.

Measured as the minimum of 12 runs over two rounds, on the same machine, before and after:

| Command | Before | After | Change |
|---|---:|---:|---:|
| `pilot ls` | 166ms | 103ms | **-38%** |
| `pilot --version` | 162ms | 125ms | -23% |
| `pilot --help` | 161ms | 129ms | -20% |
| `pilot processes ls` | 165ms | 152ms | -8% |

`tests/pilot/test_cli.py` guards the import budget: the parser build may not import the admin backend, the config layer, or Flask. `tests/pilot/commands/test_command_authoring.py` guards the deferral: a factory is not called during the parser build, its value still applies when the flag is omitted, an explicit flag still wins, and list factories stay fresh per command.

## Optimization targets

1. **Command discovery** (`pilot/internal/cli/registry.py`): `_discover()` imports all 61 command modules - about 40ms, mostly the `@dataclass` decoration of every command class. A generated name-to-module index would let a command import only its own module; the index needs a test that fails when it goes stale.
2. **Git subprocess fan-out** (`pilot/internal/git.py`, `BenchInventory`, `AppProvider`): each `GitRepo` property is a subprocess, and callers ask for the same repository repeatedly. `bench.apps()` spawns two per app, `AppProvider.get_app` about eight more, and `GET /api/v1/sites/<site>/apps` repeats both per app. Batch the reads (`git config --get-regexp`, `git for-each-ref`, one `git status`) or cache per bench/revision.
3. **Validator subprocesses** (`pilot/core/app/validator`): 21 subprocesses per validation, dominated by `ImportCheck`, `FrappeCompatibilityCheck`, `HooksCheck`, `DependencyDeclarationsCheck`, and `SyntaxCheck`. Batch the AST scripts, share one bench-Python invocation, and avoid `bench.apps()` inside checks.
4. **Flask app creation** (`admin/backend/app.py`): `create_app()` performs 66 blueprint registrations and compiles 618 URL rules on every worker start. Consider registering once per process or deferring blueprint imports.
5. **Log inventory** (`admin/backend/providers/logs.py`): `get_all()` runs `wc -l` per log file. Track line counts from the writer or cache them by mtime.
6. **Per-request token writes** (`admin/backend/internal/session.py`): every authenticated request rewrites the active-token store with `fsync` (`ActiveTokens.add`). A write-behind cache or coarser flush would cut tail latency on slow disks.
7. **Marketplace registry on the request path** (`pilot/integrations/marketplace.py`, `pilot/core/registry_cache.py`): `App.is_marketplace`, used by `GET /api/v1/sites/<site>/apps`, loads the registry - `git status` on the clone plus an hourly `git ls-remote` over the network. The first request after an hour pays the network call. Keep the registry off the request path or refresh it asynchronously.

## Not measured

`pilot init`, site create/migrate/backup/restore, app install/update, `get-app`, letsencrypt, production deploys, and database-backed devtools need a real bench, network, or services. They belong to `tests/integration` and `tests/e2e`; the harness stays offline and deterministic by excluding them.
