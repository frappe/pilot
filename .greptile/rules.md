# Greptile Review Rules

## Mandatory

- [Mandatory] When a test file is modified alongside a bug fix, flag if the test was weakened, loosened, or changed to match the new behavior instead of the behavior being fixed to satisfy the original test's intent.
- [Mandatory] Flag behavior changes that ship without added or updated tests.
- [Mandatory] Flag behavior or interface changes that don't update the relevant docs/*.md file(s); docs should stay current with the change in the same PR.
- [Mandatory] Flag cyclomatic complexity above 8.
- [Mandatory] Flag comments that explain what changed or why a change was made (that belongs in the commit message/PR description, not the code). Inline comments are only for cases absolutely necessary to explain non-obvious human-facing behavior, must be simple and well-written, and better to have a reference (e.g. a blog post or doc link) where one is available.
- [Mandatory] Flag any comment placed at the start of a file.

## Architecture and placement

- Real behavior belongs in pilot.core, managers, or tasks. Flag CLI commands or API routes that contain logic instead of delegating to those layers.
- Flag new code that passes a bench and site into an unrelated helper object instead of adding the operation to Server, Bench, Site, or App.
- Flag state that has more than one owner, or temporary state that leaks across object/module boundaries.
- For Admin UI changes, flag components that don't use Frappe UI and the Espresso design system.

## Structure and size

- Flag new same-prefix modules added to an already-crowded folder; group related files into a subfolder instead.
- Flag lazy re-exports added to a package __init__.py.
- Flag functions much longer than ~25 lines when they could be reasonably split.
- Flag files that grow past ~500 lines without being split.
- Flag plan/planning markdown files (e.g. plan_*.md) committed to the repo.

## Less code, reuse over new code

- Less code is better: flag diffs that could reasonably be smaller or that add code where deleting or simplifying existing code would solve the problem.
- Flag custom logic that duplicates a standard library API or an existing repo helper (e.g. pilot.internal.git.GitRepo for git plumbing).
- Flag new code that could reuse or simplify existing patterns instead of adding new ones.

## Error handling

- Flag broad try/except or fallback logic that hides corrupt or partial state instead of failing near the bug.
- Flag retries added around operations that are not safe to repeat.

## Naming conventions

- Flag unnecessary abbreviations in identifiers.
- Flag no-argument methods that compute and return one value without using @property (e.g. should be nginx_version, not get_nginx_version()).
- Flag multi-step or argument-taking methods named as properties instead of get_<noun>() style, e.g. get_commit_sha().
- Flag methods made private (leading underscore) without being raw parsing, security-sensitive validation, OS plumbing, or genuinely internal — and flag methods made private just because they currently have one caller.
- Flag boolean-returning properties or methods not named with an is_ or has_ prefix.
- Flag comments that restate the code (a short class/method docstring should be used instead for non-obvious behavior).
