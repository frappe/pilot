<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <span class="size-5 text-ink-gray-4 animate-spin lucide-loader-circle"></span>
  </div>
  <div v-else class="space-y-6">
    <Switch label="Enable firewall" description="Restrict who can reach Pilot and deployed sites; off means open."
      :model-value="enabled" @update:model-value="(v) => (enabled = v)" />

    <Switch label="Block by default"
      description="Only allowed IPs below can reach the server; off allows all except blocked ones."
      :model-value="defaultPolicy === 'deny'" @update:model-value="(v) => (defaultPolicy = v ? 'deny' : 'allow')" />

    <Alert v-if="lockoutRisk" title="Heads up" theme="yellow" :dismissible="false">
      <template #description>
        <span class="text-ink-gray-6 text-p-sm">Everyone is blocked by default. Add an <b>Allow</b> rule for
          your own IP<template v-if="myIp"> (<span class="font-mono text-xs">{{ myIp }}</span>)</template>,
          or you may lock yourself out of the web UI.</span>
      </template>
    </Alert>

    <div class="space-y-2">
      <div class="flex justify-between items-center">
        <p class="font-medium text-ink-gray-8 text-base leading-normal">Rules</p>
        <Button variant="subtle" icon-left="plus" @click="addRule">Add rule</Button>
      </div>

      <div v-if="!rules.length"
        class="flex flex-col items-center gap-2.5 py-10 border border-dashed rounded-lg border-outline-gray-2 text-center">
        <div class="flex justify-center items-center bg-surface-gray-2 rounded-full size-11">
          <span class="size-5 text-ink-gray-5 lucide-shield"></span>
        </div>
        <p class="font-medium text-ink-gray-7 text-sm">No firewall rules</p>
        <p class="max-w-xs text-ink-gray-5 text-xs">
          {{ defaultPolicy === 'allow'
            ? 'Everyone can reach the server. Add a rule to block specific IPs or ranges.'
            : 'No one can reach the server. Add an Allow rule for the IPs that should have access.' }}
        </p>
      </div>

      <div v-else class="space-y-3">
        <div v-for="(rule, index) in rules" :key="index" class="flex items-end gap-2">
          <div class="space-y-1.5 w-28 shrink-0">
            <p v-if="index === 0" class="font-medium text-ink-gray-7 text-sm">Action</p>
            <Select v-model="rule.action" :options="ACTION_OPTIONS" class="w-full" />
          </div>
          <div class="flex-1 space-y-1.5">
            <p v-if="index === 0" class="font-medium text-ink-gray-7 text-sm">IP / CIDR</p>
            <TextInput v-model="rule.ip" placeholder="203.0.113.4 or 10.0.0.0/8" class="w-full" />
          </div>
          <div class="flex-1 space-y-1.5">
            <p v-if="index === 0" class="font-medium text-ink-gray-7 text-sm">Note</p>
            <TextInput v-model="rule.description" placeholder="optional" class="w-full" />
          </div>
          <Button variant="subtle" icon="lucide-x" @click="removeRule(index)" />
        </div>
      </div>
    </div>

    <ErrorMessage v-if="error" :message="error" />

    <div v-if="rules.length" class="flex justify-end">
      <Button variant="solid" :loading="saving" @click="save">Save changes</Button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { Button, ErrorMessage, Select, Switch, TextInput, toast, Alert } from 'frappe-ui'
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
const defaultPolicy = ref('allow')
const rules = ref([])
const myIp = ref('')

const lockoutRisk = computed(() => enabled.value && defaultPolicy.value === 'deny')

function addRule() {
  rules.value.push({ ip: '', action: defaultPolicy.value === 'deny' ? 'allow' : 'deny', description: '' })
}

function removeRule(index) {
  rules.value.splice(index, 1)
}

function validate() {
  for (const [index, rule] of rules.value.entries()) {
    if (!rule.ip.trim()) return `Rule ${index + 1} needs an IP address or range.`
    if (!IP_PATTERN.test(rule.ip.trim())) return `Rule ${index + 1}: '${rule.ip}' is not a valid IP or CIDR.`
  }
  // Block-by-default with no Allow rule blocks everyone, including you.
  if (enabled.value && defaultPolicy.value === 'deny'
    && !rules.value.some((r) => r.action === 'allow' && r.ip.trim())) {
    return 'Block by default needs at least one Allow rule, or no one can reach the server.'
  }
  return ''
}

async function save() {
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
  } finally {
    loading.value = false
  }
})
</script>
