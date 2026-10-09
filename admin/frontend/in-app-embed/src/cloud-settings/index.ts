import type { CloudContext, CloudSettingsOptions } from '@frappe/cloud-sdk'
import { mountCloudSettings, closeCloudSettings } from './runtime'
import { isCloudSettingsAvailable } from '../../../cloud-sdk/src/context'
import { createCloudSettings } from '../../../cloud-sdk/src/controller'

const controller = createCloudSettings(async () => ({ mountCloudSettings, closeCloudSettings }))

const page = globalThis as typeof globalThis & {
  FrappeCloudSettings?: object
  frappe?: { cloudSettings?: { show: (context: CloudContext, options?: CloudSettingsOptions) => void } }
}

page.FrappeCloudSettings = { isCloudSettingsAvailable, ...controller }

if (page.frappe) page.frappe.cloudSettings = {
  show: (context, options = {}) => mountCloudSettings(context, options),
}
