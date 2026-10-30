<script lang="ts">
import { ref } from 'vue'
import type { SystemInfo } from '@/types/stats'

const info = ref<SystemInfo | null>(null)
</script>

<script setup lang="ts">
import { ErrorMessage, Skeleton } from 'frappe-ui'
import { computed, onMounted } from 'vue'

import { monitorApi } from '@/api/monitor'
import Version from '@/components/settings/Version.vue'
import { errorMessage } from '@/utils/error'
import { formatBytes } from '@/utils/format'

const error = ref('')

const hardware = computed(() => {
  if (!info.value) return []

  const tiles = [
    {
      label: 'vCPUs',
      icon: 'lucide-cpu',
      value: info.value.cpu_count ? String(info.value.cpu_count) : '',
    },
    {
      label: 'Memory',
      icon: 'lucide-memory-stick',
      value: info.value.memory_total ? formatBytes(info.value.memory_total) : '',
    },
    {
      label: 'Swap',
      icon: 'lucide-layers',
      value: info.value.swap_total ? formatBytes(info.value.swap_total) : '',
    },
    {
      label: 'Disk',
      icon: 'lucide-hard-drive',
      value: info.value.disk_total ? formatBytes(info.value.disk_total) : '',
    },
  ]

  return tiles
    .filter((tile) => tile.value)
    .map((tile) => {
      const [amount, unit = ''] = tile.value.split(' ')

      return { ...tile, amount, unit }
    })
})

const platform = computed(() =>
  [info.value?.os_version, info.value?.kernel_version && `kernel ${info.value.kernel_version}`]
    .filter(Boolean)
    .join(' · '),
)

const runtime = computed(() =>
  Object.entries(info.value?.runtime ?? {}).map(([label, value]) => ({ label, value })),
)

onMounted(async () => {
  if (info.value) return

  try {
    info.value = await monitorApi.systemInfo()
  } catch (e) {
    error.value = errorMessage(e, 'Could not load system info.')
  }
})
</script>

<template>
  <div class="flex flex-col gap-5">
    <div class="pb-5 border-b border-outline-alpha-gray-1">
      <Version :platform="platform" />
    </div>

    <ErrorMessage v-if="error" :message="error" />

    <template v-else>
      <section>
        <h4 class="mb-3 text-base-medium text-ink-gray-8">Server</h4>

        <div class="gap-3 grid grid-cols-2 sm:grid-cols-4">
          <template v-if="!info">
            <Skeleton v-for="tile in 4" :key="tile" class="rounded-7 h-16" />
          </template>

          <div
            v-for="tile in hardware"
            v-else
            :key="tile.label"
            class="flex items-center gap-3 bg-surface-gray-1 p-3 rounded-7"
          >
            <span
              class="place-items-center grid bg-surface-gray-3 dark:bg-surface-gray-2 rounded-5 size-10 shrink-0"
            >
              <span :class="tile.icon" class="size-5 text-ink-gray-8" />
            </span>

            <div class="flex flex-col gap-0.5 min-w-0">
              <span class="text-ink-gray-5 text-sm">{{ tile.label }}</span>

              <span class="flex items-baseline gap-1 tabular-nums">
                <span class="text-xl-semibold text-ink-gray-9">{{ tile.amount }}</span>
                <span v-if="tile.unit" class="text-ink-gray-5 text-sm">{{ tile.unit }}</span>
              </span>
            </div>
          </div>
        </div>
      </section>

      <section class="pt-5 border-t border-outline-alpha-gray-1">
        <h4 class="mb-1 text-base-medium text-ink-gray-8">Runtime</h4>

        <div class="gap-x-10 grid sm:grid-cols-2">
          <template v-if="!info">
            <div v-for="item in 6" :key="item" class="flex justify-between items-center h-7">
              <Skeleton class="rounded-1 w-20 h-3" />
              <Skeleton class="rounded-1 w-12 h-3" />
            </div>
          </template>

          <div
            v-for="item in runtime"
            v-else
            :key="item.label"
            class="flex justify-between items-center gap-4 h-7 text-base"
          >
            <span class="text-ink-gray-5">{{ item.label }}</span>
            <span class="tabular-nums text-ink-gray-8 truncate">{{ item.value }}</span>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>
