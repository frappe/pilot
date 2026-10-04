# How to think through a change

Use for any non-trivial fix, feature or refactor, and for discovery. Show the reasoning in chat (proposal and review), never in code or docs. A short cause can go in the commit body.

## Method

Answer each in a few lines before changing code:

1. **Problem and evidence:** what is wrong, for whom, how you know (error, task log, request, measurement). Say if you cannot reproduce it.
2. **Trace the path:** CLI command or route to task to core object to every file, process and database it touches. Name each state owner (`bench.toml`, operation record, task store) and every caller.
3. **Invariants:** what must stay true (one writer per file, backup before migrate, old CLI scripts work, a failed operation is recoverable).
4. **Two candidate fixes:** lines, owners, public surfaces, concurrency and failure behavior, fit with existing patterns. Pick one; say why the other loses.
5. **Failure and rollback:** crash halfway, runs twice, old config, how to undo.
6. **Falsifiable verification:** a test or live check that would fail if you are wrong. Run it.

Present 1, 3, 4 and 5 compactly; wait for approval if structural.

## Refactor method

1. **Pin behavior:** what callers rely on (returns, errors, side effects, CLI output, API shapes, task records) and how you will check it.
2. **Find all callers:** commands, routes, tasks, scripts, `install.sh`, the Admin UI, tests.
3. **Choose the owner:** the object that holds the state, not where it is called.
4. **Keep public surfaces:** CLI, config, Admin API and task names stay compatible; internal names may change.
5. **Small steps:** rename, move, then simplify; each step passes ruff and tests.
6. **Remove the old way** everywhere, or propose that separately.
7. **Verify** with the step 1 checks.

## Example: app update and site migration

- **Problem:** one large update task that fails midway leaves sites half migrated, and the task exit loses the context a person needs to recover.
- **Path:** Admin action or CLI → update task → app pull, dependency install, asset build → per-site backup and migrate. State lives in task logs only.
- **Invariants:** every site is backed up before it migrates; no writes while the bench is partly updated; a failure is visible later and recoverable; illegal state jumps are impossible.
- **Option A (rejected):** add retries and a longer timeout to the single task. A crash still loses the workflow, and retries repeat unsafe steps.
- **Option B (chosen):** a durable `MigrationOperation` record owns the workflow, small tasks each do one job, and a state machine picks the next task and rejects illegal transitions. Sites enter maintenance mode first; failures stop in `needs_attention` for a person to retry or revert.
- **Failure:** a worker crash leaves the saved operation; resume re-enters the unfinished state. Revert restores apps and site databases from checkpoints.
- **Lesson:** find the state owner, name the invariants, and make the recovery path part of the design instead of adding retries.
