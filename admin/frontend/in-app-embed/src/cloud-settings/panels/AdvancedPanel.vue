<script setup lang="ts">
import { useTranslation } from '../translation'
import { Button, SettingsRow } from 'frappe-ui'
import { watch } from 'vue'
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
</script>

<template>
  <Panel
    :title="__('Advanced')"
    :description="__('Deeper controls for your server.')"
    :loading="!store.state.accountLinks && !store.state.accountLinksError"
    :error="store.state.accountLinksError"
    :error-title="__(`Couldn't load your server links`)"
    @retry="store.loadAccountLinks()"
  >
    <div class="divide-y divide-outline-gray-1 border-t border-outline-gray-1">
      <SettingsRow
        label-for=""
        :title="__('Open your server')"
        class="flex-col items-start gap-3 sm:flex-row sm:items-center sm:gap-8"
        :description="
          store.isOnTrial()
            ? __('Not in the free trial. Upgrade to use it.')
            : __('Deploys, scaling, SSH, backups and sites. The full server controls.')
        "
      >
        <Button
          icon-right="lucide-arrow-up-right"
          :label="__('Open server')"
          :disabled="store.isOnTrial()"
          @click="openExternal(store.state.accountLinks?.server)"
        />
      </SettingsRow>

      <SettingsRow
        label-for=""
        :title="__('Account & billing')"
        class="flex-col items-start gap-3 sm:flex-row sm:items-center sm:gap-8"
        :description="
          __('Payment methods, invoices, billing email and account settings.')
        "
      >
        <Button
          icon-right="lucide-arrow-up-right"
          :label="__('Manage billing')"
          @click="openExternal(store.state.accountLinks?.billing)"
        />
      </SettingsRow>
    </div>

  </Panel>
</template>
