import { defineCustomElement, h, nextTick, type PropType } from 'vue'
import type { CloudContext, CloudSettingsOptions } from '@frappe/cloud-sdk'
import CloudSettings from './CloudSettings.vue'

const TAG = 'fc-cloud-settings'
const CloudSettingsElement = defineCustomElement({
  props: {
    context: { type: Object as PropType<CloudContext>, required: true },
    options: { type: Object as PropType<CloudSettingsOptions>, default: () => ({}) },
    open: Boolean,
  },
  emits: ['close'],
  styles: ['__CLOUD_SETTINGS_STYLES__'],
  shadowRoot: true,
  setup: (props, { emit }) => () => h(CloudSettings, {
    context: props.context,
    options: props.options,
    open: props.open,
    onClose: () => emit('close'),
  }),
})

if (!customElements.get(TAG)) customElements.define(TAG, CloudSettingsElement)

if (typeof CSS !== 'undefined' && 'registerProperty' in CSS) {
  try {
    CSS.registerProperty({
      name: '--fui-spinner-angle', syntax: '<angle>', inherits: false, initialValue: '0deg',
    })
  } catch { /* A second SDK bundle can share the registered property. */ }
}

let host: InstanceType<typeof CloudSettingsElement> | undefined
let isFontChecked = false

// Desk and most Frappe apps load Inter. Only the npm build carries the font, for a page without it.
const loadFontIfMissing = (): void => {
  if (import.meta.env.MODE !== 'sdk' || isFontChecked) return

  isFontChecked = true
  if (![...document.fonts].some((font) => /^["']?inter/i.test(font.family))) {
    import('./font').then(({ loadInter }) => loadInter())
  }
}

export const closeCloudSettings = (): void => {
  host?.dispatchEvent(new CustomEvent('close'))
}

export const mountCloudSettings = (context: CloudContext, options: CloudSettingsOptions = {}): void => {
  loadFontIfMissing()

  const existing = document.querySelector(TAG)
  existing?.dispatchEvent(new CustomEvent('close'))

  const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : undefined
  const element = document.createElement(TAG) as InstanceType<typeof CloudSettingsElement>
  element.context = context
  element.options = options
  element.open = true
  if (options.target) element.setAttribute('inline', '')
  host = element

  element.addEventListener('close', () => {
    element.open = false
    element.remove()
    if (host === element) host = undefined
    nextTick(() => { if (trigger?.isConnected) trigger.focus() })
    options.onClose?.()
  }, { once: true })

  ;(options.target ?? document.body).append(element)
}
