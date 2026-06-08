<script setup>
import { ref } from 'vue'
import { Button, TextInput, ErrorMessage } from 'frappe-ui'

defineProps({
  benchName: { type: String, default: '' },
})

const emit = defineEmits(['authenticated'])

const password = ref('')
const error = ref('')
const loading = ref(false)

async function postLogin(value) {
  const response = await fetch('/api/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password: value }),
  })
  return response.json()
}

async function login() {
  if (!password.value) return
  loading.value = true
  error.value = ''
  try {
    const result = await postLogin(password.value)
    if (result.ok) emit('authenticated')
    else error.value = result.error || 'Login failed'
  } catch {
    error.value = 'Could not reach the server'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="flex h-screen flex-col items-center justify-center bg-surface-gray-2">
    <div class="w-full max-w-sm rounded-xl border border-outline-gray-2 bg-surface-white p-5 shadow-sm">
      <h1 class="mb-4 text-center text-base font-medium text-ink-gray-7">
        {{ benchName || 'Bench Admin' }}
      </h1>
      <div class="flex flex-col gap-4">
        <TextInput
          v-model="password"
          type="password"
          placeholder="Password"
          @keydown.enter="login"
        />
        <ErrorMessage v-if="error" :message="error" />
        <Button variant="solid" :loading="loading" class="w-full" @click="login">
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
