# External marketplace registry — design

## Problem

`registry/apps_v2.json` lives inside this repo. As more people run pilot,
each install carries its own mutable copy of the registry, with no shared
source of truth and no community contribution path. We want a public,
community-editable registry (à la [frappe-brewery](https://github.com/lubusIN/frappe-brewery)),
and a way for every pilot install to consume it without re-fetching or
re-validating it excessively, and without standing up new infrastructure.

## Scope

This design covers the **client-side cache/fetch/serve mechanism** only —
how `pilot` resolves, caches, and refreshes registry data from an external
GitHub repo. Migrating the existing PR-check CI pipeline (semgrep +
`AppValidator` + `DependencyValidator`, currently gated on
`registry/apps_v2.json` changes in *this* repo) to the new external repo is
explicitly out of scope — that CI migration gets its own follow-up design.

## Approaches considered

- **A — Git clone cache (chosen).** Pilot clones the registry repo into a
  shared local cache dir. `git ls-remote` gates refresh; `git diff` against
  the clone's HEAD gates tamper detection. GitHub's existing infra is the
  CDN; nothing new to host.
- **B — Raw HTTP fetch** (`raw.githubusercontent.com/.../apps_v2.json` +
  ETag caching). Simpler artifact than a git clone, but loses
  tamper-detection for free — would need a hand-rolled checksum instead of
  `git diff`.
- **C — Dedicated query service.** A hosted API in front of the registry.
  Solves nothing the requirements call for — GitHub already serves the raw
  file fast — and is new infrastructure to build, host, and keep online
  indefinitely.

A is what the rest of this design builds on.

## Architecture

- A new public GitHub repo (community-run) holds the registry data (current
  `apps_v2.json` schema, or whatever it evolves to) — replaces
  `registry/apps_v2.json` living in this repo.
- `pilot/core/marketplace.py`'s `Marketplace` class stops reading a path
  relative to `pilot/__init__.py` and instead reads from
  `<cli_root>/registry-cache/apps_v2.json`, a git clone of the new repo.
- The registry URL is a single hardcoded constant (mirrors today's
  `_REGISTRY_V2_PATH` pattern) — no per-bench override. Marketplace is
  exclusively for published/vetted apps; custom apps already have their own
  `get-app <repo-url>` path that never touches the registry.
- Cache location: `<cli_root>/registry-cache/` — sibling to `benches/`,
  shared by every bench under this pilot install. Not per-bench, not a
  machine-global path across separate pilot installs.

## Cache mechanics

New class `pilot/core/registry_cache.py::RegistryCache`, owning the git
clone at `<cli_root>/registry-cache/`:

- **First use** (dir doesn't exist): shallow `git clone --depth 1
  <REGISTRY_URL> registry-cache`.
- **Freshness check**: a `.last_checked` timestamp file inside the cache
  dir. If `now - last_checked < 1hr`, skip the network entirely and use the
  clone as-is. Otherwise `git ls-remote` the tracked ref; if it differs from
  local HEAD, `git pull` (fast-forward only — this is a read-only mirror,
  never locally committed to). Update `.last_checked` either way — a check
  that finds "already current" still counts as checked.
- **Tamper check**: before trusting the cache (every read, cheap),
  `git status --porcelain` / `git diff --quiet`. Any dirty working tree →
  hard-stop with `BenchError`: *"registry-cache has been modified manually —
  restore it before using get-app/marketplace."* No auto-heal.
- **Offline fallback**: `git ls-remote`/`pull` failing (no network, GitHub
  down) is not fatal — log a warning, fall back to the existing local clone
  as-is. Only a first-ever clone with no network is fatal (nothing to fall
  back to).

`Marketplace.__post_init__` calls `RegistryCache().ensure_fresh()` before
reading `apps_v2.json`, replacing the current direct
`_REGISTRY_V2_PATH.read_text()`.

## Consumer changes

- `Marketplace.__post_init__` (`pilot/core/marketplace.py:127`) gains one
  line: `RegistryCache().ensure_fresh()` before reading the registry file.
  `_REGISTRY_V2_PATH` becomes `RegistryCache().apps_v2_path`, a property
  pointing into `registry-cache/` instead of this repo's `registry/`.
- `Marketplace.registry()` (the `@lru_cache` static path used by tasks
  without a `Bench`) goes through the same `RegistryCache`.
- No changes needed in `get_app.py`, `get_app_task.py`, `new_site_task.py`,
  or the `/apps/marketplace` admin route — they all go through
  `Marketplace`, so the swap is transparent.
- Errors surface as `BenchError` (consistent with the rest of the codebase)
  in three cases: tamper detected, first clone fails with no network, and a
  corrupt/unparseable `apps_v2.json` in the cache (unlikely given tamper
  detection, but worth a friendly message over a raw `JSONDecodeError`).
- This repo's own `registry/apps_v2.json`, `registry/apps.json`, and the
  migration/validation scripts (`migrate_registry.py`,
  `validate_registry_schema.py`, `check_marketplace_apps.py`,
  `marketplace-app-check.yml`) get deleted/archived from *this* repo once
  the new registry repo is live. That cutover — moving the data and
  re-pointing CI — is a one-time step, separate from the code changes here.

## Testing

`RegistryCache` gets unit tests against a local throwaway git repo (`git
init` in a tmp dir as the fake "remote"): clone, freshness-skip,
pull-on-stale, tamper-block, offline-fallback. `Marketplace` tests get a
`RegistryCache` stub/mock so they don't need real git operations, matching
how `TmpEnv`/`ModuleResolver` are already faked in the app-validator tests.
