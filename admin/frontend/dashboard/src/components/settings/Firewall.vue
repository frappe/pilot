<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { Alert, Button, ErrorMessage, Select, Spinner, TextInput, toast } from 'frappe-ui'

import EmptyState from '@/components/common/EmptyState.vue'
import SettingsSwitch from '@/components/settings/SettingsSwitch.vue'

import { apiErrorMessage } from '@/api/client'
import { settingsApi } from '@/api/settings'

const ACTION_OPTIONS = [
  { label: 'Block', value: 'deny' },
  { label: 'Allow', value: 'allow' },
]
// Lenient IPv4/IPv6/CIDR shape; backend validates authoritatively.
const IP_PATTERN = /^[0-9a-fA-F:.]+(\/\d{1,3})?$/

const loading = ref(true)
const saving = ref(false)
const error = ref('')
const enabled = ref(false)
const production = ref(true)
const defaultPolicy = ref('allow')
const rules = ref([])
const myIp = ref('')

const lockoutRisk = computed(() => enabled.value && defaultPolicy.value === 'deny')

const addRule = () => {
  rules.value.push({
    ip: '',
    action: defaultPolicy.value === 'deny' ? 'allow' : 'deny',
    description: '',
  })
}

const removeRule = (index) => {
  rules.value.splice(index, 1)
}

// A wrong fill shows at its own row; empty or invalid IPs just hold the button.
const ipError = (rule) => {
  const ip = rule.ip.trim()
  if (!ip || IP_PATTERN.test(ip)) return ''
  return `'${rule.ip}' is not a valid IP or CIDR.`
}
const canSave = computed(() =>
  rules.value.every((r) => r.ip.trim() && IP_PATTERN.test(r.ip.trim())),
)

const validate = () => {
  // Block-by-default with no Allow rule blocks everyone, including you.
  if (
    enabled.value &&
    defaultPolicy.value === 'deny' &&
    !rules.value.some((r) => r.action === 'allow' && r.ip.trim())
  ) {
    return 'Block by default needs at least one Allow rule, or no one can reach the server.'
  }
  return ''
}

const save = async () => {
  error.value = validate()
  if (error.value) return

  saving.value = true
  try {
    const payload = {
      enabled: enabled.value,
      default: defaultPolicy.value,
      rules: rules.value.map((r) => ({
        ip: r.ip.trim(),
        action: r.action,
        description: (r.description || '').trim(),
      })),
    }
    const result = await settingsApi.update({ firewall: payload })
    if (result.error) {
      error.value = apiErrorMessage(result, 'Failed to save.')
      return
    }
    toast.success('Firewall updated')
  } catch (e) {
    error.value = e.message || 'Failed to save.'
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  try {
    const data = await settingsApi.get()
    production.value = !!data.production?.enabled
    const fw = data.firewall || {}
    enabled.value = !!fw.enabled
    defaultPolicy.value = fw.default || 'allow'
    rules.value = (fw.rules || []).map((r) => ({
      ip: r.ip || '',
      action: r.action || 'deny',
      description: r.description || '',
    }))
    try {
      myIp.value = (await settingsApi.myIp()).ip || ''
    } catch {
      myIp.value = ''
    }
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
    <Alert
      v-if="!production"
      class="border border-outline-gray-2"
      title="Not enforced yet"
      theme="amber"
      :dismissible="false"
    >
      <template #description>
        <span class="text-ink-gray-6 text-p-sm"
          >These rules take effect only in production (they're applied by nginx). This bench isn't
          deployed, so nothing is enforced until you run
          <span class="font-mono text-xs">pilot setup production</span>.</span
        >
      </template>
    </Alert>

    <SettingsSwitch
      label="Enable firewall"
      description="Restrict who can reach Pilot and deployed sites; off means open."
      :model-value="enabled"
      @update:model-value="(v) => (enabled = v)"
    />

    <SettingsSwitch
      label="Block by default"
      description="Only allowed IPs below can reach the server; off allows all except blocked ones."
      :model-value="defaultPolicy === 'deny'"
      @update:model-value="(v) => (defaultPolicy = v ? 'deny' : 'allow')"
    />

    <Alert
      v-if="lockoutRisk"
      class="border border-outline-gray-2"
      title="Heads up"
      theme="amber"
      :dismissible="false"
    >
      <template #description>
        <span class="text-ink-gray-6 text-p-sm"
          >Everyone is blocked by default. Add an <b>Allow</b> rule for your own IP<template
            v-if="myIp"
          >
            (<span class="font-mono text-xs">{{ myIp }}</span>)</template
          >, or you may lock yourself out of the web UI.</span
        >
      </template>
    </Alert>

    <div class="space-y-2">
      <div class="flex justify-between items-center">
        <p class="font-medium text-ink-gray-8 leading-normal">Rules</p>
        <Button icon-left="lucide-plus" @click="addRule">Add rule</Button>
      </div>

      <EmptyState
        compact
        v-if="!rules.length"
        icon="lucide-shield"
        title="No firewall rules"
        :description="
          defaultPolicy === 'allow'
            ? 'Everyone can reach the server. Add a rule to block specific IPs or ranges.'
            : 'No one can reach the server. Add an Allow rule for the IPs that should have access.'
        "
      />

      <div v-else class="space-y-3">
        <div v-for="(rule, index) in rules" :key="index">
          <div class="flex items-end gap-2">
            <div class="space-y-1.5 w-28 shrink-0">
              <p v-if="index === 0" class="font-medium text-ink-gray-7">Action</p>
              <Select v-model="rule.action" :options="ACTION_OPTIONS" class="w-full" />
            </div>

            <div class="flex-1 space-y-1.5">
              <p v-if="index === 0" class="font-medium text-ink-gray-7">IP / CIDR</p>
              <TextInput v-model="rule.ip" placeholder="203.0.113.4 or 10.0.0.0/8" class="w-full" />
            </div>

            <div class="flex-1 space-y-1.5">
              <p v-if="index === 0" class="font-medium text-ink-gray-7">Note</p>
              <TextInput v-model="rule.description" placeholder="optional" class="w-full" />
            </div>

            <Button
              icon="lucide-x"
              label="Remove rule"
              tooltip="Remove rule"
              @click="removeRule(index)"
            />
          </div>

          <p v-if="ipError(rule)" class="mt-1.5 text-ink-red-5 text-p-sm">{{ ipError(rule) }}</p>
        </div>
      </div>
    </div>

    <ErrorMessage v-if="error" :message="error" />

    <div v-if="rules.length" class="flex justify-end">
      <Button variant="solid" :loading="saving" :disabled="!canSave" @click="save">
        Save changes
      </Button>
    </div>
  </div>
</template>
