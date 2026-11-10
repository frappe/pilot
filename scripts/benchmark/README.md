# Pilot Benchmark Harness

Measures the performance of Pilot's important steps so optimizations can be
compared instead of guessed at. Runs offline against a synthetic bench: site
databases are minimal seeded SQLite files, and no network or host service is
touched.

## Run

```bash
uv run python scripts/benchmark/run.py
```

Useful flags:

```bash
# List every case
uv run python scripts/benchmark/run.py --list

# One group or one case
uv run python scripts/benchmark/run.py --group config
uv run python scripts/benchmark/run.py --case admin.session

# More repetitions for stable numbers
uv run python scripts/benchmark/run.py --group inventory --iterations 50

# Collect cProfile profiles for cases marked profile=True
uv run python scripts/benchmark/run.py --profile

# Compare against a saved baseline
uv run python scripts/benchmark/run.py --save-baseline results/baseline.json
uv run python scripts/benchmark/run.py --compare results/baseline.json
```

Outputs:

- `scripts/benchmark/results/latest.json` - full machine-readable results.
- `scripts/benchmark/results/latest.md` - Markdown report.
- `scripts/benchmark/results/profiles/*.txt` - cProfile reports (with `--profile`).

The harness copies the `pilot` and `admin` packages into a temp install root,
builds a synthetic bench there, and cleans up afterwards. `--root DIR` keeps
the directory, `--keep` keeps the temp directory.

## What it measures

| Group | Steps |
|---|---|
| `cli` | Interpreter startup and end-to-end command latency (`--version`, `--help`, `ls`, `list-apps`, `processes ls`, `tasks status`, `new`) |
| `config` | `BenchConfig` read, validate, serialize, write, wizard defaults |
| `inventory` | Bench/app/site inventory, git accessors, app validation, hostname claims |
| `validator` | Every `App.validate()` check timed separately |
| `site` | Site config, app listing, domains, backup retention and sizing |
| `tasks` | Task runner init, queue/submit, reader, progress, worker state |
| `audit` | Audit log append, newest-first reads, CSV export |
| `templates` | gunicorn, redis, common site config, nginx, systemd, supervisor, Procfile |
| `processes` | Process definitions, state detection (`systemctl`/`supervisorctl`), `processes ls` |
| `admin-api` | Flask app creation and read-only API endpoints |
| `logs` | Log inventory, bounded tail, task output reads |
| `password` | PBKDF2 hash and verify at the production iteration count |

Cases marked `profile=True` are the ones worth a cProfile pass. Add new cases
to the matching `case_*.py` module; the registry picks them up automatically.

## Reading results

Median and p90 are the useful numbers. `min` is the least noisy one on a busy
machine - it is what the operation can do when nothing else competes for the
CPU. `cpu` is `time.process_time`; a large
gap between wall and CPU means the step is waiting on subprocesses or disk.
Absolute times are machine-specific - use `--save-baseline` and `--compare` to
track regressions on the same machine.

Subprocess-heavy steps (git, `systemctl`, `wc`, AST checks) dominate many
paths. Their cost is real, but the count matters more than the per-call cost:
a request that spawns 30 git processes is an optimization target even when
each call is only a few milliseconds.

## Fixture

`fixtures.py` builds:

- 3 apps (including `frappe`) as real git repositories with commits and remotes.
- 3 sites with `site_config.json`, `apps.txt`, and a SQLite site database.
- 12 task directories (queued and terminal) with output logs.
- 2 log files and 300 audit records.

Scale it with `--apps`, `--sites`, `--tasks`, `--log-lines`, and
`--audit-entries`. In-process cases patch host-touching side effects
(`patches.py`): the nginx lock, certificate reads, admin venv creation, and
marketplace registry lookups (stubbed to an empty index so no network call or
host cache write happens). Subprocess CLI cases run the unpatched CLI in the
temp install root.
