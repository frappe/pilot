import { ref } from 'vue'
import { defaultLanguage, languageStorageKey, languages, messages } from './messages'

const currentLanguage = ref(getInitialLanguage())

export function useI18n() {
  return {
    currentLanguage,
    languages,
    setLanguage,
    t: translate,
  }
}

export function translate(key, fallbackOrParams = {}, maybeParams = {}) {
  const fallback = typeof fallbackOrParams === 'string' ? fallbackOrParams : key
  const params = typeof fallbackOrParams === 'string' ? maybeParams : fallbackOrParams
  const text = messages[currentLanguage.value]?.[key] ?? messages[defaultLanguage][key] ?? fallback
  return Object.entries(params).reduce((value, [name, replacement]) => {
    return value.replaceAll(`{${name}}`, replacement)
  }, text)
}

export function initializeI18n() {
  document.documentElement.lang = currentLanguage.value
}

export function setLanguage(language) {
  currentLanguage.value = normalizeLanguage(language)
  document.documentElement.lang = currentLanguage.value
  localStorage.setItem(languageStorageKey, currentLanguage.value)
}

export function normalizeLanguage(language) {
  if (languages.some((item) => item.value === language)) return language
  if (language?.startsWith('zh')) return 'zh-CN'
  return defaultLanguage
}

function getInitialLanguage() {
  if (typeof localStorage === 'undefined') return defaultLanguage
  return normalizeLanguage(localStorage.getItem(languageStorageKey) || navigator.language)
}
