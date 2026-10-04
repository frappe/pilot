# Docs and written text

Covers `docs/*.md`, `SPEC.md`, READMEs, UI text, commit and PR text. Use `technical-writing` or `ste100-writer` for wording.

## Style

- ASD-STE100: short sentences, active voice, present tense, one term per thing, no idioms.
- One paragraph per source line; never hard-wrap. Fix wrapped paragraphs in any file you touch.
- No em or en dashes; avoid semicolon chains.
- Small paragraphs. Lists or tables for more than three items.
- Plain words; explain external terms on first use.
- Sentence-case headings that answer a reader question.

## What to write

- Current behavior only. No history, "previously" or trace of old code.
- Skip internal or expected behavior.
- One sentence per fact; no rationale paragraphs.
- Do not over-trim: keep the mechanism engineers need.
- Explain why a design exists when readers would get it wrong (migration operation vs task, trust model).
- User guides say what the reader does, not internals.
- No PII in public text: names, emails, customer hostnames or IPs.

## Where docs live

- **`docs/`:** behavior, commands, configuration, tasks, migrations, Admin API and UI, production. Written once.
- **`SPEC.md`:** object model, public surfaces, trust model, config groups, task model, and the documentation map. A router, not an explanation.
- **README:** install and a few lines that link to both.
- Update the matching `docs/*.md` in the same PR as a behavior or interface change.
- No duplication. Split multi-topic docs; merge thin ones.

## Comments

See [code.md](code.md#comments-and-docstrings).

## Commit and PR text

See [workflow.md](workflow.md#commit-sequence) and [pull requests](workflow.md#pull-requests).

## Plans and reports

Plan files (`.tmp/*_plan.md`, `plan_*.md`) are temporary, use checkboxes, skip doc rules and are never committed. Activity reports: bullets, verified PR links, grouped by project.
