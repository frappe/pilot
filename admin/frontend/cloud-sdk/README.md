# @frappe-dev/cloud-sdk

Use Cloud Settings in a Frappe app. The SDK provides typed API calls and a ready-to-use settings dialog. It works in Desk, React, Vue, and plain browser pages.

Use the shared dialog for a consistent Cloud Settings experience across apps. If your app needs its own UI, use the API calls without the dialog.

## Requirements

- A Pilot-managed Frappe site with the Cloud Settings server APIs.
- A signed-in user with the System Manager role.
- The site's session cookie. Write calls also need `frappe.csrf_token` or `window.csrf_token`.

The server checks access on every call. The browser does not receive Pilot or Central credentials.

## Install

Build the package from `admin/frontend/cloud-sdk` with `npm run build`. Then install the built package in your app:

```sh
npm install /path/to/pilot/admin/frontend/cloud-sdk
```

The package includes the UI and its styles. You do not need to add Vue, Frappe UI, or a stylesheet to your app. The UI loads when you first open it.

## Open Cloud Settings

Show your button only when `isCloudSettingsAvailable()` returns `true`. Call `openCloudSettings()` when the user clicks it.

```ts
import { isCloudSettingsAvailable, openCloudSettings, closeCloudSettings } from '@frappe-dev/cloud-sdk'

const available = await isCloudSettingsAvailable()

async function openSettings() {
  try {
    await openCloudSettings({ panels: ['billing', 'domains', 'advanced'] })
  } catch (error) {
    // Show this message in your app's alert or error component.
    console.error(error instanceof Error ? error.message : 'Could not open Cloud Settings.')
  }
}

// Call this when the host page or component is removed.
closeCloudSettings()
```

In React, use `onClick={openSettings}`. In Vue, use `@click="openSettings"`. In a plain page, use `button.addEventListener('click', openSettings)`. Disable the button while the dialog opens.

Availability is asynchronous. It returns `false` for an unavailable site or a permission denial. A connection error rejects the call. Handle that error in your app too. Importing the package is safe during server rendering. Opening the dialog requires a browser.

## Select panels and appearance

```ts
await openCloudSettings({
  panels: ['billing', 'domains'],
  tab: 'domains',
  theme: 'auto',
  onClose: () => console.log('Cloud Settings closed'),
})
```

| Option | Purpose |
| --- | --- |
| `panels` | Select which panels to show. Omit it or use an empty array to show all. Unknown names are ignored. This option does not change permissions. |
| `tab` | Select the first panel. The default is the first visible panel. |
| `theme` | Use `auto`, `light`, or `dark`. Auto follows `html[data-theme="dark"]` or `html.dark`. |
| `translate` | Supply `(message, values?) => string`. The default uses Frappe's translation catalog and replaces numbered values. |
| `onClose` | Run a callback after the dialog closes. |
| `context` | Supply a `CloudContext`. Without it, the SDK reads the current site's context. |

Panel names: `billing`, `marketplace`, `analytics`, `domains`, `backups`, `usage`, `site-config`, `maintenance`, and `advanced`.

The dialog uses Espresso components. On a phone, it fills the viewport and has a panel selector. On a desktop, it has a sidebar. Its styles, menus, dialogs, alerts, and toasts stay inside a shadow root. Closing removes the UI and returns focus to the button. Opening again replaces the current dialog. Closing also cancels a pending open.

## Use API calls without the dialog

You can build your own pages, panels, or controls with the API calls. The `/api` import does not load the shared UI. Your app controls the layout and handles loading, empty states, errors, and success feedback.

```ts
import { getBilling, getDomains } from '@frappe-dev/cloud-sdk/api'

const billing = await getBilling()
const domains = await getDomains()
```

The root import and `/api` export the same API calls and TypeScript models. Use `/api` when you only need data.

| Area | Read calls | Action calls |
| --- | --- | --- |
| Site | `getContext`, `getAccountUrl`, `getTask` | None |
| Billing | `getBilling`, `getPlanOptions`, `getBillingProfile`, `getPaymentGateways` | Change plan, save profile, set up or remove a payment method, reconcile setup |
| Apps | `getMarketplaceApps` | Install, uninstall, update |
| Domains | `getDomains` | Read DNS records, add, remove, set primary |
| Backups | `getBackups`, `getBackupDownloadLinks` | Create, delete |
| Monitoring | `getAnalytics`, `getUptime`, `getStorage` | Refresh storage |
| Site config | `getSiteConfig` | Update config |
| Maintenance | None | Clear cache, migrate |

Requests use the current origin and session. They time out after 30 seconds. The SDK does not retry write calls.

## Handle feedback and errors

| State | Dialog behavior |
| --- | --- |
| Loading | Show a skeleton in the panel. |
| No data | Show an empty message and the next available action. |
| Load failed | Show an error in the panel with a Try again button. |
| Form or action failed | Show the error beside the form or action. |
| Action running | Show a loading button and disable conflicting actions. |
| Action complete | Refresh the data or show a short success toast. |
| Task still running | Show a background status message. Closing the UI stops polling, not the server task. |

Direct API calls and errors before the dialog opens belong to your app. Catch `CloudSettingsError` and show its `message`. It also has `status`, `serverMessages`, and `excType`. Status `0` means a network failure. Messages are plain text.

## Use the Desk bundle

Desk loads `/embed/cloud-settings/cloud-settings.js` from Pilot. It exposes `window.FrappeCloudSettings` with the same availability, open, and close methods. The existing Desk entry is `frappe.cloudSettings.show(context, options)`. An npm consumer uses the packaged UI instead.

## Build and test

Run `npm test` and `npm run build` in this package. Run `npm run build` in `admin/frontend/in-app-embed` to build the Pilot browser bundle. Build both outputs after a UI change. Refresh local file dependencies before you rebuild a consumer such as Raven.

To run browser regression tests, install the dependencies in `admin/frontend/dashboard` and `admin/frontend/in-app-embed`, then build this package. Run `npx playwright install chromium` and `npx playwright test --config cloud-settings.config.ts` from `admin/frontend/dashboard`. The tests check dialog cleanup, custom feedback translations, and task cancellation and resume. CI runs these tests in the MariaDB browser job.

To test all panels with sample data, run `python3 -m http.server 8123` in this package. Open `http://localhost:8123/tests/embed.html`. No real payment or site action runs in this fixture.

To test billing without host metadata, run this from the Pilot repository:

```sh
.venv/bin/python -m scripts.cloud_settings_simulator --bench-root /path/to/local/bench
```

Use a local development site that has the server APIs and a button to open the dialog. Set that site's `pilot_endpoint` to `http://127.0.0.1:8003`, then clear its cache. Keep its existing site token. Sign in as a System Manager and open Billing. The simulation controls are at `http://127.0.0.1:8124`. Save the sample profile, complete the local checkout, then click Check status. You can reset the data or simulate an API failure. State stays in memory. Billing makes no real charges. Other Pilot operations still use the real local bench. Stop the simulator and restore the original endpoint when finished.
