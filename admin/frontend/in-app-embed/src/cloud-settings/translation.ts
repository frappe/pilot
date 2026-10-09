import { inject } from 'vue'
import type { CloudSettingsTranslator } from '@frappe/cloud-sdk'

export const translate: CloudSettingsTranslator = (message, values = []) => {
  const page = globalThis as {
    frappe?: { _messages?: Record<string, string>; boot?: { __messages?: Record<string, string> } }
  }
  const messages = page.frappe?._messages ?? page.frappe?.boot?.__messages
  const text = messages?.[message] ?? message
  return text.replace(/{(\d+)}/g, (placeholder, index) => String(values[Number(index)] ?? placeholder))
}

export const useTranslation = (): CloudSettingsTranslator => inject('cloudSettingsTranslate', translate)
