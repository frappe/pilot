<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Alert, Badge, Button, ErrorMessage, Spinner, toast } from 'frappe-ui'

import SettingsSwitch from '@/components/settings/SettingsSwitch.vue'

import { settingsApi } from '@/api/settings'
import type { CloudflareStatus } from '@/types/settings'
import { errorMessage } from '@/utils/error'

const loading = ref(true)
const loaded = ref(false)
const saving = ref(false)
const error = ref('')
const production = ref(true)
const saved = ref(false)
const enabled = ref(false)
const status = ref<CloudflareStatus | null>(null)

const proxiedDomains = computed(
  () => status.value?.domains.filter((d) => d.is_behind_cloudflare) ?? [],
)
const mismatch = computed(() => {
  if (!status.value) return ''
  if (enabled.value && !proxiedDomains.value.length) {
    return 'No site domain resolves to Cloudflare yet. Turn on the proxy (orange cloud) in Cloudflare DNS.'
  }
  if (!enabled.value && proxiedDomains.value.length) {
    return 'Some domains resolve to Cloudflare. Turn this on so visitors get the right IP and Cloudflare can connect.'
  }
  return ''
})

const save = async () => {
  saving.value = true
  error.value = ''
  try {
    await settingsApi.update({ proxy: { cloudflare: enabled.value } })
    saved.value = enabled.value
    toast.success(enabled.value ? 'Cloudflare proxy enabled' : 'Cloudflare proxy disabled')
  } catch (e) {
    error.value = errorMessage(e, 'Failed to save.')
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  try {
    const data = await settingsApi.get()
    production.value = !!data.production?.enabled
    saved.value = enabled.value = !!data.proxy?.cloudflare
    loaded.value = true
    status.value = await settingsApi.cloudflareStatus()
  } catch (e) {
    error.value = errorMessage(e, 'Could not load Cloudflare settings.')
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <Spinner size="lg" class="text-ink-gray-4" />
  </div>

  <div v-else class="space-y-6">
    <Alert
      v-if="!production"
      class="border border-outline-gray-2"
      title="Not enforced yet"
      theme="amber"
      :dismissible="false"
    >
      <template #description>
        <span class="text-ink-gray-6 text-p-sm"
          >nginx applies this setting only in production. Run
          <span class="font-mono text-xs">pilot setup production</span>
          first.</span
        >
      </template>
    </Alert>

    <SettingsSwitch
      label="Sites use Cloudflare proxy"
      description="Trust Cloudflare for the visitor IP and accept its TLS handshakes. Applies to every bench on this server."
      :model-value="enabled"
      :disabled="!loaded"
      @update:model-value="(v: boolean) => (enabled = v)"
    />

    <div v-if="status" class="space-y-2">
      <p class="font-medium text-ink-gray-8">Traffic path</p>
      <p class="text-ink-gray-6 text-p-sm">
        <template v-if="status.has_edge_proxy">
          Cloudflare → Frappe Cloud edge proxy → this server. Only the edge proxy can connect
          directly.
        </template>
        <template v-else>Cloudflare → this server.</template>
      </p>
    </div>

    <div v-if="status?.domains.length" class="space-y-2">
      <p class="font-medium text-ink-gray-8">Domains</p>
      <div
        v-for="item in status.domains"
        :key="item.domain"
        class="flex justify-between items-center text-p-sm"
      >
        <span class="font-mono text-xs text-ink-gray-7">{{ item.domain }}</span>
        <Badge
          :theme="item.is_behind_cloudflare ? 'amber' : 'gray'"
          :label="item.is_behind_cloudflare ? 'Cloudflare' : 'Direct'"
        />
      </div>
    </div>

    <Alert
      v-if="mismatch"
      class="border border-outline-gray-2"
      title="Check DNS"
      theme="amber"
      :dismissible="false"
    >
      <template #description>
        <span class="text-ink-gray-6 text-p-sm">{{ mismatch }}</span>
      </template>
    </Alert>

    <p class="text-ink-gray-6 text-p-sm">
      In Cloudflare, set SSL/TLS to <b>Full (strict)</b>. Keep <b>Always Use HTTPS</b> off, so
      certificate renewal can reach this server over HTTP.
    </p>

    <ErrorMessage v-if="error" :message="error" />

    <div class="flex justify-end">
      <Button
        variant="solid"
        :loading="saving"
        :disabled="!loaded || enabled === saved"
        @click="save"
      >
        Save changes
      </Button>
    </div>
  </div>
</template>
