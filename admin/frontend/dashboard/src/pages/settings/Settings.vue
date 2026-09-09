<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { Button, TabButtons, useColorScheme } from 'frappe-ui'

import { useAppMenu } from '@/components/navigation/useAppMenu'

const router = useRouter()
const { showBenches, logout, session } = useAppMenu()
const { colorScheme, setColorScheme } = useColorScheme()

const themeModel = computed({
  get: () => colorScheme.value,
  set: setColorScheme,
})

const menuRows = computed(() => [
  {
    icon: 'lucide-server-cog',
    label: 'Server settings',
    onClick: () => router.push({ name: 'Settings' }),
  },
  session.allowBenchManagement && {
    icon: 'lucide-repeat',
    label: 'Switch Bench',
    onClick: () => (showBenches.value = true),
  },
  {
    icon: 'lucide-history',
    label: 'Activity',
    onClick: () => router.push({ name: 'Activity' }),
  },
].filter(Boolean))

const themeOptions = [
  { value: 'system', label: 'System', icon: 'lucide-monitor' },
  { value: 'light', label: 'Light', icon: 'lucide-sun' },
  { value: 'dark', label: 'Dark', icon: 'lucide-moon' },
]
</script>

<template>
  <div class="p-3 md:p-4 mx-auto max-w-3xl">
    <div
      class="flex flex-col divide-y divide-outline-gray-1 rounded-6 border border-outline-gray-1"
    >
      <div class="flex items-center gap-3 px-3 py-2.5  text-ink-gray-8">
        <span class="size-4 text-ink-gray-6 lucide-cloud" />
        Central
      </div>

      <Button
        v-for="row in menuRows"
        :key="row.label"
        variant="ghost"
        class="w-full !h-auto !justify-between !px-3 !py-2.5"
        @click="row.onClick"
      >
        <span class="flex items-center gap-3">
          <span class="size-4 text-ink-gray-6" :class="row.icon" />
          {{ row.label }}
        </span>

        <template #suffix><span class="size-4 text-ink-gray-5 lucide-chevron-right" /></template>
      </Button>

      <div class="flex items-center justify-between gap-3 px-3 py-2.5">
        <span class="flex items-center gap-3 text-ink-gray-8">
          <span class="size-4 text-ink-gray-6 lucide-sun-moon" />
          Theme
        </span>

        <TabButtons v-model="themeModel" :options="themeOptions" />
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
