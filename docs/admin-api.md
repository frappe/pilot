# Admin API

The Admin API is a Flask JSON API over the same core objects used by the CLI. Handlers should validate input, enforce auth, and delegate work.

## Layout

```text
admin/backend/api/v1/
  benches/   bench creation, readiness, support data
  setup/     first-run and database setup
  settings/  bench config read/write/apply
  sites/     site apps, backups, domains, login, config, storage
  apps.py    bench app inventory and actions
  tasks.py   task list, logs, events, control
  logs.py    log access
  notifications.py  bench notification feed and read state
  processes.py
  stats.py
  updates.py
  ssh_keys.py
  databases.py
  git.py
```

Backend provider integrations live under `admin/backend/providers`.

## Handler Rules

- Resolve `Bench`, `Site`, `Server`, or `App` early.
- Put business behavior on core objects or task classes.
- Return task ids for long work.
- Keep route helpers public when another route imports them.
- Do not import private functions across route modules.

## Auth

Admin auth code lives under the admin backend, not in route files. Routes should depend on the shared auth helpers and avoid hand-parsing credentials.

Supported auth modes include local Admin sessions and trusted remote JWKS tokens when configured in `[admin]`.

Only these routes answer without a session: `GET /health`, `GET /bootstrap`, and the three `/auth/session` methods. `GET /bootstrap` returns just `mode`, `enabled`, and `name` until the caller has one. Add `@allow_unauthenticated` only with the same kind of reason.

A `?sid=<token>` link is exchanged for a session cookie by `POST /auth/session`. Setup links (`Session.issue_setup_link_token`) live one hour and mint a 3-hour session; a password login mints the full 24 hours.

## Response Shape

Prefer small response models that match UI needs. Include stable ids, names, status, and task ids. Avoid returning raw config objects when only a few fields are needed.

Task-starting endpoints should return:

```json
{
  "task_id": "task-id",
  "created": true
}
```

`created` is useful for idempotent submissions.

### Git Branches

`GET /git/branches?repo=...` runs local `git ls-remote --heads`, so Git must be available on the Pilot host. It returns all remote branch names and puts the remote default first.

### Site Apps

`GET /sites/<name>/apps` returns the apps in use on the site, disabled ones excluded, plus `can_disable` for whether this bench's Frappe supports disabling at all.

Two app operations answer inline instead of returning a task id, because both are flag flips on data that never left the site:

- `DELETE /sites/<name>/apps/<app>?mode=disable` returns `{"app": ..., "disabled": true}`. Without the parameter the route queues an uninstall as before.
- `POST /sites/<name>/apps` for an app the site only has disabled returns `{"app": ..., "enabled": true}`. It falls through to the install queue when a required app has to be installed first.

### Site Detail And Login

`GET /sites/<name>` includes `url`, the origin the site is served on (scheme, primary host, and port derived from the bench config), which the UI uses for "Open site".

`POST /sites/<name>/login` returns `{"url": ...}` plus an optional `hint` when the URL's host does not resolve on the server - the UI surfaces it so the user knows to add a hosts entry or use a `*.localhost` name.

### Site Storage

`GET /sites/storage` returns every site's `private_bytes`, `public_bytes`, `database_bytes`, and `total_bytes`, plus the `collected_at` of the reading. `database_bytes` is what the schema holds on disk, allocated-but-freed pages included, since nothing else can use that space until the tables are rebuilt.

Measuring means a `du` per site directory and one schema-size query, so the route serves `logs/site-storage.json` instead - written by the `site-storage` systemd timer every six hours (`pilot.core.site.storage`). Reading never measures, however old the report is; the route falls back to measuring only when there is no report at all, which is the first read on a bench whose timer has not run yet.

`POST /sites/<name>/actions/refresh-storage` queues `refresh-storage-usage` to measure again on demand. One report covers every site on the bench, so the task re-measures all of them and concurrent requests fold into one run.

### Database Performance Report

`GET /database/performance-report` returns the read-only findings behind the analyzer's Query Analysis and Index Analysis panels: `time_consuming_queries`, `full_table_scan_queries`, `unused_indexes`, `redundant_indexes`, and the `performance_schema_enabled` flag.

The first three sections come from MariaDB's Performance Schema, so they are empty and the flag is `false` whenever `performance_schema` is off - the instrumentation is a startup setting, and MariaDB collects nothing until the server restarts with it on. `redundant_indexes` reads `information_schema.STATISTICS` instead and stays populated either way. The UI keys off the flag to explain the empty panels rather than reporting them as an error.

`?site=<name>` narrows every section to that site's schema; without it the report covers every user schema on the server, system schemas excluded. Only MariaDB implements it - other engines raise, and the route answers 422.

### Setup

Every `/setup/*` route needs a session, like the rest of the API. The Admin password is set when the bench is created (`pilot new`), so there is no unauthenticated window: a browser reaches the wizard through the `?sid=` link that `pilot start` prints, or by signing in with that password. `POST /benches` returns a `setup_link` token for the same purpose.

`PUT /setup/configuration` accepts only the fields the wizard owns: `app_repo`, `app_branch`, `db_type`, `db_mode`, and the `mariadb_*`/`postgres_*` connection fields. Any other key gets a 422 - including `admin_password`. Change the remaining `bench.toml` settings through the settings API.

## Errors

Raise HTTP errors at the route boundary. Core objects should raise domain exceptions such as config or bench errors.

Routes should translate known domain errors into clear HTTP status codes and messages. Unexpected errors should remain visible in logs.

## Events And Logs

Task event and log endpoints expose task runner state. The Admin UI depends on step events, final status, and streaming logs for long operations.

Do not parse task output in route handlers except through the task runner APIs.

## Adding Endpoints

1. Place the route in the closest group. 2. Add a request/response model if the shape is not trivial. 3. Resolve the domain object and delegate. 4. Queue a task for long work. 5. Add backend tests for success and error behavior.
