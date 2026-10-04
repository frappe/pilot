# Code review

## Before reviewing

1. Re-read `CLAUDE.md`, `SPEC.md` and `.greptile/rules.md`.
2. Confirm scope (staged, unstaged, last commit, branch vs `develop`, PR); ignore the rest.
3. Read surrounding code and Frappe source when a claim depends on it.
4. Do not edit until told "fix those", "go ahead" or "do 2,3".

## What to report

Rank `P1` (blocks merge), `P2` (should fix), `P3` (minor), each with `file:line` and a one-line failure scenario. In order:

1. **Compatibility:** breaks a CLI command or flag, a config key, an Admin API field or route, a task `command` or record, or existing benches without a patch. Breaks are P1.
2. **Correctness:** wrong behavior, lost site data, partial state on failure, unsafe retries, leaked processes, files or keys.
3. **Concurrency:** stale config writes, missing file lock or atomic write, double task submission, inconsistent lock order, long work under a lock, non-idempotent tasks ([design.md](design.md#concurrency)).
4. **Security:** missing auth checks, user input to shell or nginx config, secrets in logs or URLs, trust model violations.
5. **Consistency:** a second pattern for an existing job, or a better pattern applied in only one place.
6. **Design drift:** behavior outside its owning object, logic in CLI or routes, bench and site passed to unrelated helpers, two owners for one state, unasked knobs.
7. **Simplicity:** inflated lines, single-use helpers, wrappers, dead branches, constant tables, crowded folders. Show the shorter shape.
8. **Naming:** abbreviations, missing `is_`/`has_`, `@property` and `get_` misuse, private methods without a reason.
9. **Comments and docs:** restating or change-explaining comments, file-top comments, stale or duplicated docs, wrapped Markdown, em dashes.
10. **Tests:** failing, flaky, weakened, constant-echo, mock-heavy.

"Dont nitpick" means design and structure only. "Think like a senior engineer" means abstractions, seams and ownership.

## Settled items

Do not re-raise the [accepted risks](domain.md#accepted-risks) as they stand. Flag a change that touches one, worsens it or breaks its assumption. Record a newly accepted risk there with its assumption and revisit trigger.

## Bot and agent findings

Greptile is "not always correct".

1. Judge each finding against the code and settled items. Ask "will it happen ever" in real use.
2. Valid and in scope: smallest code fix. Prefer handling it in code over arguing.
3. Invalid or by design: no code change. Short thread reply when asked (`@greptile` via `gh`).
4. Low value but valid: a one-line comment is fine when the team says so.
5. Repeat until clean or 5/5 when asked. Report new findings caused by a fix; do not loop silently.

When the team rebuts your finding correctly, withdraw it plainly.

## Contributor reviews

1. Read commit by commit; note what inflated the line count.
2. Find design errors: two authorities, reimplemented Frappe or OS behavior, logic in the wrong layer, single-implementation layers.
3. Propose the simpler shape: delete, merge, rename, keep.
4. Refactor after approval, keeping public surfaces compatible.
5. Re-landing: keep their commits and authorship; add yours on top.

## Output

Ranked findings with `file:line`, scenario and a one or two line fix. Then "no issue found in: ..." if useful. Then what ran and its limits. No praise, no diff restatement.
