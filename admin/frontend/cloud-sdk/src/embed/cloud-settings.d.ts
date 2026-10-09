import type { CloudSettingsOptions } from '../dialog.ts'
import type { CloudContext } from '../types.ts'

export function mountCloudSettings(context: CloudContext, options?: CloudSettingsOptions): void
export function closeCloudSettings(): void
