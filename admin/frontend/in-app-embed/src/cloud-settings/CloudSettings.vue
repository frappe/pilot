<script setup lang="ts">
import type { CloudContext, CloudSettingsOptions } from '@frappe/cloud-sdk'
import { Button, providePortalTarget, SettingsDialog } from 'frappe-ui'
import { ConfigProvider, TabsRoot } from 'reka-ui'
import { computed, defineAsyncComponent, provide, ref, watch } from 'vue'
import CloudSettingsBody from './components/CloudSettingsBody.vue'
import PanelFallback from './components/PanelFallback.vue'
import { createStore } from './store'
import TailwindStyles from './TailwindStyles.vue'
import { usePageDark } from './theme'
import { translate } from './translation'

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

// The site config editor reaches past the site, and a backup restores on another site,
// so a free trial gets neither.
const TRIAL_HIDDEN_TABS = new Set(['site-config', 'backups'])
// These panels only show Central's data and links, so a server without Central drops them.
const CENTRAL_TABS = new Set(['billing', 'advanced'])

// An omitted or empty selection shows every panel. Unknown names are ignored.
const visibleGroups = computed(() => {
  const allowed = new Set<string>(props.options.panels || [])
  const isShown = (value: string) =>
    (!allowed.size || allowed.has(value)) &&
    !(store.value.isOnTrial() && TRIAL_HIDDEN_TABS.has(value)) &&
    !(store.value.state.isNotConnectedToCentral && CENTRAL_TABS.has(value))

  return GROUPS.map((group) => ({
    ...group,
    tabs: group.tabs.filter((item) => isShown(item.value)),
  })).filter((group) => group.tabs.length)
})
const visibleTabs = computed(() => visibleGroups.value.flatMap((group) => group.tabs))
const inline = computed(() => !!props.options.target)
const getFirstTab = () =>
  visibleTabs.value.find((item) => item.value === props.options.tab)?.value ||
  visibleTabs.value[0]?.value

const overlays = ref<HTMLElement>()

const isPanelShown = (value: string) => visibleTabs.value.some((item) => item.value === value)
provide('cloudSettingsPanels', {
  isShown: isPanelShown,
  open: (value: string) => {
    if (isPanelShown(value)) tab.value = value
  },
})
provide('hostApp', props.options.app || '')

provide('overlayTarget', overlays)
providePortalTarget(overlays)

// The document sees a click inside this shadow root as a click on its host, so the dialog
// takes a click in a portaled popover, such as a Combobox list, as one outside it and closes.
const keepPopoverPointerInside = (event: PointerEvent) => {
  const target = event.composedPath()[0]
  const layer = target instanceof Element ? target.closest('[data-dismissable-layer]') : null
  if (layer && layer.getAttribute('role') !== 'dialog') event.stopPropagation()
}

// Reka's combobox looks up its list with `document`, which cannot see into this shadow root, so focus
// moving to a pressed option reads as leaving the list and closes it before the click selects.
const keepFocusOffPressedOption = (event: MouseEvent) => {
  const target = event.composedPath()[0]
  if (target instanceof Element && target.closest('[role="listbox"] [role="option"]')) event.preventDefault()
}

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

const pageDark = usePageDark()
const isDark = computed(
  () => props.options.theme === 'dark' || (props.options.theme !== 'light' && pageDark.value),
)
</script>

<template>
  <div
    class="cloud-settings-root"
    :class="inline && 'h-full'"
    :data-theme="isDark ? 'dark' : 'light'"
  >
    <ConfigProvider :teleport-to="overlays">
      <div ref="overlays" @pointerdown="keepPopoverPointerInside" @mousedown="keepFocusOffPressedOption" />
      <template v-if="overlays">
        <TabsRoot
          v-if="inline"
          v-model="tab"
          orientation="vertical"
          activation-mode="manual"
          unmount-on-hide
          class="flex h-full min-h-0 flex-col overflow-hidden sm:flex-row"
        >
          <CloudSettingsBody v-model:tab="tab" :store="store" :groups="visibleGroups" inline />
        </TabsRoot>

        <SettingsDialog
          v-else
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

          <CloudSettingsBody v-model:tab="tab" :store="store" :groups="visibleGroups" />
        </SettingsDialog>
      </template>

      <TailwindStyles />
    </ConfigProvider>
  </div>
</template>
