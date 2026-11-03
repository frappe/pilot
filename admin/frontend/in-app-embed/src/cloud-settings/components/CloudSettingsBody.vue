<script setup lang="ts">
import {
  Badge,
  Select,
  SettingsContent,
  SettingsNavGroup,
  SettingsNavItem,
  SettingsPanel,
  SettingsSidebar,
} from 'frappe-ui'
import { type Component, computed } from 'vue'

import type { Store } from '../store'
import { useTranslation } from '../translation'
import FrappeCloudLogo from './FrappeCloudLogo.vue'

interface Tab {
  value: string
  label: string
  icon: string
  component: Component
}

interface Props {
  store: Store
  groups: { label?: string; tabs: Tab[] }[]
  inline?: boolean
}

const props = defineProps<Props>()

const tab = defineModel<string>('tab')
const __ = useTranslation()
const tabs = computed(() => props.groups.flatMap((group) => group.tabs))
const showNav = computed(() => !props.inline || tabs.value.length > 1)
const updateCount = computed(() => props.store.state.marketplace?.update_count || 0)
</script>

<template>
  <template v-if="showNav">
    <div
      class="shrink-0 border-b border-outline-gray-1 bg-surface-sidebar px-4 pb-4 pt-3 sm:hidden"
    >
      <p class="mb-3 text-base-semibold text-ink-gray-8" :class="!inline && 'pr-12'">
        {{ __('Cloud Settings') }}
      </p>
      <Select
        v-model="tab"
        :label="__('Panel')"
        :options="tabs.map((item) => ({ label: item.label, value: item.value }))"
      />
    </div>

    <SettingsSidebar class="cloud-settings-sidebar hidden !border-0 sm:flex">
      <SettingsNavGroup>
        <span class="mb-1 flex h-7 items-center px-2 text-base text-ink-gray-7">
          <FrappeCloudLogo class="mr-2 size-4 rounded-2" />
          {{ __("Cloud Settings") }}
        </span>

        <template v-for="(group, index) in groups" :key="index">
          <span
            v-if="group.label"
            class="mt-1.5 flex h-7 items-center px-2 text-sm-medium text-ink-gray-5"
          >
            {{ group.label }}
          </span>

          <SettingsNavItem
            v-for="(item, position) in group.tabs"
            :key="item.value"
            :class="index && !group.label && !position && 'mt-1.5'"
            :value="item.value"
          >
            <template #prefix>
              <span :class="[item.icon, 'size-4 shrink-0 text-ink-gray-6']" />
            </template>
            {{ item.label }}

            <template #suffix>
              <Badge
                v-if="item.value === 'marketplace' && updateCount"
                theme="gray"
                :label="String(updateCount)"
              />
            </template>
          </SettingsNavItem>
        </template>
      </SettingsNavGroup>
    </SettingsSidebar>
  </template>

  <SettingsContent class="min-w-0 bg-surface-base">
    <p v-if="!tabs.length" class="p-8 text-p-base text-ink-gray-6">
      {{ __('Not in the free trial. Upgrade to use it.') }}
    </p>
    <SettingsPanel v-for="item in tabs" :key="item.value" :value="item.value" class="min-w-0">
      <component :is="item.component" :store="store" :active="tab === item.value" />
    </SettingsPanel>
  </SettingsContent>
</template>
