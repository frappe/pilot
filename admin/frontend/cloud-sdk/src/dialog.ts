import { createCloudSettings } from './controller.ts'
import type { CloudContext } from './types.ts'

export type CloudSettingsPanel =
  | 'billing'
  | 'marketplace'
  | 'analytics'
  | 'domains'
  | 'backups'
  | 'usage'
  | 'site-config'
  | 'maintenance'
  | 'advanced'

export type CloudSettingsTranslator = (message: string, values?: unknown[]) => string

export interface CloudSettingsOptions {
  panels?: CloudSettingsPanel[]
  tab?: CloudSettingsPanel
  theme?: 'light' | 'dark' | 'auto'
  translate?: CloudSettingsTranslator
  onClose?: () => void
  context?: CloudContext
}

export { isCloudSettingsAvailable } from './context.ts'

export const { openCloudSettings, closeCloudSettings } = createCloudSettings(
  () => import('./embed/cloud-settings.js'),
)
