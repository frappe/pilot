<script setup lang="ts">
import { computed, ref } from 'vue'
import { Button, Checkbox, Dialog, ErrorMessage, Select, TextInput } from 'frappe-ui'

import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'
import { formatTime } from '@/utils/backup'
import { cronToPicks, picksToCron } from '@/utils/cron'

interface Props {
  siteName: string
}

const props = defineProps<Props>()
const emit = defineEmits(['saved'])

const FREQ_OPTIONS = [
  { label: 'Daily', value: 'daily' },
  { label: 'Weekly', value: 'weekly' },
  { label: 'Monthly', value: 'monthly' },
]
const SCHEME_OPTIONS = [
  { label: 'GFS (daily / weekly / monthly / yearly)', value: 'gfs' },
  { label: 'FIFO (keep newest N)', value: 'fifo' },
]
const weekdayOptions = [
  'Sunday',
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
].map((label, value) => ({ label, value }))
// Cap at 28 so the chosen day exists in every month.
const monthDayOptions = Array.from({ length: 28 }, (_, i) => ({ label: `${i + 1}`, value: i + 1 }))
const hourOptions = Array.from({ length: 24 }, (_, h) => ({ label: formatTime(h), value: h }))

const show = ref(false)
const isEnabled = ref(false)
const saving = ref(false)
const error = ref('')

const frequency = ref('daily')
const weekday = ref(0)
const monthDay = ref(1)
const hour = ref(2)
const minute = ref(0)
const scheme = ref('gfs')
const keepLast = ref(7)
const keepDaily = ref(7)
const keepWeekly = ref(5)
const keepMonthly = ref(12)
const keepYearly = ref(5)

const schemeHint = computed(() =>
  scheme.value === 'fifo'
    ? 'Keeps the newest N backups; older ones are pruned.'
    : 'Keeps recent daily, weekly, monthly and yearly backups; the rest are pruned.',
)

// The pickers hold local time; the server stores the schedule in UTC.
const cron = computed(() =>
  picksToCron({
    frequency: frequency.value,
    weekday: weekday.value,
    monthDay: monthDay.value,
    hour: hour.value,
    minute: minute.value,
  }),
)

const hourPick = computed({
  get: () => hour.value,
  set: (value) => {
    hour.value = value
    minute.value = 0
  },
})

const applySchedule = (schedule) => {
  if (!schedule) return
  const picks = cronToPicks(schedule)
  frequency.value = picks.frequency
  weekday.value = picks.weekday
  monthDay.value = picks.monthDay
  hour.value = picks.hour
  minute.value = picks.minute
}

const applyRetention = (retention) => {
  if (!retention) return
  scheme.value = retention.scheme || 'gfs'
  keepLast.value = retention.keep_last ?? 7
  keepDaily.value = retention.keep_daily ?? 7
  keepWeekly.value = retention.keep_weekly ?? 5
  keepMonthly.value = retention.keep_monthly ?? 12
  keepYearly.value = retention.keep_yearly ?? 5
}

const open = async () => {
  error.value = ''
  show.value = true
  try {
    const data = await sitesApi.backups.schedule.get(props.siteName)
    isEnabled.value = !!data.schedule
    applySchedule(data.schedule)
    applyRetention(data.retention)
  } catch (e) {
    error.value = e.message || 'Could not load backup settings.'
  }
}

// Save both enables/updates (checkbox on) and turns off (checkbox off).
const save = async () => {
  saving.value = true
  error.value = ''
  try {
    if (isEnabled.value) {
      const result = await sitesApi.backups.schedule.set(props.siteName, {
        schedule: cron.value,
        retention: retentionPayload(),
      })
      if (result.error) {
        error.value = apiErrorMessage(result, 'Could not save.')
        return
      }
    } else {
      const response = await sitesApi.backups.schedule.remove(props.siteName)
      if (!response.ok) {
        error.value = apiErrorMessage(await response.json(), 'Could not save.')
        return
      }
    }
    show.value = false
    emit('saved')
  } catch (e) {
    error.value = e.message || 'Could not save.'
  } finally {
    saving.value = false
  }
}

const retentionPayload = () => {
  return {
    scheme: scheme.value,
    keep_last: keepLast.value,
    keep_daily: keepDaily.value,
    keep_weekly: keepWeekly.value,
    keep_monthly: keepMonthly.value,
    keep_yearly: keepYearly.value,
  }
}

defineExpose({ open })
</script>

<template>
  <Dialog v-model="show" title="Configure automated backups">
    <div class="space-y-5">
      <Checkbox v-model="isEnabled" label="Enable automated backups" />

      <fieldset
        :disabled="!isEnabled"
        class="space-y-5 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
      >
        <div
          class="gap-4 grid"
          :class="frequency === 'daily' ? 'md:grid-cols-2' : 'md:grid-cols-3'"
        >
          <Select label="Frequency" v-model="frequency" :options="FREQ_OPTIONS" />
          <Select
            v-if="frequency === 'weekly'"
            label="Day of week"
            v-model.number="weekday"
            :options="weekdayOptions"
          />
          <Select
            v-if="frequency === 'monthly'"
            label="Day of month"
            v-model.number="monthDay"
            :options="monthDayOptions"
          />
          <Select label="Time" v-model.number="hourPick" :options="hourOptions" />
        </div>

        <div class="space-y-4 pt-5 border-t border-outline-gray-1">
          <div class="space-y-1.5">
            <Select label="Retention" v-model="scheme" :options="SCHEME_OPTIONS" />
            <p class="text-ink-gray-5 text-p-sm">{{ schemeHint }}</p>
          </div>

          <TextInput
            v-if="scheme === 'fifo'"
            label="Backups to keep"
            type="number"
            min="0"
            v-model.number="keepLast"
          />
          <div v-else class="gap-4 grid grid-cols-2 sm:grid-cols-4">
            <TextInput label="Daily" type="number" min="0" v-model.number="keepDaily" />
            <TextInput label="Weekly" type="number" min="0" v-model.number="keepWeekly" />
            <TextInput label="Monthly" type="number" min="0" v-model.number="keepMonthly" />
            <TextInput label="Yearly" type="number" min="0" v-model.number="keepYearly" />
          </div>
        </div>
      </fieldset>

      <ErrorMessage v-if="error" :message="error" />
    </div>

    <template #actions>
      <div class="flex justify-end gap-2">
        <Button variant="ghost" @click="show = false">Cancel</Button>
        <Button variant="solid" :loading="saving" @click="save">Save</Button>
      </div>
    </template>
  </Dialog>
</template>
