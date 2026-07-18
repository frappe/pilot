# Agent Guide

This repo is a Python CLI plus Flask Admin backend for managing Frappe benches. Prefer small, direct changes that keep the object model easy to use.

## Main Rules

- Put real behavior in `pilot.core`, managers, or tasks.
- Keep CLI commands and API routes thin.
- Use `Server`, `Bench`, `Site`, and `App` as the main entry points.
- Group related files in folders instead of adding many same-prefix modules.
- Avoid lazy re-exports in package `__init__.py` when autocomplete matters.
- Keep comments short. Remove comments that restate the code.
- Do not create refactor planning markdown files.

## Useful Entry Points

- `pilot/core/server/__init__.py`: host-level operations.
- `pilot/core/bench/__init__.py`: bench object and bench-level operations.
- `pilot/core/site/__init__.py`: site object and site-level operations.
- `pilot/core/app/__init__.py`: app object and repository operations.
- `pilot/tasks/__init__.py`: public task API and task exports.
- `pilot/internal/cli`: argparse and command dispatch internals.
- `admin/backend/api/v1`: Admin API route groups.

## Design Expectations

Use object-owned syntax when adding features:

```python
bench = Server().bench("main")
site = bench.site("site.local")
InstallAppTask.queue(bench, site="site.local", apps=["erpnext"])
```

Avoid new APIs that pass a bench and site into unrelated helper objects when the operation can live under `bench`, `site`, `app`, or `server`.

## Code Taste

These rules are mandatory for agents changing this repo:

- Choose clean code over clever code.
- Prefer object-oriented code where it maps to the domain.
- Keep functions small. Around 10 lines is a useful target, not a reason to split readable code blindly.
- Keep files between 100 and 300 lines when practical.
- Avoid crowded modules. If a folder grows too large, group related files into a subfolder instead of adding more same-prefix files.
- Avoid abbreviations.
- Use standard APIs and existing repo helpers before adding custom logic.
- Reuse existing patterns. Write as little new code as the change needs.
- For Admin UI, use Frappe UI and the Espresso design system by default.
- Always add or update tests for behavior changes, and make sure they pass.
- Build the minimum working change, then iterate.
- Keep comments and docstrings terse. Explain only what the code does not already make obvious.
- Put detailed change explanation in commit messages or docs, not inline comments.
- For a no-argument method that computes and returns one noun-like value, use `@property`, such as `nginx_version`.
- For methods with arguments or multi-step work, prefer `get_<what_it_returns>()`, such as `get_commit_sha()`.
- Default to public methods. Use a leading underscore only for raw parsing, security-sensitive validation, OS plumbing, or genuinely internal details callers should not reach for.
- Do not make a method private just because it currently has one caller.
- Do not split code into more helpers than necessary. A single-use one-liner usually reads better inline.
- Name boolean-returning properties and methods with `is_` or `has_`, such as `is_workload_running` or `has_passwordless_sudo`.

## Working Rules

- Do not touch unrelated dirty files.
- Do not delete data directories.
- The top-level `benches/` directory is local data and must stay ignored.
- Use `apply_patch` for manual edits.
- Run `uv run ruff check admin pilot tests` after Python changes.
- Run targeted tests for narrow behavior changes and `uv run pytest` before committing broad refactors.

## Docs

Keep docs concise and current. Human readers should find the workflow quickly. LLMs should find the source of truth, object boundaries, and safe edit locations without scanning long prose.
