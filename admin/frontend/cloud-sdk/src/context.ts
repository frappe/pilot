import { getContext } from './api.ts'
import { CloudSettingsError } from './request.ts'
import type { CloudContext } from './types.ts'

export const getBootContext = (): CloudContext | undefined => {
  const page = globalThis as { frappe?: { boot?: { cloud_settings?: CloudContext } } }
  return page.frappe?.boot?.cloud_settings
}

/** Discover availability with or without boot data. Network failures reject. */
export const isCloudSettingsAvailable = async (context = getBootContext()): Promise<boolean> => {
  if (context) return context.enabled === true

  try {
    return (await getContext()).enabled === true
  } catch (error) {
    if (error instanceof CloudSettingsError && error.status === 403) return false
    throw error
  }
}
