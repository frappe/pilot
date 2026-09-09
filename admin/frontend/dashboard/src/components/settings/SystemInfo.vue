<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { Spinner } from 'frappe-ui'

import { monitorApi } from '@/api/monitor'
import { formatBytes } from '@/utils/format'

const loading = ref(true)
const info = ref({ disk_total: 0, runtime: {} })

const systemRows = computed(() => {
  const rows = {
    OS: info.value.os_version || '',
    Kernel: info.value.kernel_version || '',
    vCPUs: info.value.cpu_count || '',
    RAM: info.value.memory_total ? formatBytes(info.value.memory_total) : '',
    Swap: info.value.swap_total ? formatBytes(info.value.swap_total) : '',
    'Disk size': info.value.disk_total ? formatBytes(info.value.disk_total) : '',
  }
  return Object.fromEntries(Object.entries(rows).filter(([, value]) => value))
})

onMounted(async () => {
  try {
    info.value = await monitorApi.systemInfo()
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <Spinner size="lg" class="text-ink-gray-4" />
  </div>

  <div v-else class="space-y-6">
    <div>
      <p class="mb-1 font-medium text-ink-gray-5 text-xs uppercase tracking-wide">System</p>
      <div class="divide-y divide-outline-alpha-gray-1">
        <div
          v-for="(value, label) in systemRows"
          :key="label"
          class="flex justify-between items-center py-2.5"
        >
          <span class="text-ink-gray-7">{{ label }}</span>
          <span>{{ value }}</span>
        </div>
      </div>
    </div>

    <div>
      <p class="mb-1 font-medium text-ink-gray-5 text-xs uppercase tracking-wide">Runtime</p>
      <div class="divide-y divide-outline-alpha-gray-1">
        <div
          v-for="(value, label) in info.runtime"
          :key="label"
          class="flex justify-between items-center py-2.5"
        >
          <span class="text-ink-gray-7">{{ label }}</span>
          <span>{{ value }}</span>
        </div>

        <p v-if="!Object.keys(info.runtime).length" class="py-2.5 text-ink-gray-5 text-p-sm">
          No runtime versions detected.
        </p>
      </div>
    </div>
  </div>
</template>
