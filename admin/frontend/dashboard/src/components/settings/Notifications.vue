<script setup lang="ts">
import { Button, ErrorMessage, Spinner, Switch, TextInput, toast } from 'frappe-ui'
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { settingsApi } from '@/api/settings'
import EmptyState from '@/components/common/EmptyState.vue'
import TagsInput from '@/components/common/TagsInput.vue'
import NotificationsIllustration from '@/components/settings/NotificationsIllustration.vue'
import { useSession } from '@/composables/auth/useSession'
import { errorMessage } from '@/utils/error'

interface WebhookForm {
  url: string
  token: string
  token_set: boolean
  original_url: string
}

const { session } = useSession()

// Every resource alert starts off; site uptime is the only one on by default.
const RESOURCE_ALERTS = [
  {
    key: 'cpu_usage_limit',
    label: 'CPU usage',
    metric: 'processor use',
    description: 'Alert when processor use on this host crosses the limit.',
  },
  {
    key: 'memory_usage_limit',
    label: 'Memory usage',
    metric: 'memory use',
    description: 'Alert when memory use on this host crosses the limit.',
  },
  {
    key: 'disk_space_limit',
    label: 'Disk usage',
    metric: 'disk use',
    description: 'Alert when disk use on this host crosses the limit.',
  },
]

const loading = ref(true)
const saving = ref(false)
const error = ref('')
// The switch owns whether an alert is on, so clearing the field keeps it mounted.
const enabled = ref(Object.fromEntries(RESOURCE_ALERTS.map((alert) => [alert.key, false])))
const limits = ref(Object.fromEntries(RESOURCE_ALERTS.map((alert) => [alert.key, ''])))
const siteUptime = ref(true)
const webhooks = ref<WebhookForm[]>([])
const recipients = ref<string[]>([])
const mailConfigured = ref(false)
const mailRoute = { name: 'Settings', params: { section: 'mail' } }

const invalidRecipient = computed(() => recipients.value.find((address) => !isAddress(address)))

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

const isAddress = (address: string) => /^[^@\s]+@[^@\s]+$/.test(address.trim())

const setEnabled = (key: string, on: boolean) => {
  enabled.value[key] = on

  if (on && !Number(limits.value[key])) limits.value[key] = '90'
}

const addWebhook = () => {
  webhooks.value.push({ url: '', token: '', token_set: false, original_url: '' })
}

const removeWebhook = (index: number) => {
  webhooks.value.splice(index, 1)
}

// A row still being filled in says nothing and just holds the Save button.
const webhookError = (webhook: WebhookForm) => {
  const url = webhook.url.trim()
  if (url && (!URL.canParse(url) || !/^https?:\/\//.test(url)))
    return 'Endpoint must be an http:// or https:// URL.'
  return ''
}

const webhookIsComplete = (webhook: WebhookForm) => {
  return Boolean(webhook.url.trim()) && Boolean(webhook.token || webhook.token_set)
}

const limitError = (key: string) => {
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
    !invalidRecipient.value,
)

const save = async () => {
  if (!canSave.value) return

  error.value = ''
  saving.value = true
  try {
    const payload = buildPayload()
    await settingsApi.update({ resource_limits: payload })
    // Drop the typed secrets once stored, so the form reads back like a reload.
    for (const webhook of webhooks.value) {
      webhook.token_set = webhook.token_set || Boolean(webhook.token)
      webhook.token = ''
      webhook.original_url = webhook.url.trim()
    }
    savedPayload.value = JSON.stringify(buildPayload())
    toast.success('Notification settings saved')
  } catch (e) {
    error.value = errorMessage(e, 'Failed to save.')
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  try {
    const data = await settingsApi.get()
    const saved = data.resource_limits || {}
    const savedLimits: Record<string, unknown> = { ...saved }

    for (const alert of RESOURCE_ALERTS) {
      const limit = Number(savedLimits[alert.key]) || 0
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
    mailConfigured.value = Boolean(data.mail?.server)
    savedPayload.value = JSON.stringify(buildPayload())
  } catch (e) {
    error.value = errorMessage(e, 'Could not load settings.')
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <Spinner size="lg" class="text-ink-gray-4" />
  </div>

  <div v-else class="space-y-8">
    <div class="-mt-3.5 divide-y divide-outline-alpha-gray-1">
      <div class="flex justify-between items-center gap-4 py-3.5">
        <div class="flex flex-col gap-1">
          <p class="text-base-medium text-ink-gray-8">Site uptime</p>
          <p class="text-ink-gray-6 text-p-sm">
            Alert when a site stops responding to its uptime check.
          </p>
        </div>

        <Switch v-model="siteUptime" />
      </div>

      <div
        v-for="alert in RESOURCE_ALERTS"
        :key="alert.key"
        class="flex justify-between items-center gap-4 py-3.5"
      >
        <div class="flex flex-col gap-1">
          <p class="text-base-medium text-ink-gray-8">{{ alert.label }}</p>

          <p v-if="enabled[alert.key]" class="flex items-center gap-1.5 text-ink-gray-6 text-p-sm">
            Alert when {{ alert.metric }} goes above
            <TextInput
              v-model="limits[alert.key]"
              type="number"
              min="1"
              max="100"
              size="xs"
              class="-my-0.5 w-14"
              :aria-label="`${alert.label} limit`"
            />
            %
          </p>

          <p v-else class="text-ink-gray-6 text-p-sm">{{ alert.description }}</p>
        </div>

        <Switch
          :model-value="enabled[alert.key]"
          @update:model-value="(on: boolean) => setEnabled(alert.key, on)"
        />
      </div>
    </div>

    <div class="space-y-2">
      <h4 class="text-base-medium text-ink-gray-8">Email recipients</h4>

      <p class="text-ink-gray-6 text-p-sm">
        Alerts are emailed to these addresses from the
        <RouterLink :to="mailRoute" class="mail-link">mailbox</RouterLink>
        in Mail settings.
      </p>

      <TagsInput
        v-model="recipients"
        placeholder="Add an email"
        :is-invalid="(address) => !isAddress(address)"
      />

      <p v-if="invalidRecipient" class="text-ink-red-5 text-p-sm">
        {{ invalidRecipient }}
        is not an email address.
      </p>

      <p v-else-if="recipients.length && !mailConfigured" class="text-ink-amber-6 text-p-sm">
        Set up a mailbox before these addresses receive anything.
      </p>
    </div>

    <div class="space-y-2">
      <h4 class="text-base-medium text-ink-gray-8">Webhook endpoints</h4>

      <p class="text-ink-gray-6 text-p-sm">
        <template v-if="session.centralEnabled">
          Alerts go to
          <a
            v-if="session.centralUrl"
            :href="`${session.centralUrl}/dashboard`"
            target="_blank"
            rel="noopener"
            class="inline-flex items-center gap-0.5 text-p-sm-medium text-ink-gray-8 hover:text-ink-gray-9"
          >
            Central
            <span class="size-3 lucide-arrow-up-right" aria-hidden="true" />
          </a>
          <template v-else>Central</template>
          and to these endpoints.
        </template>
        Each endpoint gets a POST with your token in the
        <code>Authorization</code>
        header.
      </p>

      <div
        class="dark:bg-surface-gray-1 border border-outline-gray-1 dark:border-transparent rounded-7 overflow-hidden"
      >
        <EmptyState
          compact
          v-if="!webhooks.length"
          :bordered="false"
          title="No webhook endpoints"
          :description="
            session.centralEnabled
              ? 'Alerts only go to Central. Add an endpoint to get them too.'
              : 'Add one to send alerts to your own service.'
          "
          class="py-8"
        >
          <template #illustration>
            <NotificationsIllustration kind="webhook" />
          </template>
        </EmptyState>

        <div
          v-else
          class="items-center gap-x-3 gap-y-2 grid grid-cols-[1fr_9rem_2rem] sm:grid-cols-[1fr_12rem_2rem] p-4"
        >
          <span class="text-ink-gray-5 text-sm">Endpoint URL</span>
          <span class="text-ink-gray-5 text-sm">Token</span>
          <span />

          <template v-for="(webhook, index) in webhooks" :key="index">
            <TextInput v-model="webhook.url" placeholder="https://alerts.example.com/pilot" />

            <TextInput
              v-model="webhook.token"
              type="password"
              autocomplete="new-password"
              :placeholder="webhook.token_set ? 'Unchanged' : 'Bearer token'"
            />

            <Button
              icon="lucide-x"
              variant="ghost"
              label="Remove endpoint"
              tooltip="Remove endpoint"
              @click="removeWebhook(index)"
            />

            <p v-if="webhookError(webhook)" class="col-span-3 -mt-1 text-ink-red-5 text-p-sm">
              {{ webhookError(webhook) }}
            </p>
          </template>
        </div>

        <footer class="flex items-center px-3 py-2.5 border-outline-gray-2 border-t">
          <Button
            variant="ghost"
            icon-left="lucide-plus"
            label="Add endpoint"
            @click="addWebhook"
          />
        </footer>
      </div>
    </div>

    <ErrorMessage v-if="error" :message="error" />

    <div v-if="dirty" class="flex justify-end">
      <Button variant="solid" :loading="saving" :disabled="!canSave" @click="save">
        Save changes
      </Button>
    </div>
  </div>
</template>

<style scoped>
.mail-link {
  @apply text-ink-gray-7 underline decoration-dotted decoration-2 decoration-[--ink-gray-4] underline-offset-2 hover:text-ink-gray-8;
}
</style>
