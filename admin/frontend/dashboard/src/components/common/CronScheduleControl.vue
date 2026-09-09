<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Button, Dialog, Dropdown, ErrorMessage, Select } from 'frappe-ui'

import { formatTime, WEEKDAYS } from '@/utils/backup'
import { cronToPicks, picksToCron } from '@/utils/cron'

interface Props {
  title?: string
  // Lowercase plural noun used in button/dialog copy, e.g. "backups", "snapshots".
  noun: string
  enabledHint?: string
  disabledHint?: string
  disableBody: string
  retentionHint?: string
  // Hide the title/hint text, rendering only the enable button or schedule dropdown.
  titleless?: boolean
  fetchSchedule: () => Promise<{ schedule: string | null }>
  setSchedule: (cron: string) => Promise<void>
  removeSchedule: () => Promise<void>
}

const props = withDefaults(defineProps<Props>(), {
  title: '',
  enabledHint: '',
  disabledHint: '',
  retentionHint: '',
  titleless: false,
})

const FREQ_OPTIONS = [
  { label: 'Daily', value: 'daily' },
  { label: 'Weekly', value: 'weekly' },
  { label: 'Monthly', value: 'monthly' },
]

const WEEKDAY_OPTIONS = WEEKDAYS.map((label, value) => ({ label, value }))

const monthDayOptions = Array.from({ length: 31 }, (_, i) => ({ label: `${i + 1}`, value: i + 1 }))

const hourOptions = Array.from({ length: 24 }, (_, h) => ({ label: formatTime(h), value: h }))

// Local-time presets; the server stores their UTC equivalents.
const PRESETS = [
  { label: 'Daily, 2:00 AM', picks: { frequency: 'daily', weekday: 0, monthDay: 1, hour: 2, minute: 0 } },
  { label: 'Weekly, Sunday 2:00 AM', picks: { frequency: 'weekly', weekday: 0, monthDay: 1, hour: 2, minute: 0 } },
]

const presetCron = (index) => picksToCron(PRESETS[index].picks)

const matchingPreset = (picks) =>
  PRESETS.findIndex(
    (preset) =>
      preset.picks.frequency === picks.frequency &&
      preset.picks.hour === picks.hour &&
      preset.picks.minute === picks.minute &&
      (picks.frequency !== 'weekly' || preset.picks.weekday === picks.weekday),
  )

const disabled = ref(true)
const loading = ref(false)
const error = ref('')

const schedulePreset = ref(0)
const showCustomDialog = ref(false)
const showDisableConfirm = ref(false)
const scheduleSaving = ref(false)
const schedFrequency = ref('daily')
const schedWeekday = ref(0)
const schedMonthDay = ref(1)
const schedHour = ref(2)
const schedMinute = ref(0)

const schedHourPick = computed({
  get: () => schedHour.value,
  set: (value) => {
    schedHour.value = value
    schedMinute.value = 0
  },
})

const customScheduleLabel = computed(() => {
  const time = formatTime(schedHour.value, schedMinute.value)
  if (schedFrequency.value === 'weekly')
    return `Weekly, ${WEEKDAYS[schedWeekday.value]} ${time}`
  if (schedFrequency.value === 'monthly') return `Monthly, ${schedMonthDay.value} ${time}`
  return `Daily, ${time}`
})

const currentScheduleLabel = computed(() =>
  schedulePreset.value === 'custom'
    ? customScheduleLabel.value
    : PRESETS[schedulePreset.value]?.label || 'Custom',
)

const scheduleOptions = computed(() => {
  const customEntry = {
    label: schedulePreset.value === 'custom' ? customScheduleLabel.value : 'Custom...',
    onClick: () => {
      showCustomDialog.value = true
    },
  }
  const presets = PRESETS.map((preset, index) => ({
    label: preset.label,
    onClick: () => setPreset(index),
  }))
  const disableEntry = {
    label: `Disable ${props.noun}`,
    theme: 'red',
    onClick: () => {
      showDisableConfirm.value = true
    },
  }
  return schedulePreset.value === 'custom'
    ? [customEntry, ...presets, disableEntry]
    : [...presets, customEntry, disableEntry]
})

// The pickers hold local time; the server stores the schedule in UTC.
const schedCron = computed(() =>
  picksToCron({
    frequency: schedFrequency.value,
    weekday: schedWeekday.value,
    monthDay: schedMonthDay.value,
    hour: schedHour.value,
    minute: schedMinute.value,
  }),
)

const parseCronToState = (cron) => {
  const picks = cronToPicks(cron)
  schedFrequency.value = picks.frequency
  schedWeekday.value = picks.weekday
  schedMonthDay.value = picks.monthDay
  schedHour.value = picks.hour
  schedMinute.value = picks.minute
  return picks
}

const load = async () => {
  try {
    const data = await props.fetchSchedule()
    if (!data.schedule) {
      disabled.value = true
      return
    }
    disabled.value = false
    const matched = matchingPreset(parseCronToState(data.schedule))
    schedulePreset.value = matched === -1 ? 'custom' : matched
  } catch (e) {
    error.value = e.message || 'Failed to load schedule.'
  }
}

const setPreset = async (index) => {
  error.value = ''
  try {
    await props.setSchedule(presetCron(index))
    schedulePreset.value = index
    disabled.value = false
  } catch (e) {
    error.value = e.message || 'Failed to save schedule.'
  }
}

const saveCustomSchedule = async () => {
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

const disable = async () => {
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

const enable = async () => {
  error.value = ''
  loading.value = true
  try {
    await props.setSchedule(presetCron(0))
    disabled.value = false
    schedulePreset.value = 0
  } catch (e) {
    error.value = e.message || `Failed to enable ${props.noun}.`
  } finally {
    loading.value = false
  }
}

onMounted(load)

defineExpose({ disabled, currentScheduleLabel, loading, enable })
</script>

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
        <Button v-if="disabled" :loading="loading" @click="enable"
          >Enable {{ noun }}</Button
        >
        <Dropdown v-else :options="scheduleOptions">
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
  <Dialog v-model="showCustomDialog" :title="`Custom ${noun} schedule`" size="sm">
    <div class="space-y-4">
      <Select label="Frequency" v-model="schedFrequency" :options="FREQ_OPTIONS" class="w-full" />

      <Select
        v-if="schedFrequency === 'weekly'"
        label="Day of week"
        v-model="schedWeekday"
        :options="WEEKDAY_OPTIONS"
        class="w-full"
      />

      <Select
        v-if="schedFrequency === 'monthly'"
        label="Day of month"
        v-model="schedMonthDay"
        :options="monthDayOptions"
        class="w-full"
      />

      <Select label="Time" v-model.number="schedHourPick" :options="hourOptions" class="w-full" />

      <p v-if="retentionHint" class="text-ink-gray-4 text-p-sm">{{ retentionHint }}</p>
      <ErrorMessage v-if="error" :message="error" />
    </div>

    <template #actions>
      <div class="flex justify-end gap-2">
        <Button variant="ghost" @click="showCustomDialog = false">Cancel</Button>
        <Button variant="solid" :loading="scheduleSaving" @click="saveCustomSchedule"
          >Save schedule</Button
        >
      </div>
    </template>
  </Dialog>

  <!-- Disable confirmation -->
  <Dialog v-model="showDisableConfirm" :title="`Disable ${noun}`" size="sm">
    <p class="text-ink-gray-7 text-sm">{{ disableBody }}</p>
    <template #actions>
      <div class="flex justify-end gap-2">
        <Button variant="ghost" @click="showDisableConfirm = false">Cancel</Button>
        <Button variant="solid" theme="red" :loading="loading" @click="disable">Disable</Button>
      </div>
    </template>
  </Dialog>
</template>
