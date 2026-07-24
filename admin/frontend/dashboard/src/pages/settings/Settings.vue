<script setup>
import { computed } from 'vue'
import { Button, TabButtons, useTheme } from 'frappe-ui'
import { useAppMenu } from '@/components/navigation/useAppMenu'

const { showSettings, showBenches, logout, session } = useAppMenu()
const { currentTheme, setTheme } = useTheme()

const themeOptions = computed(() => [
  {
    icon: 'lucide-monitor',
    active: currentTheme.value === 'system',
    onClick: () => setTheme('system'),
  },
  { icon: 'lucide-sun', active: currentTheme.value === 'light', onClick: () => setTheme('light') },
  { icon: 'lucide-moon', active: currentTheme.value === 'dark', onClick: () => setTheme('dark') },
])
</script>

<template>
  <div class="mx-auto max-w-3xl">
    <div
      class="flex flex-col divide-y divide-outline-gray-1 rounded-lg border border-outline-gray-1"
    >
      <div class="flex items-center gap-3 px-3 py-2.5  text-ink-gray-8">
        <span class="size-4 text-ink-gray-6 lucide-cloud" />
        Central
      </div>

      <Button
        variant="ghost"
        class="w-full !h-auto !justify-between !px-3 !py-2.5"
        @click="showSettings = true"
      >
        <span class="flex items-center gap-3">
          <span class="size-4 text-ink-gray-6 lucide-server-cog" />
          Server settings
        </span>
        <template #suffix><span class="size-4 text-ink-gray-5 lucide-chevron-right" /></template>
      </Button>

      <Button
        v-if="session.allowBenchManagement"
        variant="ghost"
        class="w-full !h-auto !justify-between !px-3 !py-2.5"
        @click="showBenches = true"
      >
        <span class="flex items-center gap-3">
          <span class="size-4 text-ink-gray-6 lucide-repeat" />
          Switch Bench
        </span>
        <template #suffix><span class="size-4 text-ink-gray-5 lucide-chevron-right" /></template>
      </Button>

      <div class="flex items-center justify-between gap-3 px-3 py-2.5">
        <span class="flex items-center gap-3 text-ink-gray-8">
          <span class="size-4 text-ink-gray-6 lucide-sun-moon" />
          Theme
        </span>
        <TabButtons :options="themeOptions" />
      </div>

      <Button variant="ghost" class="w-full !h-auto !justify-between !px-3 !py-2.5" @click="logout">
        <span class="flex items-center gap-3">
          <span class="size-4 text-ink-gray-6 lucide-log-out" />
          Logout
        </span>
        <template #suffix><span class="size-4 text-ink-gray-5 lucide-chevron-right" /></template>
      </Button>
    </div>
  </div>
</template>
