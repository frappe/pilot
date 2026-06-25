<script setup>
import { ref, onMounted } from 'vue'
import AppLayout from './components/AppLayout.vue'
import ConnectionGuard from './components/ConnectionGuard.vue'
import Login from './pages/Login.vue'
import Setup from './pages/Setup.vue'
import { Alert, useTheme } from 'frappe-ui'

const { initializeTheme } = useTheme()

const loading = ref(true)
const adminEnabled = ref(true)
const adminError = ref('')
const authenticated = ref(false)
const benchName = ref('')
const wizardMode = ref(false)
// Setup sets this once it has shut the wizard server down and is guiding the user
// back to the terminal, so the reconnect overlay doesn't bury that instruction.
const awaitingTerminal = ref(false)

async function loadStatus() {
  const response = await fetch('/api/status')
  const data = await response.json()
  wizardMode.value = data.wizard === true
  adminEnabled.value = data.enabled !== false
  authenticated.value = data.authenticated !== false
  benchName.value = data.name ?? ''
  if (!wizardMode.value && !adminEnabled.value) {
    adminError.value = data.error || 'Admin is disabled in bench.toml'
  }
}

async function consumeSidParam() {
  const params = new URLSearchParams(window.location.search)
  const sid = params.get('sid')
  if (!sid) return
  try {
    await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sid }),
    })
  } catch {}
  params.delete('sid')
  const query = params.toString()
  window.history.replaceState({}, document.title, window.location.pathname + (query ? `?${query}` : '') + window.location.hash)
}

onMounted(async () => {
  initializeTheme()
  await consumeSidParam()
  try {
    await loadStatus()
  } catch {
    adminError.value = 'Could not reach the bench admin server.'
  } finally {
    loading.value = false
  }
})

function onSetupDone() {
  // Bench is now initialized and admin requires a password.
  // Reload so the fresh status routes the user through the login page.
  window.location.reload()
}
</script>

<template>
  <ConnectionGuard :paused="awaitingTerminal" />
  <div v-if="loading" class="flex h-screen items-center justify-center bg-surface-gray-2" />
  <Setup v-else-if="wizardMode" @done="onSetupDone" @awaiting-terminal="awaitingTerminal = true" />
  <div v-else-if="adminError" class="flex h-screen items-center justify-center p-8">
    <Alert theme="red" title="Admin Unavailable" :description="adminError" />
  </div>
  <Login v-else-if="!authenticated" :bench-name="benchName" @authenticated="authenticated = true" />
  <AppLayout v-else @logout="authenticated = false" />
</template>
