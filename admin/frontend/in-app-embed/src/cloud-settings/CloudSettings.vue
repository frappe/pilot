<script setup lang="ts">
import {
  Badge,
  SettingsContent,
  SettingsDialog,
  SettingsNavGroup,
  SettingsNavItem,
  SettingsPanel,
  SettingsSidebar,
  providePortalTarget,
} from 'frappe-ui'
import { ConfigProvider } from 'reka-ui'
import { computed, onBeforeUnmount, onMounted, provide, ref, watch } from 'vue'

import FrappeCloudLogo from './components/FrappeCloudLogo.vue'
import AdvancedPanel from './panels/AdvancedPanel.vue'
import AnalyticsPanel from './panels/AnalyticsPanel.vue'
import BackupsPanel from './panels/BackupsPanel.vue'
import BillingPanel from './panels/BillingPanel.vue'
import DomainsPanel from './panels/DomainsPanel.vue'
import MaintenancePanel from './panels/MaintenancePanel.vue'
import MarketplacePanel from './panels/MarketplacePanel.vue'
import SiteConfigPanel from './panels/SiteConfigPanel.vue'
import UsagePanel from './panels/UsagePanel.vue'
import { createStore } from './store'
import TailwindStyles from './TailwindStyles.vue'

interface Props {
  context?: Record<string, any>
  open?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  context: () => ({}),
})
const emit = defineEmits(['close'])

const GROUPS = [
  {
    tabs: [
      {
        value: 'billing',
        label: __('Billing'),
        icon: 'lucide-credit-card',
        component: BillingPanel,
      },
      {
        value: 'marketplace',
        label: __('Marketplace'),
        icon: 'lucide-store',
        component: MarketplacePanel,
      },
    ],
  },
  {
    label: __('Site'),
    tabs: [
      {
        value: 'analytics',
        label: __('Analytics'),
        icon: 'lucide-chart-line',
        component: AnalyticsPanel,
      },
      {
        value: 'domains',
        label: __('Domains'),
        icon: 'lucide-globe-code',
        component: DomainsPanel,
      },
      { value: 'backups', label: __('Backups'), icon: 'lucide-archive', component: BackupsPanel },
      { value: 'usage', label: __('Usage'), icon: 'lucide-hard-drive', component: UsagePanel },
    ],
  },
  {
    label: __('Manage'),
    tabs: [
      {
        value: 'site-config',
        label: __('Site config'),
        icon: 'lucide-file-cog',
        component: SiteConfigPanel,
      },
      {
        value: 'maintenance',
        label: __('Maintenance'),
        icon: 'lucide-wrench',
        component: MaintenancePanel,
      },
      { value: 'advanced', label: __('Advanced'), icon: 'lucide-bolt', component: AdvancedPanel },
    ],
  },
]

const TABS = GROUPS.flatMap((group) => group.tabs)

const overlays = ref(null)

provide('overlayTarget', overlays)
providePortalTarget(overlays)

const isOpen = ref(props.open)
const tab = ref(TABS[0].value)
const store = ref(createStore(props.context))

watch(
  () => props.open,
  (open) => {
    if (open) {
      store.value = createStore(props.context)
      tab.value = TABS[0].value
    }

    isOpen.value = open
  },
)

watch(isOpen, (open) => !open && emit('close'))

const isDark = ref(document.documentElement.dataset.theme === 'dark')
let themeWatcher

onMounted(() => {
  themeWatcher = new MutationObserver(() => {
    isDark.value = document.documentElement.dataset.theme === 'dark'
  })

  themeWatcher.observe(document.documentElement, {
    attributeFilter: ['data-theme'],
  })
})

onBeforeUnmount(() => themeWatcher?.disconnect())

const updateCount = computed(() => store.value.state.marketplace?.update_count || 0)
</script>

<template>
  <ConfigProvider :teleport-to="overlays">
    <SettingsDialog
      v-model:open="isOpen"
      v-model:tab="tab"
      size="5xl"
      :keyboard-shortcut="false"
      :unmount-on-hide="false"
    >
      <template #title>{{ __("Cloud Settings") }}</template>

      <SettingsSidebar class="!border-0">
        <SettingsNavGroup>
          <span class="mb-1 flex h-7 items-center px-2 text-base text-ink-gray-7">
            <FrappeCloudLogo class="mr-2 size-4 rounded-2" />
            {{ __("Cloud Settings") }}
          </span>

          <template v-for="(group, index) in GROUPS" :key="index">
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

      <SettingsContent class="bg-surface-base">
        <SettingsPanel v-for="item in TABS" :key="item.value" :value="item.value">
          <component :is="item.component" :store="store" :active="tab === item.value" />
        </SettingsPanel>
      </SettingsContent>
    </SettingsDialog>

    <div ref="overlays" :data-theme="isDark ? 'dark' : 'light'" />

    <TailwindStyles />
  </ConfigProvider>
</template>
