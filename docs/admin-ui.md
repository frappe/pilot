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

The Billing panel changes the site plan in the dialog. **Change plan** opens an inline plan list from `getPlanOptions`. Each plan shows its title, its price and its specs. A plan with a `note` also shows it as a tag next to the title. The note comes from the site's product in Central, such as "Up to 200 users". When Central sends an empty `subtitle`, because the product hides plan specs, the plan list and the current plan card show no specs line. A bigger plan stops the site for a few minutes, so the panel says so, calls `changePlan`, and opens the `progress_url` that Central returns. That page lives on Central and brings the user back when the site answers. **Check more plans** opens Central for custom sizes. During a trial, the plan shows as **Free trial** with the trial server size, no price and no **Change plan**. The site has no invoices yet. A notice above it shows the days left, the end date and one **Upgrade** action. Upgrade shows the paid plans from `getPlanOptions`, with the cheapest one selected. **Continue** saves the choice with `changePlan`, then the panel asks for billing details only when Central still needs them, then shows the payment step below the chosen plan and its price, with **Change plan** to go back. The billing details form picks the country from the list that `getBillingProfile` returns, in a searchable Combobox. For India it also picks the state from the GST state list, because Central checks the state against the GSTIN. Other countries enter the state as free text. The form does not ask for a currency: it sends INR for India and USD for every other country, the rule Central applies, and keeps the stored currency when Central reports `currency_locked`. The phone number has a **Code** field beside it, which picks the calling code from a list searchable by country name, ISO code or calling code, and starts at the address country. India requires the phone, because Razorpay needs one to save a card. The payment step asks for a phone only when the billing profile has none. The payment step names the gateway that collects the payment details, with its logo, and says that Frappe never sees them. Central offers Stripe for a card only in currencies where a hosted Stripe setup can be charged later. An INR team adds a card through Razorpay, because only Razorpay registers the India card mandate in this flow. Before Razorpay opens, the payment step says that Razorpay asks the customer to approve payments up to a limit, that only the monthly bill is charged, and that the ₹1 setup payment is refunded. When Central cannot verify a card, the payment step shows the reason and lets the user start again with another card. Adding a payment method converts the trial: the server moves to the chosen plan, billing for that plan starts, and full server access opens. The panel shows **Upgrading to \<plan\>** and opens the progress page when `getBilling` returns `plan_change.progress_url`. It does the same when the dialog opens while the move is still pending, for example after the card was added in a new tab. When the spending limit holds the trial, the notice offers **Choose a plan**, and a smaller plan converts it at once. See the [Cloud SDK](../admin/frontend/cloud-sdk/README.md) for the call shapes.

A paid site shows four rows, each with one action: **Next payment** (the estimated amount and due date, with a **See breakdown** link that opens the lines and the total under it), **Plan** (**Change plan**), **Pay with** (**Change card**) and **Billing details** (**Edit**, which opens the billing details form). A team with credit sees that the credit is used first. **Change card** adds a new card, then removes the old one, so the site always has a way to pay. At most one alert shows above the payment. The alert shows, in this order: a checkout that is open, with **Check payment** and **Reopen checkout**; an unpaid or overdue invoice, with **Pay**; or a reason from Central that the next charge would fail, such as no card. An unpaid invoice that also covers the team's other sites links to the Frappe Cloud billing page instead. **Invoices** lists this site's share of each monthly invoice and has a search by month, amount, status or invoice number. It shows the latest 12 invoices first, with a button for older ones, and a search covers all of them. On a phone, the status shows under the month. An invoice that covers only this site offers **Download** and, while it is open, **Pay**. An invoice that also covers the team's other sites shows only this site's share and says "Shared with your other sites".

The trial notice and Advanced say that the site is hosted on Frappe Cloud. Advanced opens Frappe Cloud for three tasks, and each link goes to that task for this site's server, never to the dashboard's list of servers. **Open server** opens the server, and is off during a trial. **Open account** opens team billing. **End subscription** opens Frappe Cloud's terminate dialog for this server, where the customer confirms it. A trial ends on its own, so Advanced shows End subscription only on a paid site. Pilot builds the links in `GET /sites/<name>/account-url`, and Central finds the server by its Pilot audience.

Billing and Advanced show only Central's data and links. On a server without Central (`[central] enabled` is off), Pilot answers the billing request at once with `This server is not connected to Frappe Cloud.` The dialog then drops those two panels and keeps the panels that call Pilot only. The SDK exposes this check as `isNotConnectedToCentral`.

## Cloud Settings Trials and Apps

A free trial shows no Site config panel and no Backups panel, because a config file reaches past the site and a backup restores on another site. Pilot also refuses these requests for a trial site, so hiding the panels is not the only control. See [Admin API](admin-api.md#server-access-lock).

| Situation | Marketplace behavior |
| --- | --- |
| The trial is open and the app is the one the trial is for (`trial.app` from Central) | **Uninstall** is disabled and says that the trial is for this app. It becomes available after the site converts. |
| The trial is open and the app is another app | **Uninstall** warns that the app's data is deleted. It does not offer backups. |
| The site is paid | **Uninstall** warns that the app's data is deleted and offers **Open Backups**, which switches to the Backups panel. |
| The uninstalled or disabled app serves the page that opened Cloud Settings (the SDK `app` option) | When the task finishes, the browser goes to `/desk`, because the page no longer exists. |

An install or uninstall card shows the app's logo and scrolls into view when it starts. An app's details card opens only after the pointer moves over the app, so a scroll alone does not open it, and a scroll closes it.

## UX Expectations

- Show the current bench and site context clearly.
- Show site skeletons only when no sites are cached; keep cached sites visible while refreshing after navigation.
- Prefer dense operational screens over marketing-style pages.
- Keep destructive actions explicit and reversible where possible.
- Show task progress and logs near the operation that started them.
- Do not hide backend errors behind generic failure messages.

## Local Work

Use the repo scripts and package metadata for exact frontend commands. After API shape changes, update the API client and run the relevant frontend checks.
