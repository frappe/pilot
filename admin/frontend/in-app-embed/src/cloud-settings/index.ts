import { defineCustomElement, h } from 'vue'
import CloudSettings from './CloudSettings.vue'

const TAG = 'fc-cloud-settings'

const CloudSettingsElement = defineCustomElement({
  props: { context: Object, open: Boolean },
  emits: ['close'],
  shadowRoot: true,
  configureApp: (app) => {
    app.config.globalProperties.__ = window.__
  },
  setup: (props, { emit }) => {
    return () =>
      h(CloudSettings, {
        context: props.context,
        open: props.open,
        onClose: () => emit('close'),
      })
  },
})

if (!customElements.get(TAG)) customElements.define(TAG, CloudSettingsElement)

try {
  CSS.registerProperty({
    name: '--fui-spinner-angle',
    syntax: '<angle>',
    inherits: false,
    initialValue: '0deg',
  })
} catch {}

const host = new CloudSettingsElement()

host.addEventListener('close', () => {
  host.open = false
})

frappe.cloudSettings = {
  show: (context) => {
    if (!host.isConnected) document.body.append(host)

    host.context = context || {}
    host.open = true
  },
}
