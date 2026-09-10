# Admin UI

The Admin UI is the browser surface for benches, sites, apps, tasks, logs, and settings. It should expose operations without reimplementing backend rules.

## Layout

The Vue app lives under `admin/frontend/dashboard`. The Desk Cloud Settings in-app embed lives under `admin/frontend/in-app-embed`. Backend API routes live under `admin/backend/api/v1`.

Keep UI code organized by feature area: benches, sites, apps, tasks, logs, settings, setup, and shared utilities.

Frontend source structure:

```text
admin/frontend/dashboard/src/
  api/          endpoint wrappers and API URL/error helpers
  composables/  reusable state, loading, polling, stream, and workflow logic
  components/   reusable UI pieces grouped by feature
  pages/        route-level screens that compose components and composables
  layouts/      shared page chrome
  utils/        pure formatting and browser helpers
```

Pages should stay thin. Put fetch/mutate state in composables, HTTP details in `api`, and repeated UI in components.

## Components

Use Frappe UI by default. Think carefully before writing custom controls, overlays, menus, tables, inputs, dialogs, toasts, loading states, or empty states.

Custom UI is acceptable when Frappe UI does not provide the behavior, but keep it small and reusable. Prefer wrapping Frappe UI components over rebuilding interaction and accessibility from scratch.

Icon-only buttons pass the icon through Frappe UI `Button`'s `icon` prop. An icon `<span>` in the default slot counts as text content, and the button renders wider than it is tall.

App logos render through `components/apps/AppIcon.vue`, which wraps Frappe UI `Avatar` in its square shape. Size them with Avatar's `size` scale rather than a Tailwind size class.

## Page Chrome

The shell header exposes two teleport targets: `#header-badge` for a count or status Badge beside the breadcrumb, and `#header-actions` for page actions. Pages teleport into them, so the layout does not need to know about individual pages.

The update-status button is opt-in per route through `meta.showUpdateStatus`.

`components/common/PageHero.vue` is the dotted page header. It bleeds to the page edges and re-constrains its own content to the body column, so a page header and the content below it stay the same width.

`components/common/StickyToolbar.vue` pins a filter row under the header and sets `data-stuck` while pinned, which fades content passing beneath it.

`components/common/ToolbarSelect.vue` is the dropdown trigger used in those toolbars. Attributes fall through to the `Button`, so set width and truncation with a plain `class` on the component.

## Data Flow

- Fetch state from the Admin API.
- Start long operations through task endpoints.
- Subscribe to task status, steps, and logs.
- Refresh affected data after task callbacks or final task states.

The UI should treat task ids as the handle for long work.

## Setup

The Frappe branch picker lists the branches reported by the repository. Type a different full branch name and select `Use branch` to choose it. `Set up bench` saves the selection before setup starts. The wizard restores a saved custom branch even when the repository list does not contain it.

## Settings

Settings screens edit `bench.toml` through the backend. The frontend should not know how to rewrite TOML or infer production side effects.

Post-save changes such as restarts, firewall sync, WAF sync, or S3 credential sync belong in backend/core code.

## UX Expectations

- Show the current bench and site context clearly.
- Prefer dense operational screens over marketing-style pages.
- Keep destructive actions explicit and reversible where possible.
- Show task progress and logs near the operation that started them.
- Do not hide backend errors behind generic failure messages.

## Local Work

Use the repo scripts and package metadata for exact frontend commands. After API shape changes, update the API client and run the relevant frontend checks.
