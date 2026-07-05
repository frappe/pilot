import { computed, readonly, ref } from 'vue'
import english from './locales/en'
import simplifiedChinese from './locales/zh-CN'

const STORAGE_KEY = 'pilot.locale'
const DEFAULT_LOCALE = 'en'
const messages = { en: english, 'zh-CN': simplifiedChinese }

function detectLocale() {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved in messages) return saved
  return navigator.language.toLowerCase().startsWith('zh') ? 'zh-CN' : DEFAULT_LOCALE
}

const locale = ref(detectLocale())

function resolveMessage(key) {
  return key.split('.').reduce((value, part) => value?.[part], messages[locale.value])
    ?? key.split('.').reduce((value, part) => value?.[part], messages[DEFAULT_LOCALE])
    ?? key
}

function translate(key, params = {}) {
  let message = resolveMessage(key)
  if (message.includes('|')) {
    const choices = message.split('|').map((choice) => choice.trim())
    message = Number(params.count) === 1 ? choices[0] : choices[1]
  }
  return message.replace(/\{(\w+)\}/g, (_, name) => params[name] ?? `{${name}}`)
}

function setLocale(value) {
  if (!(value in messages)) return
  locale.value = value
  localStorage.setItem(STORAGE_KEY, value)
  document.documentElement.lang = value
}

document.documentElement.lang = locale.value

export const i18n = {
  install(app) {
    app.config.globalProperties.$t = translate
  },
}

export function useI18n() {
  return {
    locale: readonly(locale),
    localeLabel: computed(() => translate(locale.value === 'en' ? 'common.english' : 'common.chinese')),
    setLocale,
    t: translate,
  }
}
