# Code and refactoring

## Taste

- Readable by a new joiner during an outage. Simple, not clever ("simple code is much better than clever code").
- Least code: fewer lines, less complexity. Delete before adding. Net negative is good.
- Not dense: blank lines between logical blocks; multi-line over packed one-liners.
- One way to do one thing: find how the codebase already does the job (task, step, config write, git call, subprocess, API route, UI action, fixture) and follow it. A clearly better way replaces every use in the same change when small; otherwise propose it separately. Never leave two.
- Explicit config over implicit behavior. Standard APIs and repo helpers (`pilot.internal.git.GitRepo`, `atomic_file`, the TOML store) before custom logic.
- Do what was asked, read literally.

## Naming

Naming draws the most corrections. Review every new name.

- Full words in code and file names. No abbreviations (`opts` → `options`, `cfg` → `config`).
- No-argument method that computes one noun-like value: `@property` (`nginx_version`).
- Method with arguments or multi-step work: `get_<what_it_returns>()` (`get_commit_sha()`).
- Booleans: `is_` or `has_` (`is_workload_running`, `has_passwordless_sudo`).
- Default to public methods. Use `_` only for raw parsing, security-sensitive validation, OS plumbing, or genuinely internal details. Never make a method private just because it has one caller.
- Names say what the thing does. No project prefix inside the project. No folder name repeated in file names.
- Config keys, API fields and task `command` names in full words, named for meaning and unit (`memory_mib`, `timeout_seconds`).
- Revert a worse rename. A rename updates all callers, tests, docs and UI, and keeps old public names working.

## Structure

- Object-owned syntax: `Server().bench("main").site("a.local").install_app("erpnext")`. Do not pass a bench and site into an unrelated helper object.
- Real behavior in `pilot.core`, managers or tasks. CLI commands (`pilot/commands`) and API routes (`admin/backend/api/v1`) parse, authorize, queue and delegate.
- Long operations are `Task` subclasses with `@step` phases and `@on_success`, `@on_failure`, `@on_cancel` callbacks. Queue with `SomeTask.queue(bench, ...)`.
- Group related files in folders (`pilot/core/site/`, `pilot/core/bench/migration/`) instead of same-prefix modules. No new `utils`, `helpers` or `common` modules. One-line helpers stay inline.
- Real imports in package `__init__.py` for autocomplete; no lazy re-exports. Absolute imports.
- Functions around 25 lines when a split keeps readability; never split into single-use helpers. Cyclomatic complexity 8 or less. Files about 100 to 500 lines.
- Type hints on public functions and important data structures. Dataclasses for task fields and records.
- Specific exceptions from `pilot/exceptions.py`; handle only what the code can recover from.

## Comments and docstrings

- Short, terse class or method docstring only when the name does not say it.
- Inline comments only for what code cannot say: business rule, invariant, external quirk (Frappe, nginx, MariaDB, systemd behavior), concurrency rule. Link a doc or blog post when one explains it.
- Never: file-top comments, "previously", change explanations, hints of the old pattern.
- Do not trim away real rationale.

## Constants and configuration

- Constants only for real tunables: timeouts, retries, limits, sizes. Not for keys, labels, route lists or status maps.
- No table that renames one state to another; use the same values.
- One limit, one constant, used by every dependent check.
- Hardcode until a second value is real. Remove options nobody needs.
- New config goes in the right group (`bench.toml` per bench, `common_config.toml` for settings shared across benches), read and written through the config model, never by string edits.

## Python

- Standard library and existing dependencies first; no new dependency when one exists.
- Subprocess calls through the existing runner helpers; print or log the command and its output for the operator.
- Templates for rendered config (nginx, systemd, supervisor) instead of large f-strings.
- Files written atomically through `atomic_file`.
- `pyproject.toml` and `uv`. Latest stable library versions.

## Admin UI

- Vue with Frappe UI and the Espresso design system. No custom component when Frappe UI has one.
- Layout approval first: mockup for a new page, dialog or form change.
- Balanced and minimal: grouped sections, short labels, few descriptions, no layout jump when a value changes, sensible defaults.
- Destructive actions grouped and confirmed. Hide actions that do not apply to the current state.
- Long work queues a task and shows its steps; no blocking request.
- Biome clean. Vendor libraries locally; no CDN.

## Shell scripts

- `set -euo pipefail`, `bash -n` clean, executable mode.
- Output: `==> step` and `error:` lines. No debug leftovers.
- Idempotent reruns. Single instance via `flock`.
- `install.sh` changes keep both install modes (released and `--dev`) working.
- Never print secrets except a newly generated password once to the operator's terminal.

## Refactoring patterns

1. **Inline single-use helpers.**
2. **Delete pointless layers:** wrappers, pass-through re-exports, unused modules, empty folders.
3. **Merge cohesive files** while under 500 lines; split crowded folders into subfolders.
4. **Move behavior to its owner:** host work to `Server`, bench work to `Bench`, site work to `Site`, repository work to `App`.
5. **Tasks for long work:** move orchestration out of routes and commands into a task with steps.
6. **Explicit state over inferred state:** a stored status or record with one owner, not guesses from files.
7. **No stored derived data.**
8. **Smaller new surface:** fewer endpoints, flags and keys. Never remove or rename an existing public one.
9. **Naming pass** on every touched file, including test file names.
10. **Verbosity pass** on every touched file, unchanged lines too.
11. **Contributor slop review:** commit by commit, list anti-patterns and inflation, propose the smaller shape, refactor after approval.
