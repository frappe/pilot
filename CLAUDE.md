# Agent Guide

This repo is a Python CLI plus Flask Admin backend for managing Frappe benches. Prefer small, direct changes that keep the object model easy to use.

## Session Continuity

- Mandatory: Follow the [engineering playbook](llm/engineering-playbook/SKILL.md) for every task. Read `SKILL.md` at the start of a session and the references that the task needs. This file wins on conflict.
- Mandatory: After context compaction, summarization, restart, or a resumed session, read this file and `SKILL.md` again before you continue. Keep a reference to both in a handover or context summary.

## Main Rules

- Put real behavior in `pilot.core`, managers, or tasks.
- Keep CLI commands and API routes thin.
- Use `Server`, `Bench`, `Site`, and `App` as the main entry points.
- Group related files in folders instead of adding many same-prefix modules.
- Avoid lazy re-exports in package `__init__.py` when autocomplete matters.
- Keep comments short. Remove comments that restate the code.
- Do not put comments at the top of a file. Use a short, terse class or method docstring instead.
- Do not create or commit plan/planning markdown files (e.g. `plan_*.md`); keep them out of git.
- When the user states a problem without a solution direction, investigate first, ask focused questions, and offer options with a recommendation before you build.
- Follow the existing pattern for the same job. Do not add a second way. If a better way exists, change every existing use, or propose that change first.
- Make code safe when a CLI call, an Admin request and task workers act on one bench at once. Read-modify-write config under its file lock with atomic writes, keep task submission idempotent, and do not hold a lock during long work.
- Get user approval with a mockup before you add an Admin UI page or change a page, dialog or form layout.
- Keep decision-making visible. Before structural work, explain the design, assumptions, ownership, state, dependencies, error flow, risks and trade-offs. Proceed after the user agrees.
- Do not add a dependency when the standard library or an existing dependency is sufficient.
- Do not commit secrets, private keys, tokens, `.env` content, or production credentials.
- Use `git mv` when you intentionally move or rename a tracked file.

## Compatibility

Existing benches, scripts and clients must keep working after every change.

- Keep public surfaces backward compatible: CLI commands and flags, `bench.toml` and `common_config.toml` keys, the Admin API, task `command` names and records, and callbacks. Add only optional fields and flags. Do not rename, remove or retype existing ones, or change a default that users rely on.
- Do not add a new API version. Change the current contract so that old clients keep working.
- Add a patch in `pilot/patches` when stored config or state changes shape.
- When a break cannot be avoided, explain the break and the upgrade path to the user before you implement it.

## Environments

Act freely in development. Ask the user before each write in staging. Never act on production or on a resource that is not declared. See [environments and access](llm/engineering-playbook/references/environments.md).

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
- Prefer explicit config over implicit behavior.
- Prefer object-oriented code where it maps to the domain.
- Keep functions small. Around 25 lines is a useful target, not a reason to split readable code blindly.
- Keep cyclomatic complexity <= 8
- Keep files between 100 and 500 lines when practical.
- Avoid crowded modules. If a folder grows too large, group related files into a subfolder instead of adding more same-prefix files.
- Avoid abbreviations.
- Use standard APIs and existing repo helpers before adding custom logic.
- Reuse existing patterns. Write as little new code as the change needs.
- Delete before adding when existing code can be simplified.
- For Admin UI, use Frappe UI and the Espresso design system by default.
- Always add or update tests for behavior changes, and make sure they pass.
- Build the minimum working change, then iterate.
- Keep comments and docstrings terse. Explain only what the code does not already make obvious.
- Put detailed change explanation in commit messages or docs, not inline comments.
- Keep one owner for state that can drift out of sync.
- Keep state scoped. Do not let temporary state leak across object or module boundaries.
- Fail loudly near the bug. Do not hide corrupt or partial state behind broad fallbacks.
- Retry only operations that are safe to repeat.
- For a no-argument method that computes and returns one noun-like value, use `@property`, such as `nginx_version`.
- For methods with arguments or multi-step work, prefer `get_<what_it_returns>()`, such as `get_commit_sha()`.
- Default to public methods. Use a leading underscore only for raw parsing, security-sensitive validation, OS plumbing, or genuinely internal details callers should not reach for.
- Do not make a method private just because it currently has one caller.
- Do not split code into more helpers than necessary. A single-use one-liner usually reads better inline.
- Name boolean-returning properties and methods with `is_` or `has_`, such as `is_workload_running` or `has_passwordless_sudo`.
- Put blank lines between logical code blocks in a function.
- Use type hints for public functions and important data structures.
- Raise specific exceptions. Handle only errors that the code can recover from.
- Do not add generic `utils`, `helpers`, or `common` modules.
- Keep tests deterministic and independent. Test meaningful behavior and failure cases, not hard-coded constants.

## Working Rules

- Read `SPEC.md` before starting changes.
- Do not touch unrelated dirty files.
- Do not delete data directories.
- The top-level `benches/` directory is local data and must stay ignored.
- Use `apply_patch` for manual edits.
- Run `uv run ruff check admin pilot tests` after Python changes.
- Run targeted tests for narrow behavior changes and `uv run pytest` before committing broad refactors.
- For bug fixes, identify the root cause before attempting a fix.

## Validation and Handover

- Mandatory: Review every changed line before you make a commit. Remove complexity that the change introduces.
- Remove unnecessary helpers, wrappers, forwarding layers and defensive fallbacks. Keep valid error handling, boundary validation, cleanup, locking, and security checks.
- Validate untrusted input at its boundary. Use concrete types in trusted code.
- Before handover, run ruff and the relevant tests. Report targeted checks and their limits when the full suite does not run.
- In the handover, report the result, changed paths, and verification. Explain implementation details only when the user asks.

## Commits

- Never commit or push unless the user asks.
- Use a short Conventional Commit subject: `type(scope): Sentence case`. Use one of `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `build`, or `chore`. Use the area as the scope, such as `auth`, `ui`, `app-embed`, `central`, `cli`, `tasks`, or `migration`.
- Keep the body short: the cause and the final state. Do not add an AI co-author, session data, or agent data.
- When an external document explains the change (official docs, an RFC, an upstream issue or a good technical blog post), add its link on a `Refs:` line at the end of the body. Do not link private documents.

### Commit Sequence

Use this flow when a change needs more than one commit.

1. Plan the complete commit list first. Give each commit one purpose and a subject. Show the list to the user.
2. Make each commit pass ruff and its tests alone.
3. Review the diff of each commit before you stage it. Remove comments that repeat the code, and shorten verbose comments, docstrings, and documentation.
4. Stage one commit at a time. Show each staged file with its diff size and a short note, then commit when the user says `next`.
5. If a pre-commit hook changes files and stops the commit, stage the changed files and commit again.
6. After the last commit, confirm that the working tree has no unstaged change. Push only when the user asks.

## Issues

When you draft an issue, use the forms in `.github/ISSUE_TEMPLATE` (bug report or feature request) and their title format. Do not include real hostnames, IP addresses, tokens or customer data.

## Pull Requests

- Use the Conventional Commit format for the pull request title.
- Keep the description short and use ASD-STE100 Simplified Technical English. State what changed and why it matters. Do not narrate the implementation.
- Write "What changed" for the reviewer. List only changes a reviewer must know: behavior, CLI, config, Admin API or task changes, risky areas and rollout steps. Do not list file names, renames, formatting, comment edits, test or doc updates, or lint fixes unless they change behavior.
- Include screenshots for Admin UI changes only.

Put each linked issue on the first line of the description as `Closes #<issue>`, so GitHub links and closes it on merge. Omit the line when there is no issue. Do not add a separate related-issues section.

For a bug fix, use this structure:

```text
Closes #<issue>

## Issue
<One sentence that states the user-visible or operational problem.>

## Summary
<1 or 2 sentences that state the fix and why it matters.>

## Why
<The technical or business reason for this approach.>

## What changed
- <Specific change>

## Screenshots
<Before and after evidence. Omit this section when it does not apply.>
```

For a feature, use this structure:

```text
Closes #<issue>

## Summary
<1 or 2 sentences that state the capability and why it matters.>

## Why
<The technical or business reason for this approach.>

## What changed
- <Specific change>

## Screenshots
<Before and after evidence. Omit this section when it does not apply.>
```

## Docs

Keep docs concise and current. Human readers should find the workflow quickly. LLMs should find the source of truth, object boundaries, and safe edit locations without scanning long prose.

- Keep Markdown prose on one continuous line; do not hard-wrap paragraphs. Do not use em dashes.
- Use ASD-STE100 Simplified Technical English. Use short, direct sentences and one term for one thing.
- Describe current behavior only. Do not describe removed behavior or old interfaces.
- Write behavior once in `docs/`. Keep `SPEC.md` as the code contract and documentation map. Link instead of repeating.
- Update the related `docs/*.md` in the same pull request as a behavior, interface, or operation change. Skip docs for internal or expected behavior.
- Use a list or table for more than 3 related items. Give an example when a command, API or configuration value can otherwise be unclear.

## Agent Tooling

Follow the [engineering playbook](llm/engineering-playbook/SKILL.md) as described in [session continuity](#session-continuity). Use the most specific available skill for the task:

| Skill | Use for |
|---|---|
| `quality-code-review` | Correctness, security, performance, concurrency, readability, API design, and test reviews. |
| `code-style` | All code edits and code-style questions. |
| `technical-writing` | Documentation, commits, pull requests, and release notes in Simplified Technical English. |
| `ui-design` | Admin UI and UX work. |
| `grill-me` | A strict review before you hand over a change. |
| `write-pr-description` | A structured pull request description. |
