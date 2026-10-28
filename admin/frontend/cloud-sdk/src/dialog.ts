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

/** The ES runtime Pilot serves next to Desk's script, so the UI follows Pilot updates. */
export const getRuntimeUrl = (context: CloudContext): string => {
  const script = context.bundle?.js
  if (!script) throw new Error('Cloud Settings is not set up on this site.')

  return new URL('esm/cloud-settings.js', new URL(script, globalThis.location?.href)).href
}

const loadRuntime = async (context: CloudContext) => {
  const url = getRuntimeUrl(context)

  try {
    return await import(/* @vite-ignore */ /* webpackIgnore: true */ url)
  } catch (cause) {
    throw new Error(
      'Could not load Cloud Settings. Check that this site can reach its Pilot server.',
      { cause },
    )
  }
}

export const { openCloudSettings, closeCloudSettings } = createCloudSettings(loadRuntime)
