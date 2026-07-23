<template>
  <div>
    <div class="flex sm:flex-row flex-col sm:justify-between sm:items-center gap-3">
      <div v-if="!titleless">
        <p class="font-medium text-ink-gray-8 text-sm">{{ title }}</p>
        <p class="mt-0.5 text-ink-gray-5 text-sm">
          <template v-if="disabled">{{ disabledHint }}</template>
          <template v-else>{{ enabledHint }}</template>
        </p>
      </div>
      <div class="flex items-center gap-2 shrink-0">
        <Button v-if="disabled" size="sm" :loading="loading" @click="enable">Enable {{ noun }}</Button>
        <Dropdown v-else :options="scheduleOptions" placement="bottom-end">
          <template #default="{ open }">
            <Button variant="subtle" size="sm" :loading="loading" :active="open">
              <template #suffix><span class="size-4 lucide-chevron-down" /></template>
              {{ currentScheduleLabel }}
            </Button>
          </template>
        </Dropdown>
        <slot name="actions" />
      </div>
    </div>

    <ErrorMessage v-if="error" :message="error" class="mt-2" />
  </div>

  <!-- Custom schedule dialog -->
  <Dialog v-model="showCustomDialog" :options="{ title: `Custom ${noun} schedule`, size: 'sm' }">
    <template #body-content>
      <div class="space-y-4">
        <div class="space-y-1.5">
          <p class="font-medium text-ink-gray-7 text-sm">Frequency</p>
          <Select v-model="schedFrequency" :options="FREQ_OPTIONS" class="w-full" />
        </div>
        <div v-if="schedFrequency === 'weekly'" class="space-y-1.5">
          <p class="font-medium text-ink-gray-7 text-sm">Day of week</p>
          <Select v-model="schedWeekday" :options="WEEKDAY_OPTIONS" class="w-full" />
        </div>
        <div v-if="schedFrequency === 'monthly'" class="space-y-1.5">
          <p class="font-medium text-ink-gray-7 text-sm">Day of month</p>
          <Select v-model="schedMonthDay" :options="monthDayOptions" class="w-full" />
        </div>
        <div class="space-y-1.5">
          <p class="font-medium text-ink-gray-7 text-sm">Time</p>
          <Select v-model="schedHour" :options="hourOptions" class="w-full" />
        </div>
        <p v-if="retentionHint" class="text-ink-gray-4 text-p-sm">{{ retentionHint }}</p>
        <ErrorMessage v-if="error" :message="error" />
      </div>
      <div class="flex justify-end gap-2 mt-4">
        <Button variant="ghost" @click="showCustomDialog = false">Cancel</Button>
        <Button variant="solid" :loading="scheduleSaving" @click="saveCustomSchedule">Save schedule</Button>
      </div>
    </template>
  </Dialog>

  <!-- Disable confirmation -->
  <Dialog v-model="showDisableConfirm" :options="{ title: `Disable ${noun}`, size: 'sm' }">
    <template #body-content>
      <p class="text-ink-gray-7 text-sm">{{ disableBody }}</p>
      <div class="flex justify-end gap-2 mt-4">
        <Button variant="ghost" @click="showDisableConfirm = false">Cancel</Button>
        <Button variant="solid" theme="red" :loading="loading" @click="disable">Disable</Button>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { Button, Dialog, Dropdown, ErrorMessage, Select } from 'frappe-ui'

const props = defineProps({
  title: { type: String, default: '' },
  // Lowercase plural noun used in button/dialog copy, e.g. "backups", "snapshots".
  noun: { type: String, required: true },
  enabledHint: { type: String, default: '' },
  disabledHint: { type: String, default: '' },
  disableBody: { type: String, required: true },
  retentionHint: { type: String, default: '' },
  // Hide the title/hint text, rendering only the enable button or schedule dropdown.
  titleless: { type: Boolean, default: false },
  fetchSchedule: { type: Function, required: true }, // () => Promise<{ schedule: string|null }>
  setSchedule: { type: Function, required: true }, // (cron: string) => Promise<void>, throws on failure
  removeSchedule: { type: Function, required: true }, // () => Promise<void>, throws on failure
})

const FREQ_OPTIONS = [
  { label: 'Daily', value: 'daily' },
  { label: 'Weekly', value: 'weekly' },
  { label: 'Monthly', value: 'monthly' },
]

const WEEKDAY_OPTIONS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
  .map((label, value) => ({ label, value }))

const monthDayOptions = Array.from({ length: 31 }, (_, i) => ({ label: `${i + 1}`, value: i + 1 }))

const hourOptions = Array.from({ length: 24 }, (_, h) => {
  const label = h === 0 ? '12:00 AM' : h < 12 ? `${h}:00 AM` : h === 12 ? '12:00 PM' : `${h - 12}:00 PM`
  return { label, value: h }
})

const WEEKDAY_FULL = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
const PRESET_CRONS = ['0 2 * * *', '0 2 * * 0']

const disabled = ref(true)
const loading = ref(false)
const error = ref('')

const schedulePreset = ref(PRESET_CRONS[0])
const showCustomDialog = ref(false)
const showDisableConfirm = ref(false)
const scheduleSaving = ref(false)
const schedFrequency = ref('daily')
const schedWeekday = ref(0)
const schedMonthDay = ref(1)
const schedHour = ref(2)

function formatHour(h) {
  if (h === 0) return '12:00 AM'
  if (h < 12) return `${h}:00 AM`
  if (h === 12) return '12:00 PM'
  return `${h - 12}:00 PM`
}

const customScheduleLabel = computed(() => {
  const time = formatHour(schedHour.value)
  if (schedFrequency.value === 'weekly') return `Weekly, ${WEEKDAY_FULL[schedWeekday.value]} ${time}`
  if (schedFrequency.value === 'monthly') return `Monthly, ${schedMonthDay.value} ${time}`
  return `Daily, ${time}`
})

const currentScheduleLabel = computed(() => {
  if (schedulePreset.value === 'custom') return customScheduleLabel.value
  if (schedulePreset.value === '0 2 * * *') return 'Daily, 2:00 AM'
  if (schedulePreset.value === '0 2 * * 0') return 'Weekly, Sunday 2:00 AM'
  return 'Custom'
})

const scheduleOptions = computed(() => {
  const customEntry = {
    label: schedulePreset.value === 'custom' ? customScheduleLabel.value : 'Custom...',
    onClick: () => { showCustomDialog.value = true },
  }
  const presets = [
    { label: 'Daily, 2:00 AM', onClick: () => setPreset('0 2 * * *') },
    { label: 'Weekly, Sunday 2:00 AM', onClick: () => setPreset('0 2 * * 0') },
  ]
  const disableEntry = { label: `Disable ${props.noun}`, theme: 'red', onClick: () => { showDisableConfirm.value = true } }
  return schedulePreset.value === 'custom'
    ? [customEntry, ...presets, disableEntry]
    : [...presets, customEntry, disableEntry]
})

const schedCron = computed(() => {
  const h = schedHour.value
  if (schedFrequency.value === 'weekly') return `0 ${h} * * ${schedWeekday.value}`
  if (schedFrequency.value === 'monthly') return `0 ${h} ${schedMonthDay.value} * *`
  return `0 ${h} * * *`
})

function parseCronToState(cron) {
  const [, h, dom, , dow] = cron.split(' ')
  schedHour.value = isNaN(parseInt(h)) ? 0 : parseInt(h)
  if (dom !== '*') { schedFrequency.value = 'monthly'; schedMonthDay.value = parseInt(dom) || 1 }
  else if (dow !== '*') { schedFrequency.value = 'weekly'; schedWeekday.value = parseInt(dow) || 0 }
  else schedFrequency.value = 'daily'
}

async function load() {
  try {
    const data = await props.fetchSchedule()
    if (!data.schedule) { disabled.value = true; return }
    disabled.value = false
    parseCronToState(data.schedule)
    schedulePreset.value = PRESET_CRONS.includes(data.schedule) ? data.schedule : 'custom'
  } catch (e) {
    error.value = e.message || 'Failed to load schedule.'
  }
}

async function setPreset(cron) {
  error.value = ''
  try {
    await props.setSchedule(cron)
    schedulePreset.value = cron
    disabled.value = false
  } catch (e) {
    error.value = e.message || 'Failed to save schedule.'
  }
}

async function saveCustomSchedule() {
  error.value = ''
  scheduleSaving.value = true
  try {
    await props.setSchedule(schedCron.value)
    schedulePreset.value = 'custom'
    disabled.value = false
    showCustomDialog.value = false
  } catch (e) {
    error.value = e.message || 'Failed to save schedule.'
  } finally {
    scheduleSaving.value = false
  }
}

async function disable() {
  error.value = ''
  loading.value = true
  try {
    await props.removeSchedule()
    disabled.value = true
    showDisableConfirm.value = false
  } catch (e) {
    error.value = e.message || `Failed to disable ${props.noun}.`
  } finally {
    loading.value = false
  }
}

async function enable() {
  error.value = ''
  loading.value = true
  try {
    await props.setSchedule(PRESET_CRONS[0])
    disabled.value = false
    schedulePreset.value = PRESET_CRONS[0]
  } catch (e) {
    error.value = e.message || `Failed to enable ${props.noun}.`
  } finally {
    loading.value = false
  }
}

onMounted(load)

defineExpose({ disabled, currentScheduleLabel, loading, enable })
</script>
