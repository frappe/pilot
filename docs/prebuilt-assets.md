# Prebuilt Assets

An app can build its JS, CSS, and SPA files in its own GitHub CI and publish them for each commit. Pilot then downloads the files for the exact commit that it deploys and does not build them on the server. Builds are the slowest step of an install or update, and they use the most memory, so this makes deploys faster and safer on small servers.

Apps that publish nothing continue to work. Pilot builds their assets on the server.

## How It Works

1. A push to a branch of the app starts the app's assets workflow.
2. The workflow builds the app against the matching Frappe branch.
3. The workflow uploads `<app>-<commit>.tar.gz` and `<app>-<commit>.tar.gz.sha256` to the GitHub release `assets-<branch>` of the app. A `/` in the branch name becomes `-`.
4. The workflow deletes the files of older commits, as the [retention inputs](#keep-or-delete-old-assets) set.
5. When Pilot installs or updates the app, it downloads the archive for the checked-out commit, checks the checksum, and puts the files in place.

The release `assets-<branch>` is a pre-release that holds files only. It is not a version release of the app. Do not edit it.

## Set Up an App

Do these steps in the app repository.

### 1. Add the Workflow

Add `.github/workflows/assets.yml`:

```yaml
name: Assets

on:
  push:
    branches: [develop, version-16, version-16-hotfix]

jobs:
  assets:
    uses: frappe/pilot/.github/workflows/app-assets.yml@develop
    permissions:
      contents: write
```

List in `branches` each branch that benches install. The workflow needs `contents: write` to create the release and upload files.

### 2. Declare Each SPA

Do this step only when the app builds a SPA, such as a Vue frontend. Apps that have only the esbuild bundles in `<app>/public/dist` need no setting.

Declare where each SPA is built, in `pyproject.toml`. These are the keys that Frappe Cloud also reads, so an app that runs on Frappe Cloud can have them already. Paths are relative to the repository root.

| Key | Value |
|---|---|
| `build_dir` | The directory that has the SPA's `package.json`. |
| `out_dir` | The directory where the SPA build writes its files. |
| `index_html_path` | The HTML file that the SPA build writes. |

An app with one SPA uses one table:

```toml
[tool.bench.assets]
build_dir = "./frontend"
out_dir = "./gameplan/public/frontend"
index_html_path = "./gameplan/www/g.html"
```

An app with more than one SPA uses one table for each SPA:

```toml
[[tool.bench.assets]]
build_dir = "./frontend"
out_dir = "./hrms/public/frontend"
index_html_path = "./hrms/www/hrms.html"

[[tool.bench.assets]]
build_dir = "./roster"
out_dir = "./hrms/public/roster"
index_html_path = "./hrms/www/roster.html"
```

The workflow fails when the app's `package.json` has a `build` script and the app declares no SPA. Without this check, the published files would not include the SPA.

### 3. Push and Check

Push to one of the branches. When the workflow is complete, the release `assets-<branch>` of the app shows the two files for the commit.

On a bench, install or update the app. The task log shows `Downloading prebuilt assets for <app> at <commit>`.

## Workflow Inputs

Set inputs under `with:` in the job.

| Input | Default | Use |
|---|---|---|
| `keep-untagged` | `30` | The number of newest untagged commits that keep their assets. At least 1. |
| `keep-tagged` | `0` | The number of newest tagged commits that keep their assets. `0` keeps all of them. |
| `frappe-branch` | From the branch | The Frappe branch to build against. `version-16` and `version-16-hotfix` build against `version-16`, and all other branches build against `develop`. Set this input when the branch name does not tell the Frappe branch. |

```yaml
jobs:
  assets:
    uses: frappe/pilot/.github/workflows/app-assets.yml@develop
    with:
      keep-untagged: 10
      keep-tagged: 0
      frappe-branch: version-16
    permissions:
      contents: write
```

## Keep or Delete Old Assets

Each push adds the files of one commit. After each upload, the workflow deletes the files of older commits. It counts tagged and untagged commits apart. A commit is tagged when a git tag points to it.

A bench that runs a commit without published assets builds them on the server. It does not fail.

| Branch | Suggested setting | Reason |
|---|---|---|
| A release branch, such as `version-16` | The defaults | Benches run tagged releases for a long time, so `keep-tagged: 0` keeps all of them. |
| A busy branch without releases, such as `develop` | `keep-untagged: 5` to `10` | Benches on this branch usually run a recent commit. |
| A branch that a bench follows at its head | `keep-untagged` larger than the number of pushes between two updates | A bench that updates once a week needs the commit it updated to. |

GitHub allows 1000 files in one release, which is 500 commits. When a release branch gets near 500 tagged releases, set `keep-tagged`.

When you tag a commit after its push, its files stay from then on, if the workflow did not delete them before.

## When Pilot Uses Prebuilt Assets

| Operation | Prebuilt assets |
|---|---|
| Install an app (`pilot get-app`, Admin) | Yes |
| Update the bench or an app | Yes |
| Switch an app's branch | Yes |
| Create a site when an app has no built assets yet | Yes |
| `pilot build` | Yes |
| `pilot build --force` | No, always builds |
| Admin **Build assets** for a site or the bench | No, always builds |

Pilot also builds on the server when the app has local changes in git, because the published files do not include those changes.

## Download Results

| Result | Pilot does |
|---|---|
| No archive for the commit | Builds on the server. The log shows `No prebuilt assets for <app> at <commit>`. |
| The download fails | Builds on the server. The log shows the reason. |
| The archive matches its checksum and its commit | Replaces each published path whole, so files that the new build removed do not stay. |
| The checksum, the commit, or a path in the archive is wrong | Stops with an error and keeps the current files. A wrong archive is not hidden behind a build. |

## Test a Build Locally

The workflow runs this script. Run it to test an app before you push:

```bash
git clone https://github.com/frappe/pilot
pilot/scripts/build-app-assets.sh <app checkout> <frappe branch> <output dir>
```

It needs git, Python 3.11 or later, Node.js 24, and yarn. It writes the archive and its checksum to the output directory.

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| CI error `has a build script but declares no [tool.bench.assets]` | The app builds a SPA that it did not declare. | [Declare each SPA](#2-declare-each-spa). |
| CI error `the build did not produce <path>` | `out_dir` or `index_html_path` is not where the SPA build writes. | Correct the path in `pyproject.toml`. |
| CI upload fails with 403 | The job has no write permission. | Add `permissions: contents: write` to the job. |
| `No prebuilt assets for <app> at <commit>` | The workflow did not run for this commit, it is still running, or it deleted the files. | Add the branch to `on.push.branches`, wait for the workflow, or increase `keep-untagged`. |
| `do not match their checksum` or `were built for another commit` | The files on the release are damaged or were replaced. | Run the workflow again for the commit. To continue now, run `pilot build --force`. |

## Limits

- Only public GitHub repositories are supported.
- Pilot looks up the release by the branch that is checked out. An app on a detached HEAD is built on the server.
