<script setup lang="ts">
import DatabaseConfigurations from '@/components/settings/DatabaseConfigurations.vue'
import DatabaseQuickActions from '@/components/settings/DatabaseQuickActions.vue'

import { DATABASE_SECTIONS } from '@/components/settings/sections'

const openSection = defineModel<{ id: string } | null>('openSection')
const configurationSection = DATABASE_SECTIONS.find((section) => section.id === 'configurations')
</script>

<template>
  <DatabaseConfigurations v-if="openSection?.id === 'configurations'" />
  <DatabaseQuickActions v-else-if="openSection?.id === 'quick-actions'" />

  <DatabaseQuickActions v-else>
    <button
      v-if="configurationSection"
      type="button"
      class="group flex justify-between items-center gap-3 py-3 text-left"
      @click="openSection = configurationSection"
    >
      <span class="flex flex-col gap-1 min-w-0">
        <span class="text-base text-ink-gray-8">{{ configurationSection.label }}</span>
        <span class="text-ink-gray-6 text-p-sm">{{ configurationSection.description }}</span>
      </span>

      <span
        class="size-4 text-ink-gray-4 group-hover:text-ink-gray-7 transition-colors lucide-chevron-right"
        aria-hidden="true"
      />
    </button>
  </DatabaseQuickActions>
</template>
