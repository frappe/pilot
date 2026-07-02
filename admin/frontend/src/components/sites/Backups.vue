<template>
  <div class="space-y-4 mt-5">
    <div class="flex sm:flex-row flex-col sm:justify-between sm:items-center gap-3">
      <div>
        <p class="font-medium text-ink-gray-8 text-sm">Automatic backups</p>
        <p class="mt-0.5 text-ink-gray-5 text-sm">
          <template v-if="backupsDisabled">Automatic backups are disabled.</template>
          <template v-else>Taken on a schedule and kept for 30 days.</template>
        </p>
      </div>
      <div class="flex items-center gap-2 shrink-0">
        <Button v-if="backupsDisabled" size="sm" :loading="scheduleLoading" @click="enableBackups">
          Enable backups
        </Button>
        <Dropdown v-else :options="scheduleOptions" placement="bottom-end">
          <template #default="{ open }">
            <Button variant="subtle" size="sm" :loading="scheduleLoading" :active="open">
              <template #suffix><span class="size-4 lucide-chevron-down" /></template>
              {{ currentScheduleLabel }}
            </Button>
          </template>
        </Dropdown>
        <Button size="sm" :loading="backingUp" @click="backupNow">
          <template #prefix><span class="size-4 lucide-archive" /></template>
          Back up now
        </Button>
      </div>
    </div>

    <ErrorMessage v-if="error" :message="error" />

    <div :class="backups.length ? '' : 'rounded-xl border border-dashed border-outline-gray-2'">
      <div v-if="backupsLoading" class="flex justify-center py-12">
        <LoadingText />
      </div>
      <div v-else-if="!backups.length" class="flex flex-col items-center gap-4 py-12 text-center">
        <span class="place-items-center grid bg-surface-gray-2 rounded-full size-10 text-ink-gray-5">
          <span class="size-5 lucide-archive" />
        </span>
        <div>
          <p class="font-medium text-ink-gray-7 text-sm">No backups yet</p>
          <p class="mt-1 max-w-xs text-ink-gray-5 text-p-sm">
            <template v-if="backupsDisabled">Enable automatic backups to start protecting your site.</template>
            <template v-else>The first automatic backup runs {{ nextHint }}. You can also back up now.</template>
          </p>
        </div>
        <Button size="sm" :loading="backingUp" @click="backupNow">
          <template #prefix><span class="size-4 lucide-archive" /></template>
          Back up now
        </Button>
      </div>
      <ListView v-else :columns="columns" :rows="rows" row-key="name"
        :options="{ selectable: false, showTooltip: false }">
        <template #cell="{ column, row, item }">
          <div v-if="column.key === 'actions'" class="flex justify-end">
            <Dropdown :options="menuOptions(row.set)" placement="left">
              <template #default="{ open }">
                <Button variant="ghost" size="sm" :active="open"><span class="size-4 lucide-ellipsis" /></Button>
              </template>
            </Dropdown>
          </div>
          <div v-else-if="column.key === 'offsite'" class="flex justify-center">
            <span v-if="row.set.is_offsite" class="size-4 text-ink-green-6 lucide-circle-check" title="Backed up offsite" />
            <span v-else class="text-ink-gray-4">—</span>
          </div>
          <ListRowItem v-else :column="column" :row="row" :item="item" :align="column.align" />
        </template>
      </ListView>
      <ListFooter v-if="backups.length" class="mt-2 px-1" :model-value="backupsLimit" :options="footerOptions"
        @update:model-value="setBackupsPageLength" @load-more="loadMoreBackups" />
    </div>
  </div>

  <!-- Custom schedule dialog -->
  <Dialog v-model="showCustomDialog" :options="{ title: 'Custom backup schedule', size: 'sm' }">
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
        <p class="text-ink-gray-4 text-p-sm">Kept 30 days. Times shown in your timezone.</p>
        <ErrorMessage v-if="error" :message="error" />
      </div>
      <div class="flex justify-end gap-2 mt-4">
        <Button variant="ghost" @click="showCustomDialog = false">Cancel</Button>
        <Button variant="solid" :loading="scheduleSaving" @click="saveCustomSchedule">Save schedule</Button>
      </div>
    </template>
  </Dialog>

  <!-- Disable backups confirmation -->
  <Dialog v-model="showDisableConfirm" :options="{ title: 'Disable Backups', size: 'sm' }">
    <template #body-content>
      <p class="text-ink-gray-7 text-sm">Automatic backups will stop. Existing backups are kept.</p>
      <div class="flex justify-end gap-2 mt-4">
        <Button variant="ghost" @click="showDisableConfirm = false">Cancel</Button>
        <Button variant="solid" theme="red" :loading="scheduleLoading" @click="disableBackups">Disable</Button>
      </div>
    </template>
  </Dialog>

  <!-- Delete backup dialog -->
  <Dialog v-model="showDelete" :options="{ title: 'Delete Backup', size: 'sm' }">
    <template #body-content>
      <p class="text-ink-gray-7 text-sm">
        Delete the backup from <strong>{{ deleteTarget ? fmt(deleteTarget.created_at) : '' }}</strong>? This cannot be
        undone.
      </p>
      <ErrorMessage v-if="deleteError" :message="deleteError" class="mt-2" />
      <div class="flex justify-end gap-2 mt-4">
        <Button variant="ghost" @click="showDelete = false">Cancel</Button>
        <Button variant="solid" theme="red" :loading="deleting" @click="confirmDelete">Delete</Button>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Button, Dialog, Dropdown, ErrorMessage, ListFooter, ListView, ListRowItem, LoadingText, Select } from 'frappe-ui'
import { sitesApi } from '@/api/sites'
import { useSite } from '@/composables/useSite'
import { openTaskDetailPage } from '@/utils/taskRoute'

const props = defineProps({ siteName: { type: String, required: true } })
const router = useRouter()

const { backups, backupsLoading, backupsHasMore, backupsLimit, loadBackups, loadMoreBackups, setBackupsPageLength } =
  useSite(props.siteName)

const footerOptions = computed(() => ({
  rowCount: backups.value.length,
  // ListFooter shows "Load More" only when rowCount < totalCount; we don't know
  // the true total (S3 metadata is read lazily), so nudge it past rowCount
  // whenever the backend signals there may be another page.
  totalCount: backupsHasMore.value ? backups.value.length + 1 : backups.value.length,
  pageLengthOptions: [20, 50, 100],
}))

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

const schedulePreset = ref('0 2 * * *')
const backupsDisabled = ref(false)
const scheduleLoading = ref(false)
const backingUp = ref(false)
const error = ref('')

const showCustomDialog = ref(false)
const showDisableConfirm = ref(false)
const scheduleSaving = ref(false)
const schedFrequency = ref('daily')
const schedWeekday = ref(0)
const schedMonthDay = ref(1)
const schedHour = ref(2)

const WEEKDAY_FULL = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

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
  const disableEntry = { label: 'Disable backups', theme: 'red', onClick: () => { showDisableConfirm.value = true } }
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

const nextHint = computed(() => currentScheduleLabel.value.toLowerCase())

function parseCronToState(cron) {
  const [, h, dom, , dow] = cron.split(' ')
  schedHour.value = isNaN(parseInt(h)) ? 0 : parseInt(h)
  if (dom !== '*') { schedFrequency.value = 'monthly'; schedMonthDay.value = parseInt(dom) || 1 }
  else if (dow !== '*') { schedFrequency.value = 'weekly'; schedWeekday.value = parseInt(dow) || 0 }
  else schedFrequency.value = 'daily'
}

const PRESET_CRONS = ['0 2 * * *', '0 2 * * 0']

async function loadSchedule() {
  try {
    const data = await sitesApi.backups.schedule.get(props.siteName)
    if (!data.schedule) { backupsDisabled.value = true; return }
    backupsDisabled.value = false
    parseCronToState(data.schedule)
    schedulePreset.value = PRESET_CRONS.includes(data.schedule) ? data.schedule : 'custom'
  }
  catch (e) {
    error.value = e.message || 'Failed to load schedule.'
  }
}

async function setPreset(cron) {
  error.value = ''
  try {
    await sitesApi.backups.schedule.set(props.siteName, cron)
    schedulePreset.value = cron
  } catch (e) {
    error.value = e.message || 'Failed to save schedule.'
  }
}

async function saveCustomSchedule() {
  error.value = ''
  scheduleSaving.value = true
  try {
    await sitesApi.backups.schedule.set(props.siteName, schedCron.value)
    schedulePreset.value = 'custom'
    showCustomDialog.value = false
  } catch (e) {
    error.value = e.message || 'Failed to save schedule.'
  } finally {
    scheduleSaving.value = false
  }
}

async function disableBackups() {
  error.value = ''
  scheduleLoading.value = true
  try {
    await sitesApi.backups.schedule.remove(props.siteName)
    backupsDisabled.value = true
    showDisableConfirm.value = false
  } catch (e) {
    error.value = e.message || 'Failed to disable backups.'
  } finally {
    scheduleLoading.value = false
  }
}

async function enableBackups() {
  error.value = ''
  scheduleLoading.value = true
  try {
    await sitesApi.backups.schedule.set(props.siteName, '0 2 * * *')
    backupsDisabled.value = false
    schedulePreset.value = '0 2 * * *'
  } catch (e) {
    error.value = e.message || 'Failed to enable backups.'
  } finally {
    scheduleLoading.value = false
  }
}

async function backupNow() {
  backingUp.value = true
  error.value = ''
  try {
    const result = await sitesApi.backups.create(props.siteName)
    if (result.ok) openTaskDetailPage(router, result.task_id)
    else error.value = result.error || 'Backup failed.'
  } catch (e) {
    error.value = e.message || 'Backup failed.'
  } finally {
    backingUp.value = false
  }
}

const columns = [
  { label: 'Date', key: 'timestamp', align: 'left', width: 2 },
  { label: 'Database', key: 'database', align: 'center', width: 1 },
  { label: 'Public', key: 'public', align: 'center', width: 1 },
  { label: 'Private', key: 'private', align: 'center', width: 1 },
  { label: 'Offsite', key: 'offsite', align: 'center', width: 1 },
  { label: '', key: 'actions', align: 'right', width: '3rem' },
]

const fmt = (iso) => new Date(iso).toLocaleString()
const fileOf = (set, kind) => set.files?.find((f) => f.kind === kind) ?? null
const fmtSize = (b) => !b ? '—' : b < 1024 ** 2 ? `${(b / 1024).toFixed(1)} KB` : `${(b / 1024 ** 2).toFixed(1)} MB`

const rows = computed(() => backups.value.map((set) => ({
  name: set.created_at,
  timestamp: fmt(set.created_at),
  database: fmtSize(fileOf(set, 'database')?.size_bytes),
  public: fmtSize(fileOf(set, 'public-file')?.size_bytes),
  private: fmtSize(fileOf(set, 'private-file')?.size_bytes),
  set,
})))

function menuOptions(set) {
  const kinds = [
    ['database', 'Download Database'],
    ['public-file', 'Download Public'],
    ['private-file', 'Download Private'],
    ['site_config', 'Download Config'],
  ]
  return [
    // Remote-only files have no local path; there's nothing to serve for download for now.
    ...kinds.filter(([k]) => fileOf(set, k)?.path).map(([k, label]) => ({
      label, icon: 'lucide-download',
      onClick: () => { window.location.href = sitesApi.backups.download(props.siteName, fileOf(set, k).filename) },
    })),
    { label: 'Delete backup', icon: 'lucide-trash-2', theme: 'red', onClick: () => { deleteTarget.value = set; showDelete.value = true } },
  ]
}

const showDelete = ref(false)
const deleteTarget = ref(null)
const deleting = ref(false)
const deleteError = ref('')

async function confirmDelete() {
  deleting.value = true
  deleteError.value = ''
  try {
    const filenames = deleteTarget.value.files.map((f) => f.filename)
    const res = await fetch('/api/tasks/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: 'delete-backup', site: props.siteName, filenames }),
    })
    const data = await res.json()
    if (data.ok) {
      showDelete.value = false
      openTaskDetailPage(router, data.task_id)
    } else deleteError.value = data.error || 'Delete failed.'
  } catch (e) {
    deleteError.value = e.message || 'Delete failed.'
  } finally {
    deleting.value = false
  }
}

onMounted(() => { loadBackups(); loadSchedule() })
</script>
