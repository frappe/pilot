<script setup lang="ts">
import { translate } from './translation'
import {
  Badge,
  Button,
  Select,
  SettingsContent,
  SettingsDialog,
  SettingsNavGroup,
  SettingsNavItem,
  SettingsPanel,
  SettingsSidebar,
  ToastProvider,
  providePortalTarget,
} from 'frappe-ui'
import { ConfigProvider } from 'reka-ui'
import { computed, onBeforeUnmount, onMounted, provide, ref, watch } from 'vue'
import type { CloudContext, CloudSettingsOptions } from '@frappe/cloud-sdk'

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
  context?: CloudContext
  open?: boolean
  options?: CloudSettingsOptions
}

const props = withDefaults(defineProps<Props>(), {
  context: () => ({ enabled: false }),
  options: () => ({}),
})
const emit = defineEmits(['close'])
const __ = props.options.translate ?? translate
provide('cloudSettingsTranslate', __)

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

// An omitted or empty selection shows every panel. Unknown names are ignored.
const visibleGroups = computed(() => {
  const allowed = new Set<string>(props.options.panels || [])
  if (!allowed.size) return GROUPS
  const groups = GROUPS.map((group) => ({
    ...group,
    tabs: group.tabs.filter((item) => allowed.has(item.value)),
  })).filter((group) => group.tabs.length)

  return groups
})
const visibleTabs = computed(() => visibleGroups.value.flatMap((group) => group.tabs))
const getFirstTab = () =>
  visibleTabs.value.find((item) => item.value === props.options.tab)?.value || visibleTabs.value[0]?.value

const overlays = ref<HTMLElement>()

provide('overlayTarget', overlays)
providePortalTarget(overlays)

const isOpen = ref(props.open)
const tab = ref(getFirstTab())
const store = ref(createStore(props.context, __))

watch(
  () => props.open,
  (open) => {
    if (open) {
      store.value = createStore(props.context, __)
      tab.value = getFirstTab()
    }

    isOpen.value = open
  },
)

watch(isOpen, (open) => !open && emit('close'))

// Desk sets `data-theme="dark"`. Tailwind apps, such as Raven, set the `dark` class.
const isPageDark = () =>
  document.documentElement.dataset.theme === 'dark' ||
  document.documentElement.classList.contains('dark')

const pageDark = ref(isPageDark())
const isDark = computed(() => props.options.theme === 'dark' || (props.options.theme !== 'light' && pageDark.value))
let themeWatcher: MutationObserver | undefined

onMounted(() => {
  themeWatcher = new MutationObserver(() => {
    pageDark.value = isPageDark()
  })

  themeWatcher.observe(document.documentElement, {
    attributeFilter: ['data-theme', 'class'],
  })
})

onBeforeUnmount(() => themeWatcher?.disconnect())

const updateCount = computed(() => store.value.state.marketplace?.update_count || 0)
</script>

<template>
  <div class="cloud-settings-root" :data-theme="isDark ? 'dark' : 'light'">
    <ConfigProvider :teleport-to="overlays">
      <div ref="overlays">
        <ToastProvider />
      </div>
      <SettingsDialog
        v-if="overlays"
        v-model:open="isOpen"
        v-model:tab="tab"
        size="5xl"
        :keyboard-shortcut="false"
        :unmount-on-hide="true"
      >
        <template #title>{{ __("Cloud Settings") }}</template>

        <Button
          class="absolute right-3 top-3 z-20"
          variant="ghost"
          icon="lucide-x"
          :aria-label="__('Close Cloud Settings')"
          @click="emit('close')"
        />

        <div class="shrink-0 border-b border-outline-gray-1 bg-surface-sidebar px-4 pb-4 pt-3 sm:hidden">
          <p class="mb-3 pr-8 text-base-semibold text-ink-gray-8">{{ __('Cloud Settings') }}</p>
          <Select
            v-model="tab"
            :label="__('Panel')"
            :options="visibleTabs.map((item) => ({ label: item.label, value: item.value }))"
          />
        </div>

        <SettingsSidebar class="cloud-settings-sidebar hidden !border-0 sm:flex">
          <SettingsNavGroup>
            <span class="mb-1 flex h-7 items-center px-2 text-base text-ink-gray-7">
              <FrappeCloudLogo class="mr-2 size-4 rounded-2" />
              {{ __("Cloud Settings") }}
            </span>

            <template v-for="(group, index) in visibleGroups" :key="index">
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

        <SettingsContent class="min-w-0 bg-surface-base">
          <SettingsPanel v-for="item in visibleTabs" :key="item.value" :value="item.value" class="min-w-0">
            <component :is="item.component" :store="store" :active="tab === item.value" />
          </SettingsPanel>
        </SettingsContent>
      </SettingsDialog>

      <TailwindStyles />
    </ConfigProvider>
  </div>
</template>
