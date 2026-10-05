<script setup lang="ts">
import { LoadingIndicator } from 'frappe-ui'
import { onBeforeUnmount, onMounted, ref } from 'vue'

import { apiUrl } from '@/api/client'

interface Props {
  paused?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  paused: false,
})

const down = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined
let stopped = false

const pingOk = async (url: string) => {
  try {
    const response = await fetch(url, { cache: 'no-store' })
    return response.status === 200
  } catch {
    return false
  }
}

const tick = async () => {
  if (stopped) return
  if (props.paused) {
    down.value = false
    timer = setTimeout(tick, 3000)
    return
  }
  const reachable = await pingOk(apiUrl('health', window.location.origin))
  if (!down.value || reachable) {
    // Same address back: keep the page and any upload in progress.
    down.value = !reachable
  } else {
    // Reload only when the other scheme answers (TLS turned on or off).
    const port = window.location.port ? `:${window.location.port}` : ''
    const authority = `${window.location.hostname}${port}`
    const [httpsOk, httpOk] = await Promise.all([
      pingOk(apiUrl('health', `https://${authority}`)),
      pingOk(apiUrl('health', `http://${authority}`)),
    ])
    const scheme = httpsOk ? 'https' : httpOk ? 'http' : null
    if (scheme) {
      window.location.href = `${scheme}://${authority}${window.location.pathname}${window.location.search}`
      return
    }
  }
  if (!stopped) timer = setTimeout(tick, down.value ? 1500 : 3000)
}

onMounted(tick)
onBeforeUnmount(() => {
  stopped = true
  clearTimeout(timer)
})
</script>

<template>
  <div
    v-if="down && !paused"
    class="fixed inset-0 z-[9999] flex items-center justify-center gap-3 bg-surface-elevation-1"
  >
    <LoadingIndicator class="h-6 w-6 text-ink-gray-5" />
    <p class="text-xl text-ink-gray-7">Reconnecting to bench</p>
  </div>
</template>
