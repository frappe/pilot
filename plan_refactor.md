# Stop non-CLI code from calling Command classes

## Context

`pilot/commands/*` classes are meant to be CLI adapters: thin argparse-facing
wrappers dispatched exactly one way, through `registry.dispatch()`
(`pilot/registry.py:82`, `cls.from_args(args, bench).run()`), itself only
called from `pilot/cli.py`'s `main()`. In practice, `Command.run()` is where
almost all business logic actually lives (there is no separate service
layer), so every other part of the codebase that needs that logic has been
reaching for the Command class directly and calling `.run()` on it. That
couples background tasks, the admin Flask backend, and even `pilot/core`
itself to CLI-shaped objects (argparse wiring, `input()` prompts, `print()`
progress output) that they don't want and shouldn't depend on.

This is the same class of layering violation already fixed once in commit
`a2a78cce` ("fix layering violations in bench, site, and nginx config"),
where `Bench.restart()` called `RestartCommand(self).run()` and was fixed by
adding `Bench.restart_processes()` so the *core* object owns the logic and
the Command becomes the thin caller. This plan applies the identical fix
mechanically across every remaining violation, found via a full-codebase
audit:

- **`pilot/tasks/jobs/*.py`** (12 files) and **`pilot/tasks/callbacks.py`** —
  every background task's `run()` body is "construct a `*Command` and call
  `.run()`". Task code is CLI-agnostic by design (it's driven by
  `wrapper.py` subprocess execution, not argparse) and shouldn't depend on
  the CLI layer at all.
- **`pilot/core/app_dependency_installer.py:47`** — `pilot/core` (the layer
  commands are supposed to depend on) imports `GetAppCommand` from
  `pilot/commands`, i.e. the dependency arrow points backwards. This is the
  clearest inversion, mirroring the exact `Bench.restart()` shape from
  `a2a78cce`.
- **`admin/backend/*`** (5 files) — Flask route handlers
  (`api/v1/benches.py`) instantiate `NewCommand`/`DropBenchCommand` directly
  to serve HTTP requests, and `providers/sites.py`,
  `api/v1/setup.py`/`core.py`/`middleware.py` import helper functions
  (including one explicitly private, `_query_via_db_cli`) out of
  `pilot/commands/*` modules that have no CLI/argparse coupling at all —
  they were just placed in the wrong package.

## Target shape

Each violating `Command.run()` gets split:

1. The actual steps (clone/validate/install, create/drop directories, issue
   a token, query installed apps, etc.) move onto the core domain object
   they already conceptually belong to — extending `Bench`, `App`, `Site`,
   or introducing a small new module only when no existing class fits (e.g.
   `pilot/commands/admin/generate_session.py`'s JWT helpers are plain
   functions today; they move to a new `pilot/core/admin_auth.py` since
   there's no existing class to hang them on).
2. The `Command` subclass shrinks to argparse wiring (`add_arguments`,
   `from_args`) plus a `run()` that calls the new core method and handles
   CLI-only concerns: `input()` confirmation prompts, `print()` progress
   messages, `sys.exit`-worthy error formatting.
3. Every non-CLI caller (task job, callback, admin backend route/provider,
   `AppDependencyInstaller`) is repointed from `SomeCommand(...).run()` to
   the new core method call, dropping the `pilot.commands` import entirely.

### Representative example (do first, proves the pattern)

**`GetAppCommand`** (`pilot/commands/apps/download.py`) is the best first
target: it's the most-called Command from task code (`new_site_task.py`,
`get_app_task.py`, `get_and_install_app_task.py`), and
`pilot/core/app_dependency_installer.py` already has the backwards import to
remove.

- Add an `install()` method (and the private `_clone`/`_normalize_folder`/
  `_install`/`_register`/`_validate`/`_build` steps it currently has) to
  `App` in `pilot/core/app.py`, taking the same
  `install_dependencies`/`skip_validations` flags and returning whatever
  callers need (e.g. `installed_dependencies`). No `print()` calls in this
  method — it's pure core logic.
- `GetAppCommand.run()` becomes: resolve the name/App as today, call
  `self.app.install(...)`, and do the `print(...)` progress/result lines
  around that call.
- `pilot/core/app_dependency_installer.py:47,63` calls `App.install(...)`
  directly (core → core) instead of importing `GetAppCommand` — removes the
  reverse-layering import outright.
- Repoint `new_site_task.py`, `get_app_task.py`,
  `get_and_install_app_task.py`, `new_site_from_backup_task.py` from
  `GetAppCommand(...).run()` to `App(...).install(...)` (or
  `bench.app(name).install(...)` where an App already exists), using
  `self._step(...)` (already available on `BaseTask`) instead of the
  Command's `print()` calls for progress.

### Step 0: give the base `Command` the CLI-plumbing helpers every subclass hand-rolls

Audited across `pilot/commands/**/*.py`: the two-line `print(msg); sys.stdout.flush()`
pairing appears at **19 call-sites in 10 files**, a `print(msg, flush=True)`
variant appears at **12 more call-sites in 4 files**, and a
skip-confirm/`input()`/raise-on-no pattern is hand-duplicated in **3 files**
(`apps/remove.py`, `bench/delete.py`, `runtime/update.py` — the last one
also handles `EOFError`/`KeyboardInterrupt`, the other two don't). None of
this lives on `Command` today (`pilot/commands/base.py` has only
`add_arguments`/`from_args`/`run`), so every subclass reimplements it
slightly differently.

Add two methods to `Command` in `pilot/commands/base.py`:

```python
def report(self, message: str) -> None:
    print(message)
    sys.stdout.flush()

def confirm(self, prompt: str, *, skip: bool = False, error: type[Exception] = BenchError) -> None:
    if skip:
        return
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer not in ("y", "yes"):
        raise error("Aborted.")
```

Then mechanically sweep every call-site found above to use them:
`self.report(...)` in place of the print/flush pairs (both variants), and
`self.confirm(prompt, skip=self.skip_confirm, error=...)` in place of the
three hand-written confirmation blocks (passing `error=MigrateError` for
`runtime/update.py`, which keeps its custom exception type while gaining the
`EOFError`/`KeyboardInterrupt` handling it already had and the other two
didn't). This is independent of the core-extraction work below, touches no
callers outside `pilot/commands/`, and shrinks every subsequent step's diff
since the Command classes being thinned in steps 1-10 stop carrying their
own print/confirm boilerplate. Do this first, as its own commit.

### Same pattern, one module at a time, in this order

1. `GetAppCommand` / `App.install()` (above) — unblocks
   `AppDependencyInstaller`, `new_site_task`, `get_app_task`,
   `get_and_install_app_task`, `new_site_from_backup_task`.
2. `NewSiteCommand` (`pilot/commands/sites/create.py`) → extend `Site`
   (`pilot/core/site.py`, which already has `Site.create()` for the
   frappe-level new-site call) with whatever `NewSiteCommand.run()` adds on
   top (app installation, admin password handling). Repoint
   `new_site_task.py`.
3. `DropSiteCommand` (`pilot/commands/sites/delete.py`) → extend `Site` with
   a `drop()`/similar method. Repoint `drop_site_task.py` and
   `pilot/tasks/callbacks.py:54-62` (`_drop_failed_site`).
4. `UninstallAppCommand`, `RemoveAppCommand`
   (`pilot/commands/apps/uninstall.py`, `remove.py`) → extend `App`.
   Repoint `uninstall_app_task.py`, `remove_app_task.py`.
5. `NewSiteFromBackupCommand` (`pilot/commands/sites/restore.py`) → extend
   `Site`/`Bench` as fits its restore steps. Repoint
   `new_site_from_backup_task.py`.
6. `UpdateCommand` (`pilot/commands/runtime/update.py`) → extend `Bench` or
   `App` per what it updates. Repoint `update_task.py`.
7. `SetupNginxCommand`, `SetupProductionCommand`, `SetupLetsEncryptCommand`,
   `InitCommand` (`pilot/commands/setup/*.py`, `pilot/commands/bench/initialize.py`)
   → extend `Bench` (production/nginx/TLS setup already has manager classes
   under `pilot/managers/`; these Commands should call those managers
   directly or through a `Bench` method rather than each other). Repoint
   `setup_nginx_task.py`, `setup_production_task.py`,
   `setup_letsencrypt_task.py`, `wizard_setup_task.py`.
8. `NewCommand`, `DropBenchCommand` (`pilot/commands/bench/create.py`,
   `delete.py`) → `DropBenchCommand`'s logic extends `Bench` directly
   (`Bench.drop()`, mirroring `Bench.restart_processes()` from `a2a78cce`).
   `NewCommand` runs before a `Bench` object exists, so add a factory,
   e.g. `Bench.create_at(target_directory, name, ...) -> Bench`, in
   `pilot/core/bench.py`. Repoint `admin/backend/api/v1/benches.py`'s
   `_create_bench_locked`/`_delete_bench_locked` route handlers to call
   these directly instead of instantiating `NewCommand`/`DropBenchCommand`.
9. `pilot/commands/admin/generate_session.py` — `issue_token`,
   `decode_token`, `has_scope`, `ensure_jwt_secret`, `issue_login_token`,
   `issue_site_token` are already plain, CLI-free functions; move the whole
   set to a new `pilot/core/admin_auth.py`. `GenerateSessionCommand`/
   `IssueSiteTokenCommand` import from there. Repoint
   `admin/backend/middleware.py:68,101`, `admin/backend/api/v1/setup.py:119`,
   `admin/backend/api/v1/core.py:124`.
10. `pilot/commands/sites/list_apps.py` — `list_installed_apps`,
    `_query_via_db_cli`, `_query_via_frappe` are already CLI-free; move them
    onto `Site` in `pilot/core/site.py` (e.g. `Site.installed_apps()`).
    `ListSiteAppsCommand.run()` and
    `admin/backend/providers/sites.py:9,97` (currently importing the
    private `_query_via_db_cli`) both call the new `Site` method instead.

Each step (including Step 0) is its own commit: move logic to core, shrink
the Command, repoint every caller found in the audit, run the affected
tests, verify, then move to the next module. Do not batch multiple modules
into one commit.

## Verification

- After each step, run the relevant unit tests
  (`tests/pilot/commands/test_commands.py`,
  `tests/pilot/core/...`, `tests/pilot/tasks/test_*.py`,
  `tests/admin/backend/...` — scoped to whatever module changed) and fix any
  import breakage from the moved code.
- Exercise the CLI path directly for at least one changed command per step
  (e.g. `bench get-app <repo>`, `bench drop -b <name>`) to confirm the
  Command still behaves identically end-to-end, not just that tests pass.
- Exercise the non-CLI path too: trigger the corresponding background task
  (via the admin UI or task API) for at least the app-install and
  bench-create/drop steps, and hit the affected `admin/backend` route
  directly (e.g. `POST /benches`, `DELETE /benches/<name>`) to confirm it no
  longer imports `pilot.commands`.
- After all steps: `grep -rn "from pilot.commands" pilot/tasks pilot/core admin/backend`
  should return nothing (aside from `pilot/registry.py`'s discovery and
  intra-command-layer calls like `DropBenchCommand._remove_production`
  calling `RemoveProductionCommand`, which stay as legitimate same-layer
  coupling).

---

## Open items

### FrappeCommand dual-mode `run()` signature

`FrappeCommand.run()` (`pilot/commands/runtime/frappe.py:21`) accepts an
optional `args` parameter:

```python
def run(self, args: list[str] | tuple[str, ...] | None = None) -> None:
```

This creates two call paths:
- **Native CLI** (`bench frappe <subcommand>`): argparse populates `self.args`
  via `nargs=REMAINDER`, but `_cli_fields` skips fields with no default and
  `()` is falsy, so `self.args` is always `()`. The subcommand arguments are
  lost.
- **Programmatic** (`cli.py:98`): calls `FrappeCommand(bench).run(frappe_args)`
  directly, bypassing argparse.

The native path is broken. Two options:
1. Override `add_arguments`/`from_args` for this command (the docstring on
   `Command` explicitly allows this for "the rare shape the inference can't
   express").
2. Drop the `args` field entirely and always require the caller to pass them
   to `run()`.
