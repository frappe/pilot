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
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, provide, ref, watch } from 'vue'
import type { CloudContext, CloudSettingsOptions } from '@frappe/cloud-sdk'

import FrappeCloudLogo from './components/FrappeCloudLogo.vue'
import PanelFallback from './components/PanelFallback.vue'
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

// Each panel loads when it is first shown, so opening the dialog fetches only the active panel.
const lazyPanel = (loader: Parameters<typeof defineAsyncComponent>[0]) =>
  defineAsyncComponent({
    loader,
    loadingComponent: PanelFallback,
    errorComponent: PanelFallback,
    delay: 150,
    onError: (_error, retry, fail, attempts) => (attempts < 3 ? retry() : fail()),
  })

const BillingPanel = lazyPanel(() => import('./panels/BillingPanel.vue'))
const MarketplacePanel = lazyPanel(() => import('./panels/MarketplacePanel.vue'))
const AnalyticsPanel = lazyPanel(() => import('./panels/AnalyticsPanel.vue'))
const DomainsPanel = lazyPanel(() => import('./panels/DomainsPanel.vue'))
const BackupsPanel = lazyPanel(() => import('./panels/BackupsPanel.vue'))
const UsagePanel = lazyPanel(() => import('./panels/UsagePanel.vue'))
const SiteConfigPanel = lazyPanel(() => import('./panels/SiteConfigPanel.vue'))
const MaintenancePanel = lazyPanel(() => import('./panels/MaintenancePanel.vue'))
const AdvancedPanel = lazyPanel(() => import('./panels/AdvancedPanel.vue'))

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

const store = ref(createStore(props.context, __))
if (props.open) store.value.loadBilling()

// The site config editor reaches past the site, so a free trial site does not get it.
const TRIAL_HIDDEN_TABS = new Set(['site-config'])

// An omitted or empty selection shows every panel. Unknown names are ignored.
const visibleGroups = computed(() => {
  const allowed = new Set<string>(props.options.panels || [])
  const isShown = (value: string) =>
    (!allowed.size || allowed.has(value)) && !(store.value.isOnTrial() && TRIAL_HIDDEN_TABS.has(value))

  return GROUPS.map((group) => ({
    ...group,
    tabs: group.tabs.filter((item) => isShown(item.value)),
  })).filter((group) => group.tabs.length)
})
const visibleTabs = computed(() => visibleGroups.value.flatMap((group) => group.tabs))
const getFirstTab = () =>
  visibleTabs.value.find((item) => item.value === props.options.tab)?.value || visibleTabs.value[0]?.value

const overlays = ref<HTMLElement>()

provide('overlayTarget', overlays)
providePortalTarget(overlays)

const isOpen = ref(props.open)
const tab = ref(getFirstTab())

watch(
  () => props.open,
  (open) => {
    if (open) {
      store.value = createStore(props.context, __)
      tab.value = getFirstTab()
      store.value.loadBilling()
    }

    isOpen.value = open
  },
)

watch(isOpen, (open) => !open && emit('close'))

// A panel that the trial hides may be the one asked for, so fall back to the first one shown.
watch(visibleTabs, (tabs) => {
  if (!tabs.some((item) => item.value === tab.value)) tab.value = getFirstTab()
})

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
          class="cloud-settings-close absolute right-3 top-3 z-20 max-sm:size-10"
          variant="ghost"
          icon="lucide-x"
          :aria-label="__('Close Cloud Settings')"
          @click="emit('close')"
        />

        <div class="shrink-0 border-b border-outline-gray-1 bg-surface-sidebar px-4 pb-4 pt-3 sm:hidden">
          <p class="mb-3 pr-12 text-base-semibold text-ink-gray-8">{{ __('Cloud Settings') }}</p>
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
          <p v-if="!visibleTabs.length" class="p-8 text-p-base text-ink-gray-6">
            {{ __('Not in the free trial. Upgrade to use it.') }}
          </p>
          <SettingsPanel v-for="item in visibleTabs" :key="item.value" :value="item.value" class="min-w-0">
            <component :is="item.component" :store="store" :active="tab === item.value" />
          </SettingsPanel>
        </SettingsContent>
      </SettingsDialog>

      <TailwindStyles />
    </ConfigProvider>
  </div>
</template>
