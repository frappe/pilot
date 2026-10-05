import { createApp } from 'vue'
import { FrappeUI } from 'frappe-ui'

import 'frappe-ui/style.css'
import '@/index.css'
import App from '@/App.vue'
import { router } from '@/router.ts'

// After an upgrade, a lazy chunk the old page asks for may be gone.
// https://vite.dev/guide/build#load-error-handling
window.addEventListener('vite:preloadError', () => window.location.reload())

const app = createApp(App)
app.use(router)
app.use(FrappeUI)

router.isReady().then(() => app.mount('#app'))
