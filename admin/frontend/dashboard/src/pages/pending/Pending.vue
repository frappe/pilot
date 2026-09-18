<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Spinner } from 'frappe-ui'

import PilotLogo from '@/components/icons/Pilot.vue'

import { authApi } from '@/api/auth'
import { useSession } from '@/composables/auth/useSession'

const POLL_MS = 5000

const router = useRouter()
const { loadSession } = useSession()
const unreachable = ref(false)
let timer: ReturnType<typeof setInterval> | undefined

// Read bootstrap directly: loadSession() reads an unreachable server as "not pending".
const poll = async () => {
  try {
    const bootstrap = await authApi.bootstrap()
    unreachable.value = false
    if (bootstrap.mode === 'pending') return
  } catch {
    unreachable.value = true
    return
  }

  await loadSession()
  router.replace({ path: '/' })
}

onMounted(() => {
  timer = setInterval(poll, POLL_MS)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div class="flex flex-col sm:justify-center items-center bg-surface-base p-4 sm:p-15 h-screen">
    <div class="flex flex-col items-start gap-5 p-6 w-full max-w-[371px]">
      <PilotLogo class="size-8" />

      <div class="flex flex-col gap-1">
        <h1 class="font-semibold text-ink-gray-9 text-lg">Waiting for configuration</h1>
        <p class="text-ink-gray-5 text-p-base">
          This machine has not been handed its settings yet. Pilot picks them up on its own and
          starts as soon as they arrive.
        </p>
      </div>

      <div class="flex items-center gap-2 text-ink-gray-5 text-p-sm">
        <Spinner class="size-4" />
        <span v-if="unreachable">Reconnecting to the server...</span>
        <span v-else>Checking every few seconds</span>
      </div>
    </div>
  </div>
</template>
