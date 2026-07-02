import { createApp } from 'vue'
import { FrappeUI } from 'frappe-ui'
import 'frappe-ui/style.css'
import './index.css'
import App from './App.vue'
import { router } from './router.js'

const app = createApp(App)
app.use(router)
app.use(FrappeUI, { resources: false, call: false, socketio: false })
app.mount('#app')
