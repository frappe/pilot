<script setup lang="ts">
import DatabaseConfigurations from '@/components/settings/DatabaseConfigurations.vue'
import DatabaseQuickActions from '@/components/settings/DatabaseQuickActions.vue'
import SettingsRow from '@/components/settings/SettingsRow.vue'

import { DATABASE_SECTIONS } from '@/components/settings/sections'

const openSection = defineModel<{ id: string } | null>('openSection')
const configurationSection = DATABASE_SECTIONS.find((section) => section.id === 'configurations')
</script>

<template>
  <DatabaseConfigurations v-if="openSection?.id === 'configurations'" />
  <DatabaseQuickActions v-else-if="openSection?.id === 'quick-actions'" />

  <template v-else>
    <section>
      <h4 class="mb-1 font-medium text-ink-gray-6">Database actions</h4>
      <DatabaseQuickActions />
    </section>

    <SettingsRow
      class="-mx-2.5 mt-2 border-t rounded border-outline-alpha-gray-1"
      as="button"
      interactive
      :label="configurationSection.label"
      :description="configurationSection.description"
      @click="openSection = configurationSection"
    >
      <span class="size-4 text-ink-gray-5 lucide-chevron-right" aria-hidden="true" />
    </SettingsRow>
  </template>
</template>
