import './index.css'
import './scope'

import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from '@/App.vue'
import { useTheme } from '@/composables/useTheme'

useTheme().apply()

const app = createApp(App)

app.use(createPinia())
app.mount('#app')
