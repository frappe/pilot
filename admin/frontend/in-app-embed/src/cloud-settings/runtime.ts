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
  inheritAttrs: false,
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

// Accessibility fixes for the embedded panel.
const trackInline = (element: HTMLElement): (() => void) => {
  const root = element.shadowRoot!
  const layers = () => [...root.querySelectorAll<HTMLElement>('[data-dismissable-layer]')]
  const controller = new AbortController()
  const { signal } = controller

  element.setAttribute('inline', '')
  element.tabIndex = 0
  element.addEventListener('keydown', (event) => {
    if (event.key === 'Tab' && !event.shiftKey && !root.activeElement) event.stopPropagation()
  }, { signal })
  document.addEventListener('pointerdown', (event) => {
    const open = layers()
    if (open.length && !open.some((layer) => event.composedPath().includes(layer))) {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    }
  }, { capture: true, signal })

  const observer = new MutationObserver(() => {
    const open = layers()
    element.toggleAttribute('overlay-open', open.length > 0)
    element.style.pointerEvents = open.some((layer) => layer.style.pointerEvents) ? 'none' : ''
  })
  observer.observe(root, { childList: true, subtree: true })
  signal.addEventListener('abort', () => observer.disconnect())
  return () => controller.abort()
}

export const mountCloudSettings = (context: CloudContext, options: CloudSettingsOptions = {}): void => {
  loadFontIfMissing()

  const existing = host ?? document.querySelector(TAG)
  existing?.dispatchEvent(new CustomEvent('close'))

  const trigger = !options.target && document.activeElement instanceof HTMLElement ? document.activeElement : undefined
  const element = document.createElement(TAG) as InstanceType<typeof CloudSettingsElement>
  element.context = context
  element.options = options
  element.open = true
  host = element

  const stopInline = options.target ? trackInline(element) : undefined

  element.addEventListener('close', () => {
    stopInline?.()
    element.open = false
    element.remove()
    if (host === element) host = undefined
    nextTick(() => { if (trigger?.isConnected) trigger.focus() })
    options.onClose?.()
  }, { once: true })

  ;(options.target ?? document.body).append(element)
}
