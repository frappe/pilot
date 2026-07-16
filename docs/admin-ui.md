# Admin UI Specification

The admin frontend is a Vue 3 SPA (Vite build, [Frappe UI](https://github.com/frappe/frappe-ui) components) that talks to the backend documented in [admin-api.md](admin-api.md). It's compiled to `admin/backend/static/dist/` and served by the Flask app.

---

## Package layout

Every layer that has more than a handful of files is grouped by the resource it belongs to, so a feature's route, composable, and components live at the same relative path across layers:

```
admin/frontend/src/
├── main.js, App.vue, router.js, navigation.js
├── api/                one client module per resource (client.js is the shared ky wrapper)
├── composables/
│   ├── auth/            useSession
│   ├── benches/         useBench, useBenches
│   ├── sites/           useSite, useSites
│   ├── apps/            useAppRegistry, useAppUpdates, useMarketplace
│   ├── tasks/           useTasks, useTaskDetail, useTaskSteps, useTaskStream
│   ├── setup/           useSetup, useSetupHandoff
│   └── common/          useBreadcrumbs, useIsMobile
├── components/
│   ├── common/          cross-cutting widgets (ActionMenu, ChartCard, AppSidebar, ...)
│   ├── benches/         BenchSwitcherDialog, NewBenchDialog
│   ├── sites/           ChooseSiteDialog, NewSiteDialog, Apps, Backups, Config,
│   │                    settings/ (General, Domains, Actions, Danger, domains/*)
│   ├── apps/             AppIcon, AddAppFromGithubDialog, InstallAppDialog, UpdateAppsDialog
│   ├── marketplace/      MarketplaceAppCard, MarketplaceFilters
│   ├── tasks/             TaskStep, TaskSteps, TaskStream
│   ├── database/         SQLCodeEditor, SQLSchemaDialog
│   ├── settings/         SettingsDialog, Firewall, Git, S3Bucket, SshKeys, SystemInfo, Workers
│   ├── logs/              LogView
│   └── icons/             GithubMark
├── pages/
│   ├── auth/             Login
│   ├── dashboard/        Home, Analytics
│   ├── sites/             Sites, SiteDetail
│   ├── tasks/             Tasks, TaskDetail
│   ├── marketplace/       Marketplace
│   ├── database/          SQLPlayground
│   ├── setup/              Setup
│   └── logs/               Logs
├── layouts/               MainLayout
└── utils/                 ansi, backup, format, passwordStrength, randomPassword,
                           redirect, taskFormat, taskRoute (each with a co-located *.test.js)
```

Every source file imports siblings via the `@/` Vite alias (`@/composables/tasks/useTasks`), resolved to `src/` — never a chain of `../../..`.

## Routing

`router.js` owns three routes that don't belong in the sidebar (`/setup`, `/login`, and the two detail routes with a path param) plus every route in `navigation.js`, which doubles as both the router table and the sidebar's data source (`navigationRoutes()` flattens it for the router; `sidebarSections()` groups it for the UI). Adding a page to the sidebar and to routing is one edit, not two.

A session guard in `router.js` (`router.beforeEach`) redirects to `/setup` while the bench has no `bench.toml` yet, to `/login` while unauthenticated, and exchanges a one-time `?sid=` sign-in token before any of that runs.

## Settings modal

Settings are presented as a tabbed modal dialog (`components/settings/SettingsDialog.vue`):

| Tab | Editable fields | Read-only fields |
|-----|----------------|-----------------|
| **Bench** | HTTP Port, SocketIO Port, Default Branch | Name, Python version, Database type |
| **Appearance** | Theme (light/dark/auto) | — |
| **MariaDB** | — | Host, Port, Admin User, Socket Path |
| **Postgres** | Host, Port, Admin User, Password | Password-set indicator only (never the password itself) |
| **Redis** | Cache Port, Queue Port | Version |
| **Workers** | Queue groups and counts | — |
| **Firewall** | Enabled toggle, default action, per-IP allow/deny rules | — |
| **Nginx / HTTPS** | Enable HTTPS toggle (`admin.tls`), Let's Encrypt email | — |
| **Production** | Process Manager (none/supervisor/the host's native manager) | — |
| **S3** | Access key, secret key, bucket, provider, region | — |
| **Updates** | — | Current version, update availability; Update button |

MariaDB connection fields are read-only because the host/port/credentials/socket are fixed at `bench init` time; the database server itself isn't reconfigured by editing `bench.toml` afterward. S3 and Postgres secret fields are write-only — `GET /settings` reports only whether one is set, never its value.

Changing a restart-triggering field (ports, worker groups, process manager) regenerates config files and restarts the running process manager automatically, skipping the admin process itself so the response is delivered before the restart. The **HTTPS** toggle only records intent in `bench.toml`; call `POST /sites/{name}/actions/enable-tls` (or the equivalent settings flow) to actually run Let's Encrypt and rewrite nginx.

Theme is local to the browser (`localStorage`) and never touches `bench.toml`.

## Building and testing

```
cd admin/frontend
npm install
npm run dev      # Vite dev server with HMR, proxies API calls per vite.config.js
npm run build    # production build -> ../backend/static/dist
npm test         # node --test src/utils/*.test.js
```

`bench build-admin` (see [commands.md](commands.md)) downloads a pre-built release tarball by default, or runs this build from source with `--force`.
