import { createApp } from 'vue'
import { FrappeUI } from 'frappe-ui'
import 'frappe-ui/style.css'
import './index.css'
import App from './App.vue'
import { router } from './router.js'
import { i18n } from './i18n'

const app = createApp(App)
app.use(router)
app.use(i18n)
app.use(FrappeUI, { resources: false, call: false, socketio: false })

router.isReady().then(() => app.mount('#app'))
