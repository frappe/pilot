<script setup lang="ts">
import { useTranslation } from '../translation'
import { Button, SettingsRow } from 'frappe-ui'
import { computed, watch } from 'vue'
import Panel from '../components/Panel.vue'
import { openExternal } from '../external'
import type { Store } from '../store'

const __ = useTranslation()

interface Props {
  store: Store
  active?: boolean
}

const props = defineProps<Props>()

watch(
  () => props.active,
  (active) => {
    if (active) props.store.loadAccountLinks()
  },
  { immediate: true },
)

type Row = { title: string; description: string; label?: string; url?: string; disabled?: boolean }

// Each button opens one task for this site in Frappe Cloud, so nobody starts at its list of servers.
const rows = computed<Row[]>(() => {
  const links = props.store.state.accountLinks
  const isTrial = props.store.isOnTrial()

  const rows: Row[] = [
    {
      title: __('Server on Frappe Cloud'),
      description: isTrial
        ? __('Server access comes with a paid plan. Upgrade to use it.')
        : __('Deploys, scaling, SSH, console and snapshots.'),
      label: __('Open server'),
      url: links?.server,
      disabled: isTrial,
    },
    {
      title: __('Frappe Cloud account'),
      description: __('Team members, payment methods, all invoices and account settings.'),
      label: __('Open account'),
      url: links?.billing,
    },
  ]

  // A trial ends on its own, so only a paid site has a subscription to end.
  if (!isTrial)
    rows.push({
      title: __('End subscription'),
      description: __('Cancel your plan and delete this site. You confirm it in Frappe Cloud.'),
      label: __('End subscription'),
      url: links?.end_subscription,
    })

  return rows
})
</script>

<template>
  <Panel
    :title="__('Advanced')"
    :description="__('Your site runs on Frappe Cloud. The Frappe Cloud dashboard has the full controls.')"
    :loading="!store.state.accountLinks && !store.state.accountLinksError"
    :error="store.state.accountLinksError"
    :error-title="__(`Couldn't load your server links`)"
    @retry="store.loadAccountLinks()"
  >
    <div class="divide-y divide-outline-gray-1 border-t border-outline-gray-1">
      <SettingsRow
        v-for="row in rows"
        :key="row.title"
        label-for=""
        :title="row.title"
        class="flex-col items-start gap-3 sm:flex-row sm:items-center sm:gap-8"
        :description="row.description"
      >
        <Button
          v-if="row.label"
          icon-right="lucide-arrow-up-right"
          :label="row.label"
          :disabled="row.disabled"
          @click="openExternal(row.url)"
        />
      </SettingsRow>
    </div>
  </Panel>
</template>
