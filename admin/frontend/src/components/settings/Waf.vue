<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <span class="size-5 text-ink-gray-4 animate-spin lucide-loader-circle"></span>
  </div>
  <div v-else class="space-y-6">
    <Switch label="Enable WAF"
      description="ModSecurity + OWASP Core Rule Set inspects request contents (SQLi, XSS, path traversal) for all sites and the admin."
      :model-value="enabled" @update:model-value="(v) => (enabled = v)" />

    <Alert v-if="enabled && !installed" title="ModSecurity will be installed" theme="blue" :dismissible="false">
      <template #description>
        <span class="text-ink-gray-6 text-p-sm">Saving installs the ModSecurity module and rule set the first
          time. This can take a minute; the page will wait for it to finish.</span>
      </template>
    </Alert>

    <div class="space-y-4">
      <FormControl type="select" label="Mode" :options="modeOptions" v-model="mode" />
      <Alert v-if="enabled && mode === 'DetectionOnly'" title="Detection only" theme="yellow" :dismissible="false">
        <template #description>
          <span class="text-ink-gray-6 text-p-sm">Attacks are <b>logged, not blocked</b>. Review the WAF analytics,
            add exclusions for any false positives, then switch to <b>On</b> to enforce.</span>
        </template>
      </Alert>
    </div>

    <div class="gap-4 grid grid-cols-2">
      <FormControl type="select" label="Paranoia level" :options="PARANOIA_OPTIONS" v-model="paranoia" />
      <FormControl type="number" label="Anomaly threshold" min="1" v-model="inboundThreshold" />
    </div>

    <div class="gap-4 grid grid-cols-2 items-start">
      <FormControl type="text" label="Max inspected body size" placeholder="50m" v-model="bodyLimit" />
      <Switch class="mt-6" label="Inspect responses"
        description="Scan outbound responses for leaks. Adds latency."
        :model-value="inspectResponses" @update:model-value="(v) => (inspectResponses = v)" />
    </div>

    <FormControl type="textarea" label="Exclusions" :rows="4" v-model="exclusionsText"
      placeholder="One SecLang rule per line, e.g. SecRuleRemoveById 942100" />

    <FormControl type="textarea" label="Exempt paths" :rows="3" v-model="exemptPathsText"
      placeholder="One path prefix per line, e.g. /api/method/frappe.ping" />

    <ErrorMessage v-if="error" :message="error" />

    <div class="flex justify-end">
      <Button variant="solid" :loading="saving" @click="save">Save changes</Button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { Alert, Button, ErrorMessage, FormControl, Switch, toast } from 'frappe-ui'
import { settingsApi } from '@/api/settings'

const PARANOIA_OPTIONS = [
  { label: '1 — fewest false positives', value: 1 },
  { label: '2', value: 2 },
  { label: '3', value: 3 },
  { label: '4 — most aggressive', value: 4 },
]

const loading = ref(true)
const saving = ref(false)
const error = ref('')

const enabled = ref(false)
const installed = ref(false)
const mode = ref('DetectionOnly')
const modes = ref(['Off', 'DetectionOnly', 'On'])
const paranoia = ref(1)
const inboundThreshold = ref(5)
const bodyLimit = ref('50m')
const inspectResponses = ref(false)
const exclusionsText = ref('')
const exemptPathsText = ref('')

const modeOptions = computed(() => modes.value.map((m) => ({ label: m, value: m })))

function linesToArray(text) {
  return text.split('\n').map((line) => line.trim()).filter(Boolean)
}

function validate() {
  const threshold = Number(inboundThreshold.value)
  if (!Number.isInteger(threshold) || threshold < 1) return 'Anomaly threshold must be a positive whole number.'
  if (!bodyLimit.value.trim()) return 'Max inspected body size is required (e.g. 50m).'
  return ''
}

async function save() {
  error.value = validate()
  if (error.value) return

  saving.value = true
  try {
    const payload = {
      enabled: enabled.value,
      mode: mode.value,
      paranoia: Number(paranoia.value),
      inbound_threshold: Number(inboundThreshold.value),
      body_limit: bodyLimit.value.trim(),
      inspect_responses: inspectResponses.value,
      exclusions: linesToArray(exclusionsText.value),
      exempt_paths: linesToArray(exemptPathsText.value),
    }
    const result = await settingsApi.update({ waf: payload })
    if (!result.ok) {
      error.value = result.error || 'Failed to save.'
      return
    }
    toast.success('WAF updated')
    if (result.nginx_error) toast.error(result.nginx_error)
    installed.value = installed.value || enabled.value
  } catch (e) {
    error.value = e.message || 'Failed to save.'
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  try {
    const data = await settingsApi.get()
    const waf = data.waf || {}
    enabled.value = !!waf.enabled
    installed.value = !!waf.installed
    if (Array.isArray(waf.modes) && waf.modes.length) modes.value = waf.modes
    mode.value = waf.mode || 'DetectionOnly'
    paranoia.value = waf.paranoia || 1
    inboundThreshold.value = waf.inbound_threshold ?? 5
    bodyLimit.value = waf.body_limit || '50m'
    inspectResponses.value = !!waf.inspect_responses
    exclusionsText.value = (waf.exclusions || []).join('\n')
    exemptPathsText.value = (waf.exempt_paths || []).join('\n')
  } finally {
    loading.value = false
  }
})
</script>
