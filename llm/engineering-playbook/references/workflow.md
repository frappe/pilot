# Workflow

## Modes

| Requester says | Mode | Do |
|---|---|---|
| "just asking", "just tell", "dont change/modify" | Answer | Answer briefly. Change nothing. |
| "propose/suggest/plan first", "lets discuss" | Proposal | Propose. Wait. |
| "review", "check", "audit", "prepare list" | Review | Ranked findings. Fix only on "fix those" or "do 2,3". |
| "go ahead", "do it", "less go", "implement", "fix" | Execute | Do it. Do not re-propose ("whatever i said do it man"). |
| "keep doing yourself", "going to sleep" | Autonomous | No questions. Keep a plan file with checkboxes. Still no commit. |
| "fix in place first", "patch it, i want to test" | Live test | Patch the development bench to prove it, then code it, then revert the patch if asked. |
| "leave it", "later", "not now" | Drop | Stop. Do not raise it again this session. |

Ambiguous short request: one question, one line. Numbered answers to design questions: apply literally.

## Discover before you decide

When no solution direction is given (skip for small, obvious fixes):

1. **Investigate** with [thinking.md](thinking.md): code, docs, logs, task records. Reproduce on a development bench. Find how the codebase solves similar problems.
2. **Cross-question** in one message, one line each: goal, who is affected, scale, failure tolerance, compatibility, deadline, what was tried. Do not ask what code or defaults answer.
3. **Offer** two or three options with trade-offs; recommend one.
4. **Build** after the requester picks.

## Plan first

Before structural work, show design, assumptions, ownership, state, dependencies, error flow, risks and trade-offs. Add a small ASCII diagram when ownership or flow is unclear.

- **Object model:** say which object owns the behavior (`Server`, `Bench`, `Site`, `App`, a manager or a task) and the call shape (`bench.site("a.local").migrate()`).
- **Admin UI:** mockup of the page, dialog or form layout before building.
- **Naming:** two or three options with a pick; accept the requester's choice.
- **Large work:** plan file in `.tmp/<topic>_plan.md` or a root plan file. TODO checkboxes, ticked as done. Never committed.
- **Better idea:** say it in a few lines; do not silently build it.
- Keep plans simple: no new daemon, store, config group or task type when an existing one fits.

## Scope

- Stay in the owning layer. Ask before a core change spreads into `admin/frontend`, `install.sh` or `scripts/`.
- List unasked fixes as one-line follow-ups. Unrelated fixes get their own PR from `develop` (a worktree is fine).
- A fix owned by another repo (Frappe, Atlas, Central): ask which repo owns it; separate PR.
- Leave untracked plan files and others' staged work alone.

## Git

- Never `git stash`, `reset --hard` or checkout over others' work. Stage only your hunks.
- `git mv` for moves. `--amend` plus `push --force-with-lease` on feature branches when agreed. `reset --soft` to re-review.
- Merging `develop`: resolve surgically; adapt to develop, never drop its changes.
- Re-landing someone's PR: keep their commits, authorship and dates.
- Use `gh`. PR bodies via heredoc or file (no literal `\n`).

## Commit sequence

1. Plan the commit list (one purpose each, with subjects). Wait.
2. Stage one commit (partial staging is fine).
3. Show a per-file list: path, diff size, a few words.
4. Commit on `next`, "commit it" or "yes". On "auto commit", commit each and suggest the next.
5. Run ruff and focused tests on the staged change.
6. A hook rewrote files: stage them, commit again.
7. Confirm nothing is left unstaged. Push only when asked.

Messages:

- `type(scope): Sentence case`, short and meaningful. Types: `feat fix refactor perf test docs build chore`. Scope names the area: `auth`, `ui`, `app-embed`, `central`, `cli`, `tasks`, `migration`, `nginx`, `database`.
- No lone dependency-bump commit, no word "clarify", no security detail in the subject.
- Body only when context matters: cause and final state, under about 300 to 400 characters, no logs or history.
- `Refs:` line at the end with links to official docs, man pages, RFCs, upstream issues or good engineering posts when they explain the change. Never private links. Not counted in the body length. Reuse the links in the PR.
- No AI co-author or session line. Human co-author only on request.

## Pull requests

- Feature branch off `develop`. Never push to `develop` unless told.
- Title: Conventional Commit. First line: `Closes #<issue>`, one per linked issue (omit if none). No related-issues section.
- Bug fix: `Issue`, `Summary`, `Why`, `What changed`, optional `Screenshots`. Feature: `Summary`, `Why`, `What changed`, optional `Screenshots`.
- Short and honest: a two or three line overview, then up to about 5 bullets by importance. Link docs for detail ("keep it smaller and compact man").
- "What changed" is for the reviewer: behavior users or operators notice, CLI, config, Admin API or task changes and whether old benches work, risky areas (concurrency, security, recovery), rollout steps. Leave out file lists, renames, formatting, comment, test and doc edits, lint fixes and non-behavioral refactor steps.
- Screenshots for Admin UI changes. No validation or ops sections. Keep removals out of the title.
- On request, watch CI and bots until clean ("keep checking and make it 5/5").

## Issues

- Draft issues with the forms in `.github/ISSUE_TEMPLATE`: bug report (`fix(<scope>): ...`) or feature request (`feat(<scope>): ...`).
- Fill every required field. Reuse the issue sections in the PR.
- No real hostnames, IP addresses, tokens or customer data. Security reports go through the private security page, never a public issue.

## Tests

- Test behavior that can regress. No constant-echo tests, no mock-heavy tests that catch nothing. Delete tests with their feature. Merge scattered test files.
- "No tests for now": skip, and do not mention it again.
- Run targeted tests for narrow changes: `uv run pytest tests/<path>`. Run `uv run pytest tests/ --ignore=tests/integration` before committing broad refactors. Integration and e2e suites run in CI unless asked.
- Lint: `uv run ruff check admin pilot tests`. Frontend: Biome and a build.
- Frappe site tests only on `test.local`, never on a site with real data.
- "Dont run tests": verify by reading.
- Fix the fixture, not production code, to make a test pass. Never weaken a test to match a bug; fix the behavior.

## Live benches and hosts

Tier rules: [environments.md](environments.md). Inside an allowed tier:

- Read first. Inspect task records, logs and `bench.toml` before changing anything.
- Before destructive work (drop site, restore, revert, database tuning): take a backup, use maintenance mode, read the migration state.
- Prefer patching over recreating. Never lose site data.
- Clean up helper scripts, temporary keys, test benches and sites; say what remains.
- Config fixes go through the config model or a patch in `pilot/patches`, not hand edits.
- After Python edits, restart the Pilot processes that serve the change (Admin backend, task workers).
- Never `pkill -f "<text>"` from a shell whose command line contains that text.
- Poll at the requested interval. Report in parts.

## Debugging

- Root cause first, then the sequence of events, then the preventive fix. Measure before optimizing.
- Wrong direction or "stop it, waste of time": stop and restart from evidence.
- Handoff notes: environment, hosts, commands, evidence, current state, in one compact block.

## Reporting

- One or two lines: result, changed paths, verification. Mention once what did not run (tests, build, deploy, restart).
- Staged commit: per-file list. "What did we do": group by objective, not by file.
- Explanations: simple words, small diagram on request.
- Status and handover stay terse ("i dont need to know the reason of change"). Proposals, reviews and "why" answers show the reasoning (evidence, invariants, options, failure paths) in chat. Never in code.
