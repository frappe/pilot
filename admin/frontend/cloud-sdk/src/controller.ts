import { getContext } from './api.ts'
import type { CloudSettingsOptions } from './dialog.ts'
import type { CloudContext } from './types.ts'

interface Runtime {
  mountCloudSettings: (context: CloudContext, options: CloudSettingsOptions) => void
  closeCloudSettings: () => void
}

export const createCloudSettings = (load: () => Promise<Runtime>) => {
  let runtime: Runtime | undefined
  let opening = 0

  const openCloudSettings = async (options: CloudSettingsOptions = {}): Promise<void> => {
    const request = ++opening
    const context = options.context ?? (await getContext())
    if (!context.enabled) throw new Error('Cloud Settings is not available on this site.')

    runtime ??= await load()
    if (request === opening) runtime.mountCloudSettings(context, options)
  }

  const closeCloudSettings = (): void => {
    opening += 1
    runtime?.closeCloudSettings()
  }

  return { openCloudSettings, closeCloudSettings }
}
