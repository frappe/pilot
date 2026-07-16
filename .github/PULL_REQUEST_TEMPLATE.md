## What and why

<!-- What changed, and the motivation/tradeoffs behind it. Detailed reasoning
belongs here or in the commit messages, not in source comments. -->

## Checklist

- [ ] Logic changes, API changes, and file moves are not combined unless they can't be separated safely.
- [ ] New/changed behavior has tests, and they pass.
- [ ] Inline comments explain only surprising constraints, invariants, or safety decisions — not what the code already says.
- [ ] Docstrings describe a non-obvious public contract or side effect, not a restatement of the signature.
- [ ] No narrated history, rejected alternatives, or review discussion left in source comments.
- [ ] `ruff check` and `mypy pilot admin/backend` are clean (see `[tool.mypy]` in `pyproject.toml`).
