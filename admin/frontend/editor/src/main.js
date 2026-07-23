import './index.css'
import './scope'

import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import { useTheme } from './composables/useTheme'

useTheme().apply()

const app = createApp(App)
app.use(createPinia())
app.mount('#app')
