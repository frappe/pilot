<script setup lang="ts">
import { Button, ErrorMessage, Spinner, TextInput, toast } from 'frappe-ui'
import { computed, onMounted, ref } from 'vue'
import { apiErrorMessage } from '@/api/client'
import { settingsApi } from '@/api/settings'
import EmptyState from '@/components/common/EmptyState.vue'
import SettingsSwitch from '@/components/settings/SettingsSwitch.vue'
import { useSession } from '@/composables/auth/useSession'

const { session } = useSession()

// Every resource alert starts off; site uptime is the only one on by default.
const RESOURCE_ALERTS = [
  {
    key: 'cpu_usage_limit',
    label: 'CPU usage',
    description: 'Alert when processor use on this host crosses the limit.',
    initial: 85,
  },
  {
    key: 'memory_usage_limit',
    label: 'Memory usage',
    description: 'Alert when memory use on this host crosses the limit.',
    initial: 90,
  },
  {
    key: 'disk_space_limit',
    label: 'Disk usage',
    description: 'Alert when disk use on this host crosses the limit.',
    initial: 90,
  },
]

const loading = ref(true)
const saving = ref(false)
const error = ref('')
// The switch owns whether an alert is on, so clearing the field keeps it mounted.
const enabled = ref(Object.fromEntries(RESOURCE_ALERTS.map((alert) => [alert.key, false])))
const limits = ref(Object.fromEntries(RESOURCE_ALERTS.map((alert) => [alert.key, ''])))
const siteUptime = ref(true)
const webhooks = ref([])
const recipients = ref([])
// Recipients are useless without a mailbox to send from, and that lives in Mail settings.
const mailConfigured = ref(false)

const savedPayload = ref('')
const dirty = computed(() => JSON.stringify(buildPayload()) !== savedPayload.value)

const buildPayload = () => {
  return {
    ...Object.fromEntries(
      RESOURCE_ALERTS.map((alert) => [
        alert.key,
        enabled.value[alert.key] ? Number(limits.value[alert.key]) || 0 : 0,
      ]),
    ),
    site_uptime: siteUptime.value,
    // Blank token means "keep the stored one"; original_url is how it is found.
    webhook_endpoints: webhooks.value.map((webhook) => ({
      url: webhook.url.trim(),
      token: webhook.token,
      original_url: webhook.original_url,
    })),
    email_recipients: recipients.value.map((address) => address.trim()).filter(Boolean),
  }
}

const addRecipient = () => {
  recipients.value.push('')
}

const removeRecipient = (index) => {
  recipients.value.splice(index, 1)
}

const recipientError = (address) => {
  const trimmed = address.trim()
  if (trimmed && !/^[^@\s]+@[^@\s]+$/.test(trimmed)) return 'Must be an email address.'
  return ''
}

const setEnabled = (key, on) => {
  enabled.value[key] = on
  if (on && !Number(limits.value[key])) {
    limits.value[key] = String(RESOURCE_ALERTS.find((alert) => alert.key === key).initial)
  }
}

const addWebhook = () => {
  webhooks.value.push({ url: '', token: '', token_set: false, original_url: '' })
}

const removeWebhook = (index) => {
  webhooks.value.splice(index, 1)
}

// A row still being filled in says nothing and just holds the Save button.
const webhookError = (webhook) => {
  const url = webhook.url.trim()
  if (url && (!URL.canParse(url) || !/^https?:\/\//.test(url)))
    return 'Endpoint must be an http:// or https:// URL.'
  return ''
}

const webhookIsComplete = (webhook) => {
  return Boolean(webhook.url.trim()) && Boolean(webhook.token || webhook.token_set)
}

const limitError = (key) => {
  if (!enabled.value[key]) return ''
  const limit = Number(limits.value[key])
  if (!Number.isInteger(limit) || limit < 1 || limit > 100)
    return 'Limit must be a whole percentage between 1 and 100.'
  return ''
}

const canSave = computed(
  () =>
    RESOURCE_ALERTS.every((alert) => !limitError(alert.key)) &&
    webhooks.value.every((webhook) => webhookIsComplete(webhook) && !webhookError(webhook)) &&
    recipients.value.every((address) => !recipientError(address)),
)

const save = async () => {
  if (!canSave.value) return

  error.value = ''
  saving.value = true
  try {
    const payload = buildPayload()
    const result = await settingsApi.update({ resource_limits: payload })
    if (result.error) {
      error.value = apiErrorMessage(result, 'Failed to save.')
      return
    }
    // Drop the typed secrets once stored, so the form reads back like a reload.
    for (const webhook of webhooks.value) {
      webhook.token_set = webhook.token_set || Boolean(webhook.token)
      webhook.token = ''
      webhook.original_url = webhook.url.trim()
    }
    savedPayload.value = JSON.stringify(buildPayload())
    toast.success('Notification settings saved')
  } catch (e) {
    error.value = e.message || 'Failed to save.'
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  try {
    const data = await settingsApi.get()
    const saved = data.resource_limits || {}
    for (const alert of RESOURCE_ALERTS) {
      const limit = Number(saved[alert.key]) || 0
      enabled.value[alert.key] = limit > 0
      limits.value[alert.key] = limit ? String(limit) : ''
    }
    siteUptime.value = saved.site_uptime ?? true
    webhooks.value = (saved.webhook_endpoints || []).map((webhook) => ({
      url: webhook.url || '',
      token: '',
      token_set: Boolean(webhook.token_set),
      original_url: webhook.url || '',
    }))
    recipients.value = [...(saved.email_recipients || [])]
    mailConfigured.value = Boolean((data.mail || {}).server)
    savedPayload.value = JSON.stringify(buildPayload())
  } catch (e) {
    error.value = e.message || 'Could not load settings.'
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
    <SettingsSwitch
      label="Site uptime"
      description="Alert when a site stops responding to its uptime check."
      :model-value="siteUptime"
      @update:model-value="(on) => (siteUptime = on)"
    />

    <div v-for="alert in RESOURCE_ALERTS" :key="alert.key" class="space-y-3">
      <SettingsSwitch
        :label="alert.label"
        :description="alert.description"
        :model-value="enabled[alert.key]"
        @update:model-value="(on) => setEnabled(alert.key, on)"
      />
      <div v-if="enabled[alert.key]" class="flex items-center gap-2 pl-0.5">
        <TextInput
          v-model="limits[alert.key]"
          type="number"
          min="1"
          max="100"
          class="w-24"
          :aria-label="`${alert.label} limit`"
        />
        <span class="text-ink-gray-6">% and above</span>
      </div>
    </div>

    <div class="space-y-2">
      <div class="flex justify-between items-center">
        <p class="font-medium text-ink-gray-8 leading-normal">Email recipients</p>
        <Button icon-left="lucide-plus" @click="addRecipient">Add recipient</Button>
      </div>

      <p class="text-ink-gray-5 text-p-sm">
        Alerts are emailed to these addresses, using the mailbox in Mail settings.
      </p>

      <EmptyState
        compact
        v-if="!recipients.length"
        icon="lucide-mail"
        title="No email recipients"
        description="Add an address to receive alerts by email."
      />

      <div v-else class="space-y-2">
        <div v-for="(address, index) in recipients" :key="index">
          <div class="flex items-center gap-2">
            <TextInput v-model="recipients[index]" placeholder="ops@example.com" class="w-full" />
            <Button
              icon="lucide-x"
              label="Remove recipient"
              tooltip="Remove recipient"
              @click="removeRecipient(index)"
            />
          </div>
          <p v-if="recipientError(address)" class="mt-1.5 text-ink-red-5 text-p-sm">
            {{ recipientError(address) }}
          </p>
        </div>
      </div>

      <p v-if="recipients.length && !mailConfigured" class="text-ink-amber-5 text-p-sm">
        Set up a mailbox in Mail settings before these addresses receive anything.
      </p>
    </div>

    <details class="group">
      <summary
        class="flex items-center gap-1.5 text-ink-gray-6 cursor-pointer select-none"
      >
        <span class="size-4 transition-transform group-open:rotate-90 lucide-chevron-right"></span>
        Advanced
      </summary>

      <div class="space-y-2 pt-4">
        <div class="flex justify-between items-center">
          <p class="font-medium text-ink-gray-8 leading-normal">Webhook endpoints</p>
          <Button icon-left="lucide-plus" @click="addWebhook">Add endpoint</Button>
        </div>

        <p class="text-ink-gray-5 text-p-sm">
          <template v-if="session.centralEnabled">Alerts go to Central. Endpoints listed here receive them too, as a POST carrying an</template>
          <template v-else>Endpoints listed here receive alerts as a POST carrying an</template>
          <code>Authorization: Bearer</code>
          header, so the token stays out of the URL.
        </p>

        <EmptyState
          compact
          v-if="!webhooks.length"
          icon="lucide-webhook"
          title="No webhook endpoints"
          :description="
            session.centralEnabled
              ? 'Alerts are only reported to Central. Add an endpoint to receive them yourself.'
              : 'Nothing receives alerts yet. Add an endpoint to receive them.'
          "
        />

        <div v-else class="space-y-3">
          <div v-for="(webhook, index) in webhooks" :key="index">
            <div class="flex items-end gap-2">
              <div class="flex-1 space-y-1.5">
                <p v-if="index === 0" class="font-medium text-ink-gray-7">Endpoint URL</p>
                <TextInput
                  v-model="webhook.url"
                  placeholder="https://alerts.example.com/pilot"
                  class="w-full"
                />
              </div>

              <div class="flex-1 space-y-1.5">
                <p v-if="index === 0" class="font-medium text-ink-gray-7">Token</p>
                <TextInput
                  v-model="webhook.token"
                  type="password"
                  :placeholder="webhook.token_set ? 'Unchanged' : 'Bearer token'"
                  class="w-full"
                />
              </div>

              <Button
                icon="lucide-x"
                label="Remove endpoint"
                tooltip="Remove endpoint"
                @click="removeWebhook(index)"
              />
            </div>

            <p v-if="webhookError(webhook)" class="mt-1.5 text-ink-red-5 text-p-sm">
              {{ webhookError(webhook) }}
            </p>
          </div>
        </div>
      </div>
    </details>

    <ErrorMessage v-if="error" :message="error" />

    <div v-if="dirty" class="flex justify-end">
      <Button variant="solid" :loading="saving" :disabled="!canSave" @click="save">
        Save changes
      </Button>
    </div>
  </div>
</template>
