# App Dependencies

Every app on a bench shares one Python environment and one process. Frappe imports all installed
apps into the same interpreter, so a package like `markdown` exists there at exactly one version, no
matter how many apps want it.

That single fact drives everything below. An app cannot get its own copy of a library, so two apps
demanding different versions is not a packaging problem to route around - it is a conflict someone
has to resolve.

## What an app must declare

```toml
[project]
dependencies = ["markdown>=3.5,<4"]        # PyPI packages

[tool.bench.frappe-dependencies]
frappe = ">=16.0.0,<17.0.0"                # frappe apps, with the versions supported
erpnext = ">=16.0.0,<17.0.0"
```

`[tool.bench.frappe-dependencies]` is required for every app except frappe itself, and every entry
needs a real version range. An empty or missing range is rejected: pilot cannot tell whether the app
fits the bench without one.

Prefer ranges over exact pins. `markdown==3.8.2` says no other app on the bench may ever need a
different version - which is a claim about other people's apps, not just your own.

## What Pilot checks, and when

Every check runs on every path - `get-app`, `update`/migration, and `switch-branch`. A revision can
drop a `pyproject.toml` or a declaration as easily as it can break a hook, so an app that has moved
is held to the same standard as one being installed.

Apart from the file patterns in [Ignoring files](#ignoring-files), there is no way past the
checks: no flag, no override, on any path, including the marketplace dependencies pilot installs on
an app's behalf. An app that cannot pass them cannot get onto a bench, so everything installed is
known to have passed.

Validating on update matters most for resolution: the reinstall that follows runs `uv pip install`
with no constraints, so it resolves only that app's own requirements and will move a package
another app pinned without complaint.

### Frappe compatibility

`[tool.bench.frappe-dependencies]` is compared against the versions actually installed. An app that
declares nothing is skipped, so a legacy app still updates.

Version comparison follows PEP 440, which excludes pre-releases from an exclusive upper bound: a
bench running frappe `develop` reports `17.0.0-dev`, and that does **not** satisfy `<17.0.0`. An app
that declares it stops at 16 is held to it rather than silently running on 17.

### Resolution

Pilot runs the real install command with `--dry-run`, carrying every other app's declared
requirements as a constraints file. uv then answers two questions at once:

- can this app's own requirements be satisfied at all?
- can they be satisfied **without moving a package another app pinned**?

Without the constraints, `uv pip install -e app` reports success while quietly replacing
`markdown 3.5.2` with `3.8.2`, and the app that required the old one keeps running until something
touches the changed API. With them, uv refuses instead.

A constraints file only binds packages the resolve actually touches, so an app that never mentions
`markdown` is unaffected by a `markdown` conflict elsewhere on the bench.

Only requirements carrying extras are left out, because a constraints file may not name them.
URL requirements stay in, and this matters: uv refuses to resolve any tree containing a URL
requirement unless it is pinned as a direct requirement or a constraint, and frappe pins two
(`pypika` and `gunicorn`). Leaving them out failed every app that depends on frappe. Environment
markers stay in too, and travel with the line they belong to.

### Imports

Imports are resolved without running the app's code, in three stages, stopping at the first answer:

1. **Stat what the bench already has** - the app's own package, the other apps' source trees, and
   the environment's `site-packages`. Sub-second, and enough for almost every app.
2. **Ask the bench's Python** about third-party names with no file to stat. A package can bind
   submodules when it is imported: `apiclient.discovery` is an alias for a `googleapiclient` module
   and has no file of its own. App modules never reach this stage - their files are right there, and
   importing them would run the code being validated.
3. **Install into a throwaway venv** when something is still missing, which is the case that
   genuinely needs installing - a dependency the bench does not have yet.

### Ignoring files

An app can exclude paths from the file-walking checks - syntax, imports, fixtures, and symlinks:

```toml
[tool.bench]
validation-ignore = ["atlas/internal/*", "atlas/generated/**"]
```

Patterns are globs relative to the app root, and `*` crosses directory separators, so
`atlas/internal/*` excludes that whole subtree. This is for generated or vendored code the app does
not own. Nothing else is skippable: the declaration, compatibility, and resolution checks apply to
the app as a whole and have no override.

## Reading a failure

```
'wiki' has dependencies that can't be resolved against this bench:
  × No solution found when resolving dependencies:
  ╰─▶ Because wiki==3.0.0 depends on markdown==3.8.2 and markdown>=3.5.1,<3.6.dev0,
      we can conclude that your requirements are unsatisfiable.
Already required by:
  markdown~=3.5.1  # lms
Widen the requirement in pyproject.toml, or update the app that pinned it.
```

uv explains the arithmetic; the `Already required by` block names which app asked for what. Three
ways out, in order of preference:

1. **Widen the range** in whichever app is stricter than it needs to be. Usually the pin was written
   by `pip freeze` and reflects the version the author tested with, not a real incompatibility.
2. **Update the other app**, if a newer release already relaxed its requirement.
3. **Separate benches**, when the two apps genuinely need incompatible versions. No tooling can put
   two versions of one package in one interpreter.

There is no flag to install anyway. A conflict has to be resolved, not deferred - moving it to
runtime only means finding out during a migration instead.

## Limits

- An app that declares no requirements constrains nothing, and cannot be protected.
- Compatibility is only as good as what apps declare. A range nobody maintains says nothing.
- Imports inside functions and inside `try` blocks are skipped: they are lazy and often deliberately
  optional.
