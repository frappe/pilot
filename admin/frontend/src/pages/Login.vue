<template>
  <div class="flex h-screen flex-col items-center justify-center bg-surface-base">
    <div
      class="w-full max-w-sm rounded-xl border border-outline-gray-2 bg-surface-base p-5 shadow-sm"
    >
      <h1 class="mb-4 text-center font-medium text-ink-gray-7">
        {{ session.benchName || 'Pilot' }}
      </h1>
      <div class="flex flex-col gap-4">
        <TextInput
          v-model="password"
          type="password"
          placeholder="Password"
          @keydown.enter="login"
        />
        <ErrorMessage v-if="errorMessage" :message="errorMessage" />
        <Button variant="solid" :loading="isSubmitting" class="w-full" @click="login">
          Login
        </Button>
      </div>
      <p class="mt-4 text-center text-xs text-ink-gray-4">
        Enter the password configured in
        <code class="rounded bg-surface-gray-2 px-1 font-mono">bench.toml</code>
      </p>
    </div>

    <p class="absolute bottom-6 text-xs text-ink-gray-3">Frappe Bench Administrator</p>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button, TextInput, ErrorMessage } from 'frappe-ui'
import { authApi } from '../api/auth'
import { useSession } from '../composables/useSession'
import { safeRedirect } from '../utils/redirect'

const route = useRoute()
const router = useRouter()
const { session, loadSession } = useSession()
const password = ref('')
const errorMessage = ref('')
const isSubmitting = ref(false)

async function login() {
  if (!password.value) return
  isSubmitting.value = true
  errorMessage.value = ''
  try {
    const result = await authApi.login(password.value)
    if (!result.ok) {
      errorMessage.value = result.error || 'Login failed'
      return
    }
    await loadSession()
    router.replace(safeRedirect(route.query.redirect))
  } catch (e) {
    console.error(e)
    errorMessage.value = 'Could not reach the server'
  } finally {
    isSubmitting.value = false
  }
}
</script>
