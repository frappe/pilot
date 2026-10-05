# @frappe-dev/cloud-sdk

API client for the Frappe Cloud site settings in a Frappe app. It calls the `frappe.integrations.frappe_providers.cloud_settings` methods on the current site

## Example
```ts
import { changePlan, getBilling } from '@frappe-dev/cloud-sdk'

const billing = await getBilling()
await changePlan('pro')
```

The client sends requests with the session cookie and reads the CSRF token from `frappe.csrf_token` (Desk) or `window.csrf_token` (frappe-ui apps).

A failed call throws `CloudSettingsError`:

| Field | Value |
|---|---|
| `message` | Server messages without HTML, or an English fallback |
| `serverMessages` | Server messages, empty when the server sent none |
| `status` | HTTP status |
| `excType` | Frappe exception type, such as `ValidationError` |

