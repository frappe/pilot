<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { RouterLink } from 'vue-router'
import { Button, ErrorMessage, Select, Spinner, TabButtons, TextInput, Textarea, toast } from 'frappe-ui'

import SettingsSwitch from '@/components/settings/SettingsSwitch.vue'
import WafCustomRules from '@/components/settings/WafCustomRules.vue'

import { settingsApi } from '@/api/settings'
import { ruleProblem } from '@/utils/wafRules'
import { useUnsavedChanges } from '@/composables/common/useUnsavedChanges'

// "Paused" leaves the module loaded and idle; the Enable switch drops it from
// the server config. The stored value stays "Off" - only the label differs.
const ACTION_OPTIONS = [
  { label: 'Paused', value: 'Off' },
  { label: 'Log only', value: 'DetectionOnly' },
  { label: 'Block', value: 'On' },
]
const SENSITIVITY_OPTIONS = [
  { label: 'Low', value: 1 },
  { label: 'Medium', value: 2 },
  { label: 'High', value: 3 },
  { label: 'Very High', value: 4 },
]
// CRS paranoia levels.
const SENSITIVITY_HINTS = {
  1: 'Very few false positives. Start here.',
  2: 'Admin tooling may start tripping it.',
  3: 'Expect to add exclusions.',
  4: 'Most coverage, most false positives.',
}
// DetectionOnly's hint lives in the template - it carries a link.
const ACTION_HINTS = {
  Off: 'Loaded but idle. Nothing is inspected.',
  On: 'Matching requests are rejected.',
}

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

const sensitivityHint = computed(() => SENSITIVITY_HINTS[Number(paranoia.value)] || '')

// `installed` only means anything in production, so at most one applies.
const setupNote = computed(() => {
  if (!enabled.value) return ''
  if (!production.value)
    return "Enforced in production only. This bench isn't deployed - run pilot setup production first."
  if (!installed.value)
    return 'ModSecurity is not installed on this host. Redeploy production to install it.'
  return ''
})

const linesToArray = (text) => {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
}

const buildPayload = () => {
  return {
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
}

const savedPayload = ref('')
const dirty = computed(() => JSON.stringify(buildPayload()) !== savedPayload.value)

// After `dirty`: useUnsavedChanges evaluates its argument immediately, and a
// route-level guard never fires for the shell's param-only navigation.
useUnsavedChanges(dirty)

const warnIfDirty = (event) => {
  if (!dirty.value) return
  event.preventDefault()
  event.returnValue = ''
}
onMounted(() => window.addEventListener('beforeunload', warnIfDirty))
onUnmounted(() => window.removeEventListener('beforeunload', warnIfDirty))

const thresholdError = computed(() => {
  const threshold = Number(inboundThreshold.value)
  if (Number.isInteger(threshold) && threshold >= 1) return ''
  return 'Must be a positive whole number.'
})
const canSave = computed(
  () =>
    !thresholdError.value &&
    Boolean(bodyLimit.value.trim()) &&
    !customRules.value.some((rule) => ruleProblem(rule)),
)

const save = async () => {
  saving.value = true
  try {
    const payload = buildPayload()
    const result = await settingsApi.update({ waf: payload })
    if (!result.ok) {
      error.value = result.error || 'Failed to save.'
      return
    }
    savedPayload.value = JSON.stringify(payload)
    toast.success('Web application firewall updated')
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
    // TabButtons matches by Object.is; a stringy "2" would fall back to Low.
    paranoia.value = Number(waf.paranoia) || 1
    inboundThreshold.value = waf.inbound_threshold ?? 5
    bodyLimit.value = waf.body_limit || '50m'
    inspectResponses.value = !!waf.inspect_responses
    exclusionsText.value = (waf.exclusions || []).join('\n')
    exemptPathsText.value = (waf.exempt_paths || []).join('\n')
    customRules.value = waf.custom_rules || []
    ruleFields.value = waf.rule_fields || []
    ruleOperators.value = waf.rule_operators || []
    ruleActions.value = waf.rule_actions || []
    // Same builder as the save payload, so normalisation is not an edit.
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

  <div v-else class="space-y-12">
    <div class="space-y-4">
      <SettingsSwitch
        label="Enable web application firewall"
        description="Inspects request contents for SQLi, XSS and path traversal, across all sites and the admin."
        :model-value="enabled"
        @update:model-value="(v) => (enabled = v)"
      />

      <p v-if="setupNote" class="flex items-start gap-1.5 text-ink-amber-6 text-p-sm">
        <span class="shrink-0 mt-0.5 size-3.5 lucide-triangle-alert" />
        <span>{{ setupNote }}</span>
      </p>

      <div class="items-start gap-4 grid md:grid-cols-2">
        <div class="space-y-1.5">
          <Select label="Action" :options="ACTION_OPTIONS" v-model="mode" />
          <p v-if="mode === 'DetectionOnly'" class="text-ink-gray-5 text-p-sm">
            Matches are logged, not blocked. Review
            <RouterLink
              class="text-ink-gray-7 hover:text-ink-gray-8"
              :to="{ name: 'Analytics', query: { view: 'system', window: '1h' } }"
              >the firewall analytics</RouterLink
            >, then switch to Block.
          </p>

          <p v-else class="text-ink-gray-5 text-p-sm">{{ ACTION_HINTS[mode] }}</p>
        </div>

        <div class="space-y-1.5">
          <span class="block text-ink-gray-5">Sensitivity</span>
          <TabButtons :options="SENSITIVITY_OPTIONS" v-model="paranoia" />
          <p class="text-ink-gray-5 text-p-sm">{{ sensitivityHint }}</p>
        </div>
      </div>
    </div>

    <!-- Visible with the WAF off: rules are staged first and save independently
         of `enabled`. -->
    <WafCustomRules
      v-model="customRules"
      :fields="ruleFields"
      :operators="ruleOperators"
      :actions="ruleActions"
    />

    <details class="group">
      <summary
        class="flex items-center gap-1.5 pr-1.5 rounded-1 w-fit text-ink-gray-6 cursor-pointer select-none"
        @click="(e) => e.currentTarget.blur()"
      >
        <span
          class="size-4 transition-transform group-open:rotate-90 lucide-chevron-right" />Advanced
      </summary>

      <div class="space-y-4 mt-4">
        <div class="gap-4 grid md:grid-cols-2 items-start">
          <div class="space-y-1.5">
            <TextInput
              type="number"
              label="Anomaly threshold"
              min="1"
              v-model="inboundThreshold"
            />
            <p v-if="thresholdError" class="text-ink-red-5 text-p-sm">{{ thresholdError }}</p>
            <p v-else class="text-ink-gray-5 text-p-sm">
              Score needed before Action applies. Sensitivity raises scores, so the two compound.
            </p>
          </div>

          <div class="space-y-1.5">
            <TextInput label="Max inspected body size" v-model="bodyLimit" />
            <p class="text-ink-gray-5 text-p-sm">Number with a k, m or g suffix, e.g. 50m.</p>
          </div>
        </div>

        <div class="space-y-1.5">
          <Textarea
            label="Exempt paths"
            :rows="3"
            placeholder="/api/method/frappe.ping"
            v-model="exemptPathsText"
          />
          <p class="text-ink-gray-5 text-p-sm">
            One path prefix per line. Requests under these skip the firewall entirely.
          </p>
        </div>

        <div class="space-y-1.5">
          <Textarea
            label="Rule exclusions (SecLang)"
            :rows="3"
            placeholder="SecRuleRemoveById 942100"
            v-model="exclusionsText"
          />
          <p class="text-ink-gray-5 text-p-sm">
            One SecLang directive per line. Turns a managed rule off everywhere.
          </p>
        </div>

        <SettingsSwitch
          label="Inspect responses"
          description="Scan outbound responses for leaks. Adds latency."
          :model-value="inspectResponses"
          @update:model-value="(v) => (inspectResponses = v)"
        />
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
