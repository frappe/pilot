---
name: engineering-playbook
description: Pilot team workflow and engineering judgment for code changes, refactors, reviews, design, docs, commits, PRs, and host or bench commands. Covers the Pilot CLI, core objects, tasks, Admin API, Admin UI, and Central integration.
---

# Engineering playbook

`CLAUDE.md` is the primary rulebook. This skill holds the team's working method and decisions drawn from developer sessions. Follow `CLAUDE.md` on conflict and state the conflict briefly.

Read only the references relevant to the task, fully on first use. Before a host, bench, or site command, read [environments.md](references/environments.md). Before a non-trivial change or refactor, use [thinking.md](references/thinking.md). Before handover, use [review.md](references/review.md).

| Task | Reference |
|---|---|
| Problem solving and refactoring | [thinking.md](references/thinking.md) |
| Scope, planning, git, tests, commits, PRs, handover | [workflow.md](references/workflow.md) |
| Development, staging, production access | [environments.md](references/environments.md) |
| Code and refactoring conventions | [code.md](references/code.md) |
| Architecture, state, APIs, concurrency, security | [design.md](references/design.md) |
| Failure, recovery, operations, user experience | [reliability.md](references/reliability.md) |
| Diff and review-bot findings | [review.md](references/review.md) |
| Documentation and written text | [docs.md](references/docs.md) |
| Rejected patterns and developer quotes | [anti-patterns.md](references/anti-patterns.md) |
| Pilot facts and accepted risks | [domain.md](references/domain.md) |

## Rules

1. **Terse status, complete reasoning.** Lead with the result. Keep routine replies short; give enough context for design, review, and teaching. Reasoning goes in chat, at most a compact commit body, never in code.
2. **Minimal, clear diff.** Prefer fewer lines without dense code. Avoid single-use wrappers, generic helpers, magic constant tables, extra layers, and unused knobs.
3. **Useful comments only.** Explain invariants, external quirks, and non-obvious behavior; never restate code, describe the change, or add file-top comments.
4. **Keep public surfaces compatible.** Existing benches, scripts and clients must keep working: CLI commands and flags, `bench.toml` and `common_config.toml` keys, the Admin API, task `command` names and records, and callbacks. Add optional fields and flags; do not rename, remove or retype existing ones, change relied-on defaults, or add an API version. Ship a patch in `pilot/patches` when stored config or state changes shape. Explain an unavoidable break and its upgrade path first.
5. **Discover before structural work.** Investigate and offer options when no solution is given. Show the design and wait for agreement. "Just tell", "propose first" and "dont modify" mean no edits.
6. **Never commit or push unprompted.** Stage one planned commit at a time with a per-file summary; raise a PR only when asked. Do not add an AI co-author, session trailer, or generated-with line.
7. **Use the object model.** Put behavior on `Server`, `Bench`, `Site`, `App`, managers or tasks. Keep CLI commands and API routes thin.
8. **Name precisely.** Full words, one term per concept across code, docs, API and UI. Follow Pilot's method rules in [code.md](references/code.md#naming).
9. **Fail near the cause.** Do not hide errors with broad catches or silent fallbacks. Retry only operations that are safe to repeat.
10. **Document current behavior once.** One source line per Markdown paragraph; no em dash or duplicate explanation.
11. **Verify claims.** Check the code, Frappe source, or an allowed development bench before asserting behavior.
12. **Stay in scope.** Do not alter unrelated files, another person's staged work, or another repo; never stash their work.
13. **One pattern per job.** Follow the existing pattern. Replace all uses when a better pattern fits within scope; otherwise propose a separate refactor.
14. **Design for concurrency.** Two tasks, a CLI call and an Admin request can touch one bench at once. Prevent lost config writes, double submissions, lock deadlocks and partial state.
15. **Approve UI layout first.** Show a mockup for a new Admin UI page, dialog or form layout change. Use Frappe UI and Espresso; keep it balanced and minimal.
16. **Respect environment tiers.** Development actions are delegated; ask before each staging write; never act on production or undeclared resources. Run Frappe site tests only on `test.local`.

## Pre-handover checklist

- [ ] The diff solves the request with no unrelated changes or unnecessary complexity.
- [ ] Existing benches, CLI scripts, Admin API clients and task records keep working; config shape changes have a patch.
- [ ] Concurrency is safe: config read-modify-write under the file lock, atomic writes, idempotent task submission, fixed lock order, no long work under a lock, safe retries.
- [ ] The [reliability pre-mortem](references/reliability.md#pre-mortem) covers repeat, partial failure, recovery, rollout, and user-visible states.
- [ ] The existing pattern is followed, or all affected uses are updated. Any UI layout change was approved.
- [ ] Names follow Pilot rules; logical code blocks have space; comments and docstrings carry only useful context; cyclomatic complexity is 8 or less.
- [ ] Affected `docs/*.md` and `SPEC.md` describe current behavior, with no duplicate explanation or broken links.
- [ ] `uv run ruff check admin pilot tests` and targeted tests ran (full `uv run pytest` for broad refactors), or the handover states their limits. Frontend changes pass Biome and build.
- [ ] Every command used an allowed environment. Restart the Pilot services when checking changed behavior live.
- [ ] Report the result, changed paths, verification, and material limitations. Show staged paths before a requested commit.
