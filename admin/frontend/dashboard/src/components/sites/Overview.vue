<script setup lang="ts">
import { Button, Dialog, Skeleton, Tooltip } from 'frappe-ui'
import { computed, onMounted, reactive, ref, useId } from 'vue'
import { RouterLink } from 'vue-router'
import { auditApi } from '@/api/audit'
import { unwrap } from '@/api/client'
import { sitesApi } from '@/api/sites'
import { updatesApi } from '@/api/updates'
import AppIcon from '@/components/apps/AppIcon.vue'
import CircularProgress from '@/components/common/CircularProgress.vue'
import Sparkline from '@/components/common/Sparkline.vue'
import OverviewEmpty from '@/components/sites/OverviewEmpty.vue'
import OverviewSkeleton from '@/components/sites/OverviewSkeleton.vue'
import { useSite } from '@/composables/sites/useSite'
import { useSiteStorage } from '@/composables/sites/useSiteStorage'
import type { AuditEntry } from '@/types/audit'
import type { MigrationSummary } from '@/types/migrations'
import type { SiteActivity } from '@/types/siteActivity'
import type { Backup, BackupSchedule } from '@/types/siteBackups'
import type { SiteDomain } from '@/types/siteDomains'
import type { SiteHistory } from '@/types/siteHistory'
import type { FailedJob } from '@/types/siteJobs'
import type { SiteUptime, UptimeBucket } from '@/types/siteMonitoring'
import type { SiteStorageDay } from '@/types/siteStorage'
import { activityLabel, activityTypeIcon } from '@/utils/activity'
import { cronToLabel } from '@/utils/backup'
import { errorMessage } from '@/utils/error'
import { formatBytes } from '@/utils/format'
import { relativeTime } from '@/utils/time'

const props = defineProps<{ siteName: string }>()

const emit = defineEmits<{ open: [tab: string] }>()

const { site } = useSite(props.siteName)
const { load: loadStorage, storageUsage, collectedAt } = useSiteStorage()

const ready = reactive({
  storage: false,
  uptime: false,
  activity: false,
  backups: false,
  schedule: false,
  domains: false,
  recent: false,
  history: false,
  failedJobs: false,
  info: false,
  lastRun: false,
})
const failures = reactive<Partial<Record<keyof typeof ready, string>>>({})
const uptime = ref<SiteUptime | null>(null)
const activity = ref<SiteActivity | null>(null)
const backups = ref<Backup[]>([])
const schedule = ref<BackupSchedule | null>(null)
const domains = ref<SiteDomain[]>([])
const recent = ref<AuditEntry[]>([])
const history = ref<SiteStorageDay[]>([])
const allFailedJobs = ref<FailedJob[]>([])
const showFailedJobs = ref(false)
const showFailedRequests = ref(false)
const siteHistory = ref<SiteHistory | null>(null)
const lastRun = ref<MigrationSummary | null>(null)
const sizeHover = ref<number | null>(null)

const hourFormat = new Intl.DateTimeFormat(undefined, { hour: 'numeric' })
const dayFormat = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' })

const sum = (values: number[]) => values.reduce((total, value) => total + value, 0)

const requests = computed(() => activity.value?.requests ?? [])
const requestTotal = computed(() => sum(requests.value))
const peak = computed(() => requests.value.indexOf(Math.max(...requests.value)))

const hourLabel = (index: number, length: number) =>
  hourFormat.format(Date.now() - (length - 1 - index) * 3_600_000)

const uptimeBuckets = computed(() => uptime.value?.buckets ?? [])
const dips = computed(() =>
  uptimeBuckets.value.filter((bucket) => bucket.percent !== null && bucket.percent < 99.9),
)

const lastDip = computed(() => dips.value.at(-1))

const uptimeTone = (percent: number | null) => {
  if (percent === null) return 'bg-surface-gray-3'
  if (percent >= 99.9) return 'bg-surface-green-5 dark:bg-surface-green-3'
  if (percent >= 99) return 'bg-surface-amber-6 dark:bg-surface-amber-5'

  return 'bg-surface-red-6 dark:bg-surface-red-5'
}

const usage = computed(() => storageUsage(props.siteName))

const storageParts = computed(() => [
  {
    key: 'database',
    label: 'DB',
    icon: 'lucide-database',
    bytes: usage.value?.database_bytes ?? 0,
  },
  {
    key: 'files',
    label: 'Files',
    icon: 'lucide-folder',
    bytes: (usage.value?.public_bytes ?? 0) + (usage.value?.private_bytes ?? 0),
  },
])

const lastBackup = computed(() => backups.value[0] ?? null)
const backupDays = computed(() =>
  Array.from({ length: 14 }, (_, index) => {
    const day = new Date(Date.now() - (13 - index) * 86_400_000).toDateString()
    const backup = backups.value.find((item) => new Date(item.created_at).toDateString() === day)

    return { day, backup }
  }),
)

const backupBytes = (backup: Backup) => sum(backup.files.map((file) => file.size_bytes))

const offsiteBytes = computed(() =>
  sum(backups.value.filter((backup) => backup.is_offsite).map(backupBytes)),
)
const localBytes = computed(() =>
  sum(backups.value.filter((backup) => !backup.is_offsite).map(backupBytes)),
)

const errors = computed(() => activity.value?.errors ?? [])
const errorTotal = computed(() => sum(errors.value))
const jobRunTotal = computed(() => sum(activity.value?.jobs ?? []))
const failedRunTotal = computed(() => sum(allFailedJobs.value.map((row) => row.count)))
const jobSuccess = computed(() =>
  jobRunTotal.value
    ? Math.round((Math.max(jobRunTotal.value - failedRunTotal.value, 0) / jobRunTotal.value) * 100)
    : 100,
)

const worstHour = computed(() => errors.value.indexOf(Math.max(...errors.value)))

const growth = computed(() => {
  const days = history.value

  return days.length > 1 ? days[days.length - 1].total_bytes - days[0].total_bytes : 0
})

const sizeLine = computed(() => {
  const raw = history.value.map((day) => day.total_bytes)
  const sizes = raw.map((_, index) => {
    const window = raw.slice(Math.max(0, index - 2), index + 3)

    return sum(window) / window.length
  })
  const low = Math.min(...sizes) * 0.97
  const high = Math.max(...sizes, low + 1)
  const step = 100 / Math.max(sizes.length - 1, 1)
  const toY = (size: number) => 38 - ((size - low) / (high - low)) * 34

  return sizes.map((size, index) => [index * step, toY(size)])
})

const sizePath = computed(() =>
  sizeLine.value
    .map(([x, y], index) => {
      if (!index) return `M${x},${y}`

      const [px, py] = sizeLine.value[index - 1]
      const mid = (px + x) / 2

      return `C${mid},${py} ${mid},${y} ${x},${y}`
    })
    .join(' '),
)

const sizeGradient = useId()

const failedJobs = computed(() => allFailedJobs.value.slice(0, 3))

const failedPaths = computed(() => activity.value?.failed_paths ?? [])

const errorLogUrl = computed(() => `${site.value?.url ?? ''}/app/error-log`)

const sizeDay = (day: SiteStorageDay) => dayFormat.format(new Date(`${day.date}T00:00:00`))

const toTime = (value: string) => new Date(value.replace(' ', 'T')).getTime()

const failedRunStates = ['needs_attention', 'revert_failed', 'reverted', 'stopped']
const doneRunStates = ['completed', 'dismissed']

const lastUpdateTone = computed(() => {
  const state = lastRun.value?.state

  if (!state) return ''
  if (failedRunStates.includes(state)) return 'text-ink-red-6'
  if (doneRunStates.includes(state)) return 'text-ink-gray-8'

  return 'text-ink-amber-6'
})

const lastUpdateIcon = computed(() => {
  const state = lastRun.value?.state

  if (!state) return ''
  if (failedRunStates.includes(state)) return 'lucide-circle-x'
  if (doneRunStates.includes(state)) return 'lucide-circle-check text-ink-gray-5'

  return 'lucide-loader-circle'
})

const lastUpdateLabel = computed(() => {
  const run = lastRun.value
  const info = siteHistory.value

  if (!run) return info?.updated_at ? relativeTime(toTime(info.updated_at)) : '-'

  const when = relativeTime(run.finished_at ?? run.started_at ?? run.created_at)

  if (failedRunStates.includes(run.state)) return `Failed · ${when}`
  if (doneRunStates.includes(run.state)) return when

  return 'Running'
})

const certificate = computed(() => {
  const status = domains.value.find((domain) => domain.is_primary)?.tls_status

  if (!status) return { value: 'None', tone: 'text-ink-gray-5' }
  if (status.state === 'failed' || status.state === 'expired')
    return { value: status.state === 'failed' ? 'Failed' : 'Expired', tone: 'text-ink-red-6' }
  if (status.state === 'pending') return { value: 'Pending', tone: 'text-ink-gray-6' }
  if (status.state === 'expiring')
    return { value: `Expires in ${status.days_left ?? 0} days`, tone: 'text-ink-amber-6' }

  return { value: `Valid · ${status.days_left ?? 0} days`, tone: 'text-ink-gray-8' }
})

const siteFacts = computed(() => {
  const info = siteHistory.value

  return [
    {
      label: 'Primary domain',
      value: domains.value.find((domain) => domain.is_primary)?.domain ?? '-',
    },
    { label: 'Frappe version', value: info?.frappe_version.split(' ')[0] || '-' },
    ...(info ? [{ label: 'Apps', value: '' }] : []),
    { label: 'Users', value: info ? `${info.users}` : '-' },
    { label: 'Created', value: info?.created_at ? dayFormat.format(toTime(info.created_at)) : '-' },
    { label: 'Certificate', value: certificate.value.value, tone: certificate.value.tone },
    { label: 'Last update', value: lastUpdateLabel.value, tone: lastUpdateTone.value },
  ]
})

const scrubSize = (event: MouseEvent) => {
  const box = (event.currentTarget as HTMLElement).getBoundingClientRect()
  const ratio = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width))

  sizeHover.value = Math.round(ratio * (history.value.length - 1))
}

const slowest = computed(() => activity.value?.slow_requests.slice(0, 3) ?? [])
const topPaths = computed(() => activity.value?.top_paths.slice(0, 7) ?? [])
const slowJobs = computed(() => activity.value?.slow_jobs.slice(0, 3) ?? [])
const responseTimes = computed(() => activity.value?.response_ms ?? [])
const responseAverage = computed(() => {
  const values = responseTimes.value.filter(Boolean)

  return values.length ? Math.round(sum(values) / values.length) : 0
})
const responsePeak = computed(() => Math.round(Math.max(...responseTimes.value, 0)))
const slowMs = 1000
const slowHours = computed(() => responseTimes.value.filter((ms) => ms >= slowMs).length)

const shortName = (path: string) => path.replace('/api/method/', '')
const jobName = (method: string) => method.split('.').at(-1)?.replaceAll('_', ' ') ?? method

const uptimePercent = computed(() => {
  const percent = uptime.value?.overall_percent

  return percent == null ? '-' : `${Number(percent.toFixed(2))}%`
})

const uptimeNote = computed(() => {
  if (uptimeBuckets.value.every((bucket) => bucket.percent === null)) return 'No checks yet'
  if (!lastDip.value) return 'No downtime'

  return `Last downtime ${dayFormat.format(lastDip.value.time)}`
})

const bucketTip = (bucket: UptimeBucket) =>
  `${dayFormat.format(bucket.time)} · ${bucket.percent === null ? 'No checks' : `${bucket.percent}%`}`

const backupTip = (backup: Backup) =>
  `${dayFormat.format(new Date(backup.created_at))} · ${backup.is_offsite ? 'Uploaded' : 'Local only'}`

const backupTone = (backup?: Backup) => {
  if (!backup) return 'bg-surface-gray-2'

  return backup.is_offsite
    ? 'bg-surface-green-5 dark:bg-surface-green-4'
    : 'bg-surface-gray-6 dark:bg-surface-gray-5'
}

const recentRows = computed(() =>
  [
    ...recent.value,
    ...backups.value.map(
      (backup) =>
        ({
          type: 'backup',
          event: 'scheduled',
          logged_at: backup.created_at,
          status: null,
        }) as AuditEntry,
    ),
  ]
    .sort((a, b) => new Date(b.logged_at).getTime() - new Date(a.logged_at).getTime())
    .slice(0, 5),
)

const eventIcon = (entry: AuditEntry) => [
  activityTypeIcon(entry.type),
  entry.status === 'failed' ? 'text-ink-red-5' : 'text-ink-gray-4',
]

const load = () => {
  const name = props.siteName
  const track = <T>(key: keyof typeof ready, request: Promise<T>, save: (data: T) => void) =>
    unwrap(request)
      .then(save)
      .catch((caught) => (failures[key] = errorMessage(caught, 'Could not load this.')))
      .finally(() => (ready[key] = true))

  loadStorage().finally(() => (ready.storage = true))
  track('uptime', sitesApi.uptime.get(name, '1w'), (data) => (uptime.value = data))
  track('activity', sitesApi.activity(name), (data) => (activity.value = data))
  track('backups', sitesApi.backups.list(name), (data) => (backups.value = data))
  track('schedule', sitesApi.backups.schedule.get(name), (data) => (schedule.value = data))
  track('domains', sitesApi.domains.list(name), (data) => (domains.value = data.domains))
  track('recent', auditApi.list({ site: name, limit: 7 }), (page) => (recent.value = page.data))
  track('history', sitesApi.storageHistory(name), (data) => (history.value = data))
  track('failedJobs', sitesApi.failedJobs(name), (data) => (allFailedJobs.value = data))
  track('info', sitesApi.history(name), (data) => (siteHistory.value = data))
  track(
    'lastRun',
    updatesApi.list({ site: name, limit: 1 }),
    (page) => (lastRun.value = page.data[0] ?? null),
  )
}

onMounted(load)
</script>

<template>
  <div class="flex flex-col gap-4">
    <section class="gap-3 md:gap-4 grid sm:grid-cols-2 lg:grid-cols-4">
      <div
        class="flex flex-col gap-3 px-5 py-4.5 border border-outline-gray-1.5 rounded-6 min-w-0 fade-in-children"
      >
        <span class="flex justify-between text-ink-gray-5 text-sm">
          Requests
          <span>24h</span>
        </span>

        <template v-if="ready.activity">
          <span class="tabular-nums text-ink-gray-9 text-base-medium">
            {{ requestTotal.toLocaleString() }}
          </span>

          <Sparkline
            v-if="requestTotal"
            :values="requests"
            unit="request"
            class="w-full h-7 text-ink-gray-8"
          />

          <span v-else class="mb-3.5 border-outline-gray-3 border-b border-dashed h-3.5" />

          <span v-if="requestTotal" class="text-ink-gray-5 text-xs truncate">
            Peak {{ hourLabel(peak, requests.length) }} ·
            <span class="tabular-nums text-ink-gray-7">{{ requests[peak] }}</span>
          </span>

          <span v-else class="text-ink-gray-5 text-xs">No requests yet</span>
        </template>

        <OverviewSkeleton v-else chart="h-7" />
      </div>

      <div
        class="flex flex-col gap-3 px-5 py-4.5 border border-outline-gray-1.5 rounded-6 min-w-0 fade-in-children"
      >
        <span class="flex justify-between text-ink-gray-5 text-sm">
          Uptime
          <span>7d</span>
        </span>

        <template v-if="ready.uptime">
          <span class="tabular-nums text-ink-gray-9 text-base-medium">
            {{ uptimePercent }}
          </span>

          <div class="flex items-center gap-px h-7">
            <Tooltip v-for="bucket in uptimeBuckets" :key="bucket.time" :text="bucketTip(bucket)">
              <span class="flex-1 rounded-[1.5px] h-5" :class="uptimeTone(bucket.percent)" />
            </Tooltip>
          </div>

          <span class="text-ink-gray-5 text-xs truncate">{{ uptimeNote }}</span>
        </template>

        <OverviewSkeleton v-else chart="h-7" />
      </div>

      <div
        class="flex flex-col gap-3 px-5 py-4.5 border border-outline-gray-1.5 rounded-6 min-w-0 fade-in-children"
      >
        <span class="flex justify-between gap-2 text-ink-gray-5 text-sm">
          Storage
          <span v-if="sizeHover !== null" class="tabular-nums truncate">
            {{ sizeDay(history[sizeHover]) }}
            · {{ formatBytes(history[sizeHover].total_bytes) }}
          </span>

          <span v-else-if="collectedAt" class="truncate">{{ relativeTime(collectedAt) }}</span>
        </span>

        <template v-if="ready.storage">
          <span class="flex justify-between items-center gap-2">
            <span class="tabular-nums text-ink-gray-9 text-base-medium">
              {{ usage ? formatBytes(usage.total_bytes) : '-' }}
            </span>

            <span
              v-if="growth"
              class="flex items-center gap-1 tabular-nums text-ink-gray-5 text-2xs"
              :title="history.length ? `Since ${sizeDay(history[0])}` : ''"
            >
              <span
                class="size-3"
                :class="growth > 0 ? 'lucide-trending-up' : 'lucide-trending-down'"
              />
              {{ formatBytes(Math.abs(growth)) }}
            </span>
          </span>

          <div
            v-if="history.length > 1"
            class="relative w-full h-7"
            role="img"
            aria-label="Site size over the last 30 days"
            @mousemove="scrubSize"
            @mouseleave="sizeHover = null"
          >
            <svg
              viewBox="0 0 100 40"
              preserveAspectRatio="none"
              class="absolute inset-0 size-full text-ink-gray-8"
              aria-hidden="true"
            >
              <defs>
                <linearGradient :id="sizeGradient" x1="0" x2="0" y1="0" y2="1">
                  <stop offset="0" stop-color="currentColor" stop-opacity="0.18" />
                  <stop offset="1" stop-color="currentColor" stop-opacity="0" />
                </linearGradient>
              </defs>

              <path :d="`${sizePath} L100,40 L0,40 Z`" :fill="`url(#${sizeGradient})`" />

              <path
                :d="sizePath"
                fill="none"
                stroke="currentColor"
                stroke-width="1.5"
                stroke-linejoin="round"
                vector-effect="non-scaling-stroke"
              />
            </svg>

            <span
              v-if="sizeHover !== null"
              class="absolute bg-current rounded-full ring-4 ring-outline-alpha-gray-3 size-1.5 text-ink-gray-8 -translate-x-1/2 -translate-y-1/2 pointer-events-none"
              :style="{ left: `${sizeLine[sizeHover][0]}%`, top: `${(sizeLine[sizeHover][1] / 40) * 100}%` }"
            />
          </div>

          <span v-else class="mb-3.5 border-outline-gray-3 border-b border-dashed h-3.5" />

          <span v-if="usage" class="flex items-center gap-4 text-ink-gray-5 text-xs truncate">
            <span v-for="part in storageParts" :key="part.key" class="flex items-center gap-1.5">
              <span class="size-3 text-ink-gray-4" :class="part.icon" />
              {{ part.label }}
              <span class="tabular-nums text-ink-gray-7">{{ formatBytes(part.bytes) }}</span>
            </span>
          </span>
        </template>

        <OverviewSkeleton v-else chart="h-7" />
      </div>

      <div
        class="flex flex-col gap-3 px-5 py-4.5 border border-outline-gray-1.5 rounded-6 min-w-0 fade-in-children"
      >
        <span class="flex justify-between text-ink-gray-5 text-sm">
          Last backup
          <span class="truncate">
            {{ schedule?.schedule ? cronToLabel(schedule.schedule) : 'Not scheduled' }}
          </span>
        </span>

        <template v-if="ready.backups && ready.schedule">
          <span class="text-ink-gray-9 text-base-medium truncate">
            {{ lastBackup ? relativeTime(lastBackup.created_at) : 'Never' }}
          </span>

          <div class="flex items-center gap-1 h-7">
            <Tooltip
              v-for="slot in backupDays"
              :key="slot.day"
              :text="slot.backup ? backupTip(slot.backup) : `${dayFormat.format(new Date(slot.day))} · No backup`"
            >
              <span class="flex-1 rounded-[1.5px] h-5" :class="backupTone(slot.backup)" />
            </Tooltip>
          </div>

          <span class="flex items-center gap-3 text-ink-gray-5 text-xs truncate">
            <span
              class="flex items-center gap-1.5 before:bg-surface-green-5 dark:before:bg-surface-green-4 before:rounded-full before:size-2"
            >
              Offsite
              <span class="tabular-nums text-ink-gray-7">{{ formatBytes(offsiteBytes) }}</span>
            </span>

            <span
              class="flex items-center gap-1.5 before:bg-surface-gray-6 dark:before:bg-surface-gray-5 before:rounded-full before:size-2"
            >
              Local
              <span class="tabular-nums text-ink-gray-7">{{ formatBytes(localBytes) }}</span>
            </span>
          </span>
        </template>

        <OverviewSkeleton v-else chart="h-7" />
      </div>
    </section>

    <div
      class="grid lg:grid-cols-[22rem_minmax(0,1fr)] xl:grid-cols-[24rem_minmax(0,1fr)] border border-outline-gray-1.5 rounded-6 overflow-hidden"
    >
      <div
        class="grid xl:grid-cols-2 min-w-0 [&>section]:border-outline-gray-1.5 [&>section]:border-b [&>section:last-child]:border-b-0 xl:[&>section:nth-child(odd)]:border-r xl:[&>section:nth-last-child(-n+2)]:border-b-0"
      >
        <section class="flex flex-col gap-3 px-5 py-4.5 min-w-0 fade-in-children">
          <span class="flex items-center gap-2 h-5 text-ink-gray-5 text-sm">
            <span class="text-base-medium text-ink-gray-8">Errors</span>
            · 24h

            <Button
              v-if="errorTotal"
              class="-mr-1.5 ml-auto"
              size="xs"
              variant="ghost"
              label="View all"
              icon-right="lucide-arrow-right"
              @click="showFailedRequests = true"
            />
          </span>

          <template v-if="ready.activity">
            <template v-if="errorTotal">
              <span class="flex items-baseline gap-2 text-ink-gray-5 text-sm">
                <span class="tabular-nums text-ink-red-6 text-sm-medium">
                  {{ errorTotal.toLocaleString() }}
                </span>
                failed requests
              </span>

              <Sparkline
                :values="errors"
                unit="error"
                class="mt-auto w-full h-20 text-ink-red-6 dark:text-ink-red-5"
              />

              <span v-if="errorTotal" class="text-ink-gray-5 text-xs">
                Most at {{ hourLabel(worstHour, errors.length) }} ·
                <span class="tabular-nums text-ink-gray-7">{{ errors[worstHour] }} errors</span>
              </span>
            </template>

            <OverviewEmpty v-else icon="lucide-circle-check" text="No failed requests" />
          </template>

          <OverviewSkeleton v-else chart="h-20" />
        </section>

        <section class="flex flex-col gap-3 px-5 py-4.5 min-w-0 fade-in-children">
          <span class="flex justify-between items-center h-5 text-base-medium text-ink-gray-8">
            Page speed
            <span class="text-ink-gray-5 text-sm">24h</span>
          </span>

          <template v-if="ready.activity">
            <template v-if="responsePeak">
              <span class="flex items-baseline gap-2 text-ink-gray-5 text-sm">
                <span class="tabular-nums text-ink-gray-9 text-base-medium">
                  {{ responseAverage.toLocaleString() }}
                  ms
                </span>
                average wait per request{{ slowHours ? ` · ${slowHours} slow hours` : '' }}
              </span>

              <div class="relative flex justify-around items-end h-16">
                <span
                  v-if="responsePeak > slowMs"
                  class="right-0 left-0 absolute border-outline-gray-3 border-t border-dashed"
                  :style="{ bottom: `${(slowMs / responsePeak) * 100}%` }"
                >
                  <span class="right-0 absolute -top-4 text-ink-gray-5 text-2xs">1s</span>
                </span>

                <Tooltip
                  v-for="(ms, index) in responseTimes"
                  :key="index"
                  :text="`${hourLabel(index, responseTimes.length)} · ${Math.round(ms)} ms`"
                >
                  <span
                    class="rounded-full w-1.5 hover:opacity-70 transition-opacity"
                    :class="ms >= slowMs ? 'bg-surface-amber-5 dark:bg-surface-amber-4' : 'bg-surface-gray-5'"
                    :style="{ height: ms ? `${Math.max(8, (ms / responsePeak) * 100)}%` : '3px' }"
                  />
                </Tooltip>
              </div>

              <span class="flex justify-between text-ink-gray-5 text-xs">
                {{ hourLabel(0, responseTimes.length) }}
                <span>Now</span>
              </span>
            </template>

            <OverviewEmpty v-else icon="lucide-gauge" text="No requests yet" />
          </template>

          <OverviewSkeleton v-else chart="h-16" />
        </section>

        <section class="flex flex-col px-5 py-4.5 min-w-0 fade-in-children">
          <span class="flex items-center gap-2 h-5 text-ink-gray-5 text-sm">
            <span class="text-base-medium text-ink-gray-8">Background jobs</span>
            · 24h

            <Button
              v-if="allFailedJobs.length"
              class="-mr-1.5 ml-auto"
              size="xs"
              variant="ghost"
              label="View all"
              icon-right="lucide-arrow-right"
              @click="showFailedJobs = true"
            />
          </span>

          <template v-if="ready.activity && ready.failedJobs">
            <OverviewEmpty
              v-if="failures.failedJobs"
              class="mt-3"
              icon="lucide-database-zap"
              :text="failures.failedJobs"
            />

            <div
              v-else-if="jobRunTotal || allFailedJobs.length"
              class="items-center grid grid-cols-2 mt-3"
            >
              <div class="flex justify-center items-center gap-4 pr-5">
                <span class="relative place-items-center grid size-24 shrink-0">
                  <CircularProgress
                    :value="100 - jobSuccess"
                    tone="text-ink-red-6"
                    track="text-ink-gray-1"
                    class="absolute inset-0 size-full stroke-[1.25]"
                  />

                  <span class="tabular-nums text-ink-gray-9 text-sm-medium">
                    {{ 100 - jobSuccess }}%
                  </span>
                </span>

                <span class="flex flex-col gap-1 whitespace-nowrap">
                  <span class="tabular-nums text-ink-red-6 text-base-medium">
                    {{ failedRunTotal }}
                    failed
                  </span>

                  <span class="text-ink-gray-5 text-sm">
                    of {{ jobRunTotal.toLocaleString() }} runs
                  </span>
                </span>
              </div>

              <div class="flex flex-col pl-5 border-outline-gray-2 border-l border-dashed min-w-0">
                <span class="flex items-center h-7 text-ink-gray-5 text-sm">Failed jobs</span>

                <span
                  v-for="row in failedJobs"
                  :key="row.job"
                  class="items-center gap-3 grid grid-cols-[minmax(0,1fr)_auto] h-7 text-sm"
                >
                  <Tooltip :text="row.error || row.job">
                    <span class="text-ink-gray-7 truncate first-letter:uppercase">
                      {{ jobName(row.job) }}
                    </span>
                  </Tooltip>

                  <span class="tabular-nums text-ink-red-6">{{ row.count }}×</span>
                </span>

                <span
                  v-if="!failedJobs.length"
                  class="flex items-center h-7 text-ink-gray-5 text-sm"
                >
                  No failures
                </span>
              </div>
            </div>

            <OverviewEmpty v-else class="mt-3" icon="lucide-cog" text="No jobs ran" />
          </template>

          <OverviewSkeleton v-else :rows="3" />
        </section>

        <section class="flex flex-col px-5 py-4.5 min-w-0 fade-in-children">
          <span class="flex justify-between items-center mb-3 h-5 text-base-medium text-ink-gray-8">
            Slowest jobs
            <span class="text-ink-gray-5 text-sm">24h</span>
          </span>

          <template v-if="ready.activity">
            <span
              v-for="row in slowJobs"
              :key="row.name"
              class="items-center gap-3 grid grid-cols-[minmax(0,1fr)_auto] h-9 text-base"
            >
              <span class="text-ink-gray-7 truncate first-letter:uppercase">
                {{ jobName(row.name) }}
              </span>

              <span class="tabular-nums text-ink-gray-7 text-sm">
                {{ row.value.toFixed(1) }}s
              </span>
            </span>

            <OverviewEmpty v-if="!slowJobs.length" icon="lucide-timer" text="No jobs ran" />
          </template>

          <OverviewSkeleton v-else :rows="3" />
        </section>

        <section class="flex flex-col px-5 py-4.5 min-w-0 fade-in-children">
          <span class="flex justify-between items-center mb-3 h-5 text-base-medium text-ink-gray-8">
            Top requests
            <span class="text-ink-gray-5 text-sm">24h</span>
          </span>

          <template v-if="ready.activity">
            <span
              v-for="row in topPaths"
              :key="row.name"
              class="items-center gap-3 grid grid-cols-[minmax(0,1fr)_auto] h-9 text-base"
            >
              <span class="font-mono text-ink-gray-7 text-xs truncate">
                {{ shortName(row.name) }}
              </span>

              <span class="tabular-nums text-ink-gray-7 text-sm">
                {{ row.value.toLocaleString() }}
              </span>
            </span>

            <OverviewEmpty
              v-if="!topPaths.length"
              icon="lucide-chart-no-axes-column"
              text="No requests yet"
            />
          </template>

          <OverviewSkeleton v-else :rows="5" />
        </section>

        <section class="flex flex-col px-5 py-4.5 min-w-0 fade-in-children">
          <span class="flex justify-between items-center mb-3 h-5 text-base-medium text-ink-gray-8">
            Slowest requests
            <span class="text-ink-gray-5 text-sm">24h</span>
          </span>

          <template v-if="ready.activity">
            <span
              v-for="row in slowest"
              :key="row.name"
              class="items-center gap-3 grid grid-cols-[minmax(0,1fr)_auto] h-9 text-base"
            >
              <span class="font-mono text-ink-gray-7 text-xs truncate">
                {{ shortName(row.name) }}
              </span>

              <span class="tabular-nums text-ink-gray-7 text-sm">
                {{ row.value.toFixed(1) }}s
              </span>
            </span>

            <OverviewEmpty v-if="!slowest.length" icon="lucide-timer" text="No requests yet" />
          </template>

          <OverviewSkeleton v-else :rows="3" />
        </section>
      </div>

      <aside
        class="flex flex-col bg-surface-gray-0 divide-y divide-outline-gray-1.5 border-outline-gray-1.5 border-t lg:border-t-0 lg:border-r lg:order-first [&>section]:px-5 [&>section]:py-4.5"
      >
        <section class="flex flex-col fade-in-children">
          <span class="flex items-center h-5 text-base-medium text-ink-gray-8">
            Site information
          </span>

          <template v-if="ready.info && ready.domains && ready.lastRun">
            <dl class="flex flex-col mt-3">
              <div
                v-for="fact in siteFacts"
                :key="fact.label"
                class="flex justify-between items-center gap-8 h-9 text-base"
              >
                <dt class="text-ink-gray-6">{{ fact.label }}</dt>

                <dd v-if="fact.label === 'Apps'" class="flex items-center gap-1">
                  <AppIcon
                    v-for="app in siteHistory?.apps.slice(0, 6) ?? []"
                    :key="app.app"
                    :name="app.app"
                    size="xs"
                    shape="circle"
                  />
                </dd>

                <dd v-else-if="fact.label === 'Last update' && lastRun">
                  <RouterLink
                    :to="{ name: 'UpdateDetail', params: { operationId: lastRun.id } }"
                    class="flex items-center gap-1.5 tabular-nums hover:underline"
                    :class="fact.tone"
                  >
                    <span class="size-3.5 shrink-0" :class="lastUpdateIcon" />
                    {{ fact.value }}
                  </RouterLink>
                </dd>

                <dd v-else class="tabular-nums" :class="fact.tone || 'text-ink-gray-8'">
                  {{ fact.value }}
                </dd>
              </div>
            </dl>

            <p v-if="failures.info" class="flex items-center gap-1.5 mt-2 text-ink-gray-5 text-sm">
              <span class="size-3.5 lucide-database-zap" />
              {{ failures.info }}
            </p>
          </template>

          <OverviewSkeleton v-else :rows="7" />
        </section>

        <section class="flex flex-col fade-in-children">
          <span class="flex justify-between items-center h-5 text-base-medium text-ink-gray-8">
            Recent

            <Button
              class="-mr-1.5"
              size="xs"
              variant="ghost"
              label="All activity"
              icon-right="lucide-arrow-right"
              @click="emit('open', 'activity')"
            />
          </span>

          <template v-if="ready.recent && ready.backups">
            <ul class="mt-3">
              <li
                v-for="entry in recentRows"
                :key="entry.logged_at + entry.type"
                class="flex items-center gap-2.5 h-9 min-w-0 text-base"
              >
                <span class="size-4 shrink-0" :class="eventIcon(entry)" />

                <span
                  class="truncate"
                  :class="entry.status === 'failed' ? 'text-ink-red-6' : 'text-ink-gray-8'"
                >
                  {{ activityLabel({ ...entry, site: null }) }}
                </span>

                <span class="ml-auto tabular-nums text-ink-gray-5 text-sm shrink-0">
                  {{ relativeTime(entry.logged_at) }}
                </span>
              </li>
            </ul>

            <span v-if="!recentRows.length" class="flex items-center h-9 text-ink-gray-5 text-sm">
              Nothing yet
            </span>
          </template>

          <OverviewSkeleton v-else :rows="5" />
        </section>
      </aside>
    </div>

    <Dialog v-model="showFailedRequests" title="Failed requests · 24h" size="xl">
      <ul class="flex flex-col divide-y divide-outline-gray-1">
        <li
          v-for="row in failedPaths"
          :key="row.name"
          class="flex items-center gap-3 py-3 min-w-0 text-base"
        >
          <span class="font-mono text-ink-gray-7 text-xs truncate">{{ shortName(row.name) }}</span>
          <span class="ml-auto tabular-nums text-ink-red-6 shrink-0">{{ row.value }}×</span>
        </li>
      </ul>

      <div class="flex justify-between items-center gap-4 mt-4 text-ink-gray-5 text-sm">
        Tracebacks are in the site's Error Log.

        <Button label="Open Error Log" icon-right="lucide-arrow-up-right" :href="errorLogUrl" />
      </div>
    </Dialog>

    <Dialog v-model="showFailedJobs" title="Failed jobs · 24h" size="xl">
      <ul class="flex flex-col divide-y divide-outline-gray-1">
        <li
          v-for="row in allFailedJobs"
          :key="row.job"
          class="items-center gap-x-3 gap-y-1 grid grid-cols-[minmax(0,1fr)_auto_auto] py-3 text-base"
        >
          <span class="text-ink-gray-8 truncate first-letter:uppercase">
            {{ jobName(row.job) }}
          </span>

          <span class="text-ink-gray-5 text-sm">
            {{ relativeTime(toTime(row.last_failed_at)) }}
          </span>

          <span class="tabular-nums text-ink-red-6">{{ row.count }}×</span>

          <span class="col-span-3 font-mono text-ink-gray-6 text-xs break-words">
            {{ row.error || row.job }}
          </span>
        </li>
      </ul>
    </Dialog>
  </div>
</template>
