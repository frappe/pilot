<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { Button, TabButtons, useColorScheme } from 'frappe-ui'

import { useAppMenu } from '@/components/navigation/useAppMenu'
import { useSession } from '@/composables/auth/useSession'

const router = useRouter()
const { showBenches, logout, session } = useAppMenu()
const { colorScheme, setColorScheme } = useColorScheme()
const { centralServerUrl } = useSession()

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
].filter((row) => row !== false))

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
      <a
        v-if="session.centralUrl"
        :href="centralServerUrl()"
        target="_blank"
        rel="noopener"
        class="flex items-center gap-3 px-3 py-2.5 text-ink-gray-8 hover:bg-surface-gray-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-outline-gray-3"
      >
        <span class="size-4 text-ink-gray-6 lucide-cloud" aria-hidden="true" />
        Central
        <span class="ml-auto size-4 text-ink-gray-5 lucide-external-link" aria-hidden="true" />
      </a>

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
