<script setup lang="ts">
import { Button, ErrorMessage, SettingsRow } from 'frappe-ui'
import { computed, ref } from 'vue'
import Panel from '../components/Panel.vue'
import { openExternal } from '../external'
import type { Store } from '../store'

interface Props {
  store: Store
}

const props = defineProps<Props>()

const context = computed(() => props.store.state.context || {})
const openingBilling = ref(false)
const billingError = ref('')

const links = computed(() => [
  {
    title: __('Open your server'),
    description: __('Deploys, scaling, SSH, backups, sites — the full server controls.'),
    label: __('Open server'),
    url: context.value.server_url,
  },
])

const openBilling = async () => {
  if (openingBilling.value) return

  openingBilling.value = true
  billingError.value = ''

  try {
    const response = context.value.account_url
      ? { url: context.value.account_url }
      : await props.store.api.getAccountUrl()

    if (!response?.url) throw new Error(__('Central is not configured.'))

    openExternal(response.url)
  } catch (exception) {
    billingError.value = props.store.api.getErrorMessage(exception)
  } finally {
    openingBilling.value = false
  }
}
</script>

<template>
  <Panel :title="__('Advanced')" :description="__('Deeper controls for your server.')">
    <div class="divide-y divide-outline-gray-1 border-t border-outline-gray-1">
      <SettingsRow
        v-for="link in links"
        :key="link.title"
        label-for=""
        :title="link.title"
        :description="link.description"
      >
        <Button
          v-if="link.url"
          icon-right="lucide-arrow-up-right"
          :label="link.label"
          @click="openExternal(link.url)"
        />

        <span v-else class="text-p-sm text-ink-gray-5">{{ __("Not configured") }}</span>
      </SettingsRow>

      <SettingsRow
        label-for=""
        :title="__('Account & billing')"
        :description="
          __('Payment methods, invoices, billing email and account settings.')
        "
      >
        <Button
          icon-right="lucide-arrow-up-right"
          :disabled="openingBilling"
          :label="openingBilling ? __('Opening billing') : __('Manage billing')"
          @click="openBilling"
        />
      </SettingsRow>
    </div>

    <ErrorMessage :message="billingError" class="mt-2" />
  </Panel>
</template>
