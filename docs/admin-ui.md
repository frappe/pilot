# Admin UI

The Admin UI is the browser surface for benches, sites, apps, tasks, logs, and settings. It should expose operations without reimplementing backend rules.

## Layout

The Vue app lives under `admin/frontend/dashboard`. The shared Cloud Settings dialog lives under `admin/frontend/in-app-embed`. The [Cloud SDK](../admin/frontend/cloud-sdk/README.md) packages its APIs and a loader for Vue, React, and plain browser pages. The loader imports the UI from Pilot at runtime, so a Pilot update reaches every app. Backend API routes live under `admin/backend/api/v1`.

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

For an external database, a blank port uses the bench's configured MariaDB or PostgreSQL port (3310 or 5450 by default), shown as the field's placeholder.

## Site Navigation

**Visit site** opens the site's Desk URL using the browser's current site session. It does not sign you in or switch users, and shows no loading or success toast. **Login as admin** explicitly creates an admin login session and can replace an existing normal-user session for that site. These actions are available in the sites dropdown, with matching labels on the site detail page.

## Settings

Settings screens edit `bench.toml` through the backend. The frontend should not know how to rewrite TOML or infer production side effects.

Post-save changes such as restarts, firewall sync, WAF sync, or S3 credential sync belong in backend/core code.

## Cloud Settings Billing

The Billing panel changes the site plan in the dialog. **Change plan** opens an inline plan list from `getPlanOptions`. A bigger plan stops the site for a few minutes, so the panel says so, calls `changePlan`, and opens the `progress_url` that Central returns. That page lives on Central and brings the user back when the site answers. **Check more plans** opens Central for custom sizes. During a trial, the plan shows as **Trial plan** with no price and no **Change plan**. A notice above it shows the days left, the end date and one **Upgrade** action. Upgrade shows the paid plans from `getPlanOptions`, with the cheapest one selected. **Continue** saves the choice with `changePlan`, then the panel asks for billing details only when Central still needs them, then shows the payment step below the chosen plan and its price, with **Change plan** to go back. The billing details form picks the country from the list that `getBillingProfile` returns. For India it also picks the state from the GST state list, because Central checks the state against the GSTIN. Other countries enter the state as free text. Central offers Stripe for a card only in currencies where a hosted Stripe setup can be charged later. An INR team adds a card through Razorpay, because only Razorpay registers the India card mandate in this flow. Before Razorpay opens, the payment step says that Razorpay asks the customer to approve payments up to a limit, that only the monthly bill is charged, and that the ₹1 setup payment is refunded. When Central cannot verify a card, the payment step shows the reason and lets the user start again with another card. Adding a payment method converts the trial: the server moves to the chosen plan, billing for that plan starts, and full server access opens. The panel shows **Upgrading to \<plan\>** and opens the progress page when `getBilling` returns `plan_change.progress_url`. It does the same when the dialog opens while the move is still pending, for example after the card was added in a new tab. When the spending limit holds the trial, the notice offers **Choose a plan**, and a smaller plan converts it at once. See the [Cloud SDK](../admin/frontend/cloud-sdk/README.md) for the call shapes.

Billing and Advanced show only Central's data and links. On a server without Central (`[central] enabled` is off), Pilot answers the billing request at once with `This server is not connected to Frappe Cloud.` The dialog then drops those two panels and keeps the panels that call Pilot only. The SDK exposes this check as `isNotConnectedToCentral`.

## UX Expectations

- Show the current bench and site context clearly.
- Show site skeletons only when no sites are cached; keep cached sites visible while refreshing after navigation.
- Prefer dense operational screens over marketing-style pages.
- Keep destructive actions explicit and reversible where possible.
- Show task progress and logs near the operation that started them.
- Do not hide backend errors behind generic failure messages.

## Local Work

Use the repo scripts and package metadata for exact frontend commands. After API shape changes, update the API client and run the relevant frontend checks.
