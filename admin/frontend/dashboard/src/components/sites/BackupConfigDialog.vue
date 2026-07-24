<template>
  <Dialog v-model="show" :options="{ title: 'Configure automated backups', size: 'lg' }">
    <template #body-content>
      <div class="space-y-5">
        <Checkbox v-model="isEnabled" label="Enable automated backups" />

        <template v-if="isEnabled">
          <div
            class="gap-4 grid grid-cols-1"
            :class="frequency === 'daily' ? 'sm:grid-cols-2' : 'sm:grid-cols-3'"
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
            <Select label="Time" v-model.number="hour" :options="hourOptions" />
          </div>

          <div class="space-y-4 pt-5 border-t border-outline-gray-1">
            <div class="space-y-1.5">
              <Select label="Retention" v-model="scheme" :options="SCHEME_OPTIONS" />
              <p class="text-ink-gray-5 text-p-sm">{{ schemeHint }}</p>
            </div>

            <FormControl
              v-if="scheme === 'fifo'"
              label="Backups to keep"
              type="number"
              min="0"
              v-model.number="keepLast"
            />
            <div v-else class="gap-4 grid grid-cols-2 sm:grid-cols-4">
              <FormControl label="Daily" type="number" min="0" v-model.number="keepDaily" />
              <FormControl label="Weekly" type="number" min="0" v-model.number="keepWeekly" />
              <FormControl label="Monthly" type="number" min="0" v-model.number="keepMonthly" />
              <FormControl label="Yearly" type="number" min="0" v-model.number="keepYearly" />
            </div>
          </div>
        </template>

        <ErrorMessage v-if="error" :message="error" />
      </div>

      <div class="flex justify-end gap-2 mt-6">
        <Button variant="ghost" @click="show = false">Cancel</Button>
        <Button variant="solid" :loading="saving" @click="save">Save</Button>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Button, Checkbox, Dialog, ErrorMessage, FormControl, Select } from 'frappe-ui'
import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'
import { formatHour } from '@/utils/backup'

const props = defineProps({ siteName: { type: String, required: true } })
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
const hourOptions = Array.from({ length: 24 }, (_, h) => ({ label: formatHour(h), value: h }))

const show = ref(false)
const isEnabled = ref(false)
const saving = ref(false)
const error = ref('')

const frequency = ref('daily')
const weekday = ref(0)
const monthDay = ref(1)
const hour = ref(2)
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

const cron = computed(() => {
  if (frequency.value === 'weekly') return `0 ${hour.value} * * ${weekday.value}`
  if (frequency.value === 'monthly') return `0 ${hour.value} ${monthDay.value} * *`
  return `0 ${hour.value} * * *`
})

function applySchedule(schedule) {
  if (!schedule) return
  const [, h, dom, , dow] = schedule.split(' ')
  hour.value = parseInt(h) || 0
  if (dom !== '*') {
    frequency.value = 'monthly'
    monthDay.value = parseInt(dom) || 1
  } else if (dow !== '*') {
    frequency.value = 'weekly'
    weekday.value = parseInt(dow) || 0
  } else frequency.value = 'daily'
}

function applyRetention(retention) {
  if (!retention) return
  scheme.value = retention.scheme || 'gfs'
  keepLast.value = retention.keep_last ?? 7
  keepDaily.value = retention.keep_daily ?? 7
  keepWeekly.value = retention.keep_weekly ?? 5
  keepMonthly.value = retention.keep_monthly ?? 12
  keepYearly.value = retention.keep_yearly ?? 5
}

async function open() {
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
async function save() {
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

function retentionPayload() {
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
