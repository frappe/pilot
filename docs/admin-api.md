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

Every `/sites/<name>/...` route accepts the site's directory name or any hostname the site currently answers to, including custom domains and an old hostname kept through a rename. Pilot resolves the hostname to the directory name after the request is authenticated and before checking its site scope.

`GET /sites/<name>` includes `url` and `tls`. The route policy supplies the public scheme for both values.

`DELETE /sites/<name>` queues `drop-site`. Frappe takes a full backup before the drop; pass `?no_backup=1` to skip it on large sites you do not need to keep.

`GET /sites/<name>/domains` returns one row for each hostname. Each row has `domain`, `is_site`, `is_primary`, `public_scheme`, and `tls`.

`POST /sites/<name>/login` returns `{"url": ...}` plus an optional `hint` when the URL's host does not resolve on the server - the UI surfaces it so the user knows to add a hosts entry or use a `*.localhost` name.

### Renaming And Domains

`POST /sites/<name>/actions/rename` takes `{"new_name": "...", "keep_old_hostname": true}` and queues `rename-site`. The new name is validated the same way a new site's is, and both names are claimed as task resources so nothing can create or drop either while the site is moving between them. `keep_old_hostname` defaults to true and keeps the old hostname on the site, so open tabs and existing links keep working; pass false to release a pooled name a fleet reuses. A rename also replaces the site's `pilot_auth_token` with one scoped to the new name. See [Renaming without downtime](commands.md#renaming-without-downtime).

`POST /settings/admin-domain` takes `{"domain": "...", "tls": true|false}` (`tls` optional) and queues `change-admin-domain`, which registers the route with the domain provider, writes `bench.toml`, reissues the certificate when TLS is on, and republishes nginx. The previous hostname is released only once the switch has committed.

Both operations re-point any matching `central.hostname_aliases` entry, so a VM hostname keeps reaching the thing it named. Every route that claims a hostname - creating a site, renaming one, moving the admin - also takes the task resource `host:<hostname>`, so two of them cannot run at once for the same name. That key is host-wide - benches share one nginx and one `/etc/letsencrypt` - so it conflicts with active tasks on every bench of the host, not only its own.

### Site Storage

`GET /sites/storage` returns every site's `private_bytes`, `public_bytes`, `database_bytes`, and `total_bytes`, plus the `collected_at` of the reading. `database_bytes` is what the schema holds on disk, allocated-but-freed pages included, since nothing else can use that space until the tables are rebuilt.

Measuring means a `du` per site directory and one schema-size query, so the route serves `logs/site-storage.json` instead - written by the `pilot-storage` systemd timer every six hours (`pilot.core.site.storage`). Reading never measures, however old the report is; the route falls back to measuring only when there is no report at all, which is the first read on a bench whose timer has not run yet.

`POST /sites/<name>/actions/refresh-storage` queues `refresh-storage-usage` to measure again on demand. One report covers every site on the bench, so the task re-measures all of them and concurrent requests fold into one run.

### Backups And Restore

Pilot writes backup runs to `sites/<site>/backups`. Frappe prunes `private/backups` on every backup and every hour, so Pilot keeps its runs out of that directory and its retention policy is the only pruner.

`POST /sites/<name>/actions/restore` queues `restore-site`. The body has `parts`, a list of `database`, `public`, `private`, and `config`, an optional `skip_failing_patches` boolean, and one source:

| Source | Body | Notes |
|---|---|---|
| A run of a site on this bench | `source_site`, `backup_timestamp` | A run that only exists offsite is downloaded first. Omit `source_site` to use the target's own run. |
| A fresh backup of a site on this bench | `source_site` | The source site is backed up first. |
| The latest backup of a remote Frappe site | `remote_site`, `password`, `backup_timestamp` | Get `backup_timestamp` from `remote-backups`. The restore stops if the remote has a newer backup by then. To restore a newer state, take a backup on the remote site first. |
| An offsite backup of a Frappe Cloud v1 site | `frappe_cloud_backup` | Connect to Frappe Cloud first. The download links are kept out of the task record. |

A remote source needs a bench session and an `https://` site. The Administrator password is checked before the task is queued and is kept out of the task record.

`POST /sites/<name>/actions/remote-backups` takes `remote_site` and `password` and returns the remote's latest backup as `{"backups": [{"timestamp", "created_at", "parts"}]}`. Frappe exposes only its latest backup, so the list has one entry, or none when the remote has no backup from the last 30 days.

`POST /sites/<name>/actions/restore-upload` takes the same `parts` and `skip_failing_patches` (`"true"`) as multipart form fields, plus the files `database`, `public`, `private`, and `config` (the site config backup). nginx `client_max_body_size` limits the upload size.

A restore puts the site in maintenance mode, restores only the chosen parts, and migrates it. `skip_failing_patches` passes `--skip-failing` to the migration. It takes no backup of the site first. A database from another site brings that site's encryption key. The `config` part merges the source site config into this site, except the keys that belong to this site: `db_*`, `redis_*`, `pilot_*`, `atlas_*`, `rds_db`, `host_name`, `installed_apps`, `maintenance_mode`, and `pause_scheduler`. A config restored without its database keeps this site's `encryption_key`, so the passwords in this site's database stay readable. If a step fails, the site stays in maintenance mode. Restoring from another site needs a bench session; a site token can only restore its own backups.

#### Chunked uploads

The dashboard sends large backup files in chunks of up to 256 MB, because one request cannot carry them. The routes take a bench session, or a site token for its own site.

| Route | Purpose |
|---|---|
| `POST /sites/<name>/uploads` | Takes `{"files": {part: {"filename", "size"}}}`. Returns `upload_id` and `chunk_size`. Refuses files larger than the free disk space. |
| `PUT /sites/<name>/uploads/<upload_id>/files/<part>?offset=N` | The raw chunk bytes. `offset` must be the bytes received so far; a chunk sent again rewrites from its offset. A gap returns 409. Returns `received`. |
| `GET /sites/<name>/uploads/<upload_id>` | The `size` and `received` bytes of each file, to resume after an error. |
| `DELETE /sites/<name>/uploads/<upload_id>` | Cancels the upload and removes its files. |

`POST /sites/<name>/actions/restore` with `upload_id` restores the uploaded files once each one is complete. The restore then owns the files and removes them when it ends. Each new upload removes the uploads that got no chunk for 3 hours. The admin nginx vhost accepts bodies of at least 1024 MB. The chunk route streams to the admin without request buffering and without the WAF, because the WAF cannot inspect raw backup bytes.

#### Frappe Cloud v1

A site on Frappe Cloud v1 is restored after its team approves the access. All routes need a bench session. Frappe Cloud is `[frappe_cloud] url` in `common_config.toml`.

| Route | Purpose |
|---|---|
| `POST /sites/<name>/integrations/frappe-cloud` | Takes `remote_site`, any domain of the site. Returns `status`, `remote_site`, `approval_url`, and an 8-character `code`. |
| `GET /sites/<name>/integrations/frappe-cloud` | The status of the request: `Pending`, `Approved`, `Rejected`, `Revoked`, or `Expired`. |
| `DELETE /sites/<name>/integrations/frappe-cloud` | Revokes the request and deletes the token. |
| `GET /sites/<name>/integrations/frappe-cloud/backups?start=0` | Five offsite backups from `start`, newest first, and `running_backup`. |
| `POST /sites/<name>/integrations/frappe-cloud/backups` | Takes an offsite backup with files. Returns its `name`. |
| `GET /sites/<name>/integrations/frappe-cloud/backups/<backup>` | The backup `status` and the `job_url` of its job on Frappe Cloud. |

A user of the team opens `approval_url` and enters the code within 10 minutes. Five wrong codes reject the request. The approval gives access for 12 hours. Pilot keeps the token in `config/frappe_cloud/<site>.json` with mode 0600. The restore task revokes the token before it downloads the files. The download links stay valid for 24 hours.

### Site Actions

`POST /sites/<name>/actions/complete-setup` queues `complete-setup`, which runs Frappe's setup wizard without its screens. It takes `full_name` and `email`, and optionally `language` (a Frappe language name, such as English), `country`, `time_zone` and `currency`. Frappe creates the user as a System Manager, sets the site's region settings, runs each app's setup step, and skips a site whose setup is already complete. Example:

```json
{"full_name": "Asha Rao", "email": "asha@example.com", "language": "English", "country": "India", "time_zone": "Asia/Kolkata", "currency": "INR"}
```

`POST /sites/<name>/actions/build-assets` queues `build` for the apps the site runs. Assets are shared by every site on the bench that has those apps, so the task also takes the `bench:update` lock and waits for an update or another build.

### App Branches

`POST /apps/<name>/actions/switch-branch` takes `{"branch": "..."}` and queues `switch-branch`. The task validates, reinstalls, and builds the app on the new branch, and returns to the old branch if a step fails. It then backs up and migrates every site that has the app, through one migration operation that takes over the task's locks. If a migration fails, restoring that operation returns the app to its previous branch and restores the site databases.

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
