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

export const closeCloudSettings = (): void => {
  host?.dispatchEvent(new CustomEvent('close'))
}

export const mountCloudSettings = (context: CloudContext, options: CloudSettingsOptions = {}): void => {
  const existing = document.querySelector(TAG)
  existing?.dispatchEvent(new CustomEvent('close'))

  const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : undefined
  const element = document.createElement(TAG) as InstanceType<typeof CloudSettingsElement>
  element.context = context
  element.options = options
  element.open = true
  host = element

  element.addEventListener('close', () => {
    element.open = false
    element.remove()
    if (host === element) host = undefined
    nextTick(() => { if (trigger?.isConnected) trigger.focus() })
    options.onClose?.()
  }, { once: true })

  document.body.append(element)
}
