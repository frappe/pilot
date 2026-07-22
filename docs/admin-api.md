# Admin API

The Admin API is a Flask JSON API over the same core objects used by the CLI. Handlers should validate input, enforce auth, and delegate work.

## Layout

```text
admin/backend/api/v1/
  benches/   bench creation, readiness, support data
  setup/     first-run and database setup
  settings/  bench config read/write/apply
  sites/     site apps, backups, domains, login, config
  apps.py    bench app inventory and actions
  tasks.py   task list, logs, events, control
  logs.py    log access
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

## Errors

Raise HTTP errors at the route boundary. Core objects should raise domain exceptions such as config or bench errors.

Routes should translate known domain errors into clear HTTP status codes and messages. Unexpected errors should remain visible in logs.

## Events And Logs

Task event and log endpoints expose task runner state. The Admin UI depends on step events, final status, and streaming logs for long operations.

Do not parse task output in route handlers except through the task runner APIs.

## Adding Endpoints

1. Place the route in the closest group. 2. Add a request/response model if the shape is not trivial. 3. Resolve the domain object and delegate. 4. Queue a task for long work. 5. Add backend tests for success and error behavior.
