<template>
  <div v-if="loading" class="flex justify-center items-center h-40">
    <span class="size-5 text-ink-gray-4 animate-spin lucide-loader-circle"></span>
  </div>
  <div v-else class="space-y-8">
    <div class="space-y-6">
      <Switch label="Enable WAF"
        description="ModSecurity + OWASP Core Rule Set inspects request contents (SQLi, XSS, path traversal) for all sites and the admin."
        :model-value="enabled" @update:model-value="(v) => (enabled = v)" />

      <Alert v-if="!production" title="Not enforced yet" theme="yellow" :dismissible="false">
        <template #description>
          <span class="text-ink-gray-6 text-p-sm">The WAF takes effect only in production (it's applied by nginx).
            This bench isn't deployed, so nothing is enforced until you run
            <span class="font-mono text-xs">bench setup production</span>.</span>
        </template>
      </Alert>

      <Alert v-if="production && enabled && !installed" title="ModSecurity not installed" theme="yellow" :dismissible="false">
        <template #description>
          <span class="text-ink-gray-6 text-p-sm">The ModSecurity module isn't installed on this host, so the WAF
            stays inactive even when enabled. Redeploy production
            (<span class="font-mono text-xs">bench setup production</span>) to install it, then it takes effect.</span>
        </template>
      </Alert>

      <div>
        <p class="mb-3 font-medium text-ink-gray-8 text-base leading-normal">Managed ruleset (OWASP CRS)</p>
        <div class="gap-4 grid grid-cols-2">
          <FormControl type="select" label="Action" :options="ACTION_OPTIONS" v-model="mode" />
          <FormControl type="select" label="Sensitivity" :options="SENSITIVITY_OPTIONS" v-model="paranoia" />
        </div>
      </div>

      <Alert v-if="enabled && mode === 'DetectionOnly'" title="Log only" theme="yellow" :dismissible="false">
        <template #description>
          <span class="text-ink-gray-6 text-p-sm">Matches (managed <b>and</b> custom rules) are <b>logged, not
            blocked</b>. Review the WAF analytics, then switch Action to <b>Block</b> to enforce.</span>
        </template>
      </Alert>
    </div>

    <WafCustomRules :rules="customRules" :fields="ruleFields" :operators="ruleOperators" :actions="ruleActions" />

    <details class="group">
      <summary class="flex items-center gap-1.5 text-ink-gray-6 text-sm cursor-pointer select-none">
        <span class="size-4 transition-transform group-open:rotate-90 lucide-chevron-right"></span>Advanced
      </summary>
      <div class="space-y-4 mt-4">
        <div class="gap-4 grid grid-cols-2 items-start">
          <FormControl type="number" label="Anomaly threshold" min="1" v-model="inboundThreshold" />
          <FormControl type="text" label="Max inspected body size" placeholder="50m" v-model="bodyLimit" />
        </div>
        <Switch label="Inspect responses" description="Scan outbound responses for leaks. Adds latency."
          :model-value="inspectResponses" @update:model-value="(v) => (inspectResponses = v)" />
        <FormControl type="textarea" label="Exclusions" :rows="3" v-model="exclusionsText"
          placeholder="One SecLang rule per line, e.g. SecRuleRemoveById 942100" />
        <FormControl type="textarea" label="Exempt paths" :rows="2" v-model="exemptPathsText"
          placeholder="One path prefix per line, e.g. /api/method/frappe.ping" />
      </div>
    </details>

    <ErrorMessage v-if="error" :message="error" />

    <div class="flex justify-end">
      <Button variant="solid" :loading="saving" @click="save">Save changes</Button>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Alert, Button, ErrorMessage, FormControl, Switch, toast } from 'frappe-ui'
import WafCustomRules from '@/components/settings/WafCustomRules.vue'
import { settingsApi } from '@/api/settings'

const ACTION_OPTIONS = [
  { label: 'Off', value: 'Off' },
  { label: 'Log only', value: 'DetectionOnly' },
  { label: 'Block', value: 'On' },
]
const SENSITIVITY_OPTIONS = [
  { label: 'Low', value: 1 }, { label: 'Medium', value: 2 },
  { label: 'High', value: 3 }, { label: 'Very High', value: 4 },
]

const loading = ref(true)
const saving = ref(false)
const error = ref('')

const enabled = ref(false)
const installed = ref(false)
const production = ref(true)
const mode = ref('DetectionOnly')
const paranoia = ref(1)
const inboundThreshold = ref(5)
const bodyLimit = ref('50m')
const inspectResponses = ref(false)
const exclusionsText = ref('')
const exemptPathsText = ref('')
const customRules = ref([])
const ruleFields = ref([])
const ruleOperators = ref([])
const ruleActions = ref([])

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
      custom_rules: customRules.value,
    }
    const result = await settingsApi.update({ waf: payload })
    if (!result.ok) {
      error.value = result.error || 'Failed to save.'
      return
    }
    toast.success('WAF updated')
    if (result.nginx_error) toast.error(result.nginx_error)
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
    const waf = data.waf || {}
    enabled.value = !!waf.enabled
    installed.value = !!waf.installed
    mode.value = waf.mode || 'DetectionOnly'
    paranoia.value = waf.paranoia || 1
    inboundThreshold.value = waf.inbound_threshold ?? 5
    bodyLimit.value = waf.body_limit || '50m'
    inspectResponses.value = !!waf.inspect_responses
    exclusionsText.value = (waf.exclusions || []).join('\n')
    exemptPathsText.value = (waf.exempt_paths || []).join('\n')
    customRules.value = waf.custom_rules || []
    ruleFields.value = waf.rule_fields || []
    ruleOperators.value = waf.rule_operators || []
    ruleActions.value = waf.rule_actions || []
  } finally {
    loading.value = false
  }
})
</script>
