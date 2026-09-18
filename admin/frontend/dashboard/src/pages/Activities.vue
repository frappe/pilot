<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { type RouteLocationRaw, useRoute, useRouter } from 'vue-router'
import { Button, Combobox, Dialog, Dropdown, ErrorMessage, Tooltip } from 'frappe-ui'

import ListSkeleton from '@/components/common/ListSkeleton.vue'
import Table from '@/components/common/Table.vue'

import { useActivities } from '@/composables/activities/useActivities'
import { useSites } from '@/composables/sites/useSites'
import { relativeTime } from '@/utils/time'
import { commandLabel, fmtDateTime } from '@/utils/taskFormat'
import type { AuditEntry } from '@/types/audit'

const props = defineProps<{ siteName?: string }>()

const typeMetaMap: any = {
  backup: { icon: 'lucide-database', bg: 'bg-surface-blue-2 text-ink-blue-7' },
  app: { icon: 'lucide-package', bg: 'bg-surface-purple-2 text-ink-purple-7' },
  ssh_key: { icon: 'lucide-key', bg: 'bg-surface-gray-2 text-ink-gray-7' },
  git: { icon: 'lucide-git-branch', bg: 'bg-surface-gray-2 text-ink-gray-7' },
  task: { icon: 'lucide-list-checks', bg: 'bg-surface-blue-2 text-ink-blue-7' },
  bypass_patch: { icon: 'lucide-wrench', bg: 'bg-surface-red-2 text-ink-red-7' },
}
const defaultTypeMeta = {
  icon: 'lucide-activity',
  bg: 'bg-surface-gray-2 text-ink-gray-7',
}

const activityTypeMeta = (entry: AuditEntry) => {
  const meta = typeMetaMap[entry.type] || defaultTypeMeta

  // backup icon : failed must show red else green
  if (entry.type === 'backup' && entry.event !== 'download' && entry.event !== 'delete') {
    return {
      icon: meta.icon,
      bg:
        entry.status === 'failed'
          ? 'bg-surface-red-2 text-ink-red-7'
          : 'bg-surface-green-2 text-ink-green-7',
    }
  }
  return meta
}

const activityTypeIcon = (type: string) => (typeMetaMap[type] || defaultTypeMeta).icon

const typeLabels: Record<string, string> = {
  backup: 'Backup',
  app: 'App',
  ssh_key: 'SSH key',
  git: 'Git',
  task: 'Task',
  bypass_patch: 'Patch',
}
const activityTypeOptions = [
  { label: 'All types', value: '', icon: 'lucide-layout-grid' },
  ...Object.entries(typeLabels).map(([value, label]) => ({
    label,
    value,
    icon: activityTypeIcon(value),
  })),
]

const activityLabel = (entry: AuditEntry) => {
  const site = entry.site ? ` on ${entry.site}` : ''
  switch (entry.type) {
    case 'backup':
      if (entry.event === 'download') return `Backup file downloaded${site}`
      if (entry.event === 'delete') return `Backup deleted${site}`
      return `Backup ${entry.status === 'failed' ? 'failed' : 'completed'}${site}`
    case 'app':
      return `App ${entry.app} ${entry.event}${site}`
    case 'ssh_key':
      return entry.event === 'added' ? 'SSH key added' : 'SSH key removed'
    case 'git':
      return entry.event === 'connected'
        ? `Connected ${entry.provider} account${entry.username ? ` (${entry.username})` : ''}`
        : 'Git account disconnected'
    case 'task':
      return `Queued ${commandLabel(entry.command)}`
    case 'bypass_patch':
      return `Bypassed patch${site}`
    default:
      return entry.event ? `${entry.type} ${entry.event}` : entry.type
  }
}

const activityResourceLabel = (entry: AuditEntry) => {
  if (entry.site) return entry.site
  if (entry.type === 'task' && entry.task_id) return entry.task_id
  return ''
}

const activityResourceRoute = (entry: AuditEntry): RouteLocationRaw | null => {
  if (entry.site) return { name: 'SiteDetail', params: { name: entry.site } }
  if (entry.type === 'task' && entry.task_id)
    return { name: 'TaskDetail', params: { taskId: entry.task_id } }
  return null
}

const activityActions = (entry: AuditEntry) => {
  const target = activityResourceRoute(entry)
  const options = [{ label: 'View details', icon: 'lucide-info', onClick: () => openDetail(entry) }]
  if (target && !(props.siteName && entry.site)) {
    options.push({
      label: entry.site ? 'View site' : 'View task',
      icon: 'lucide-arrow-up-right',
      onClick: () => router.push(target),
    })
  }
  return options
}

const activityActor = (entry: AuditEntry) => {
  if (entry.actor) return { primary: entry.actor, secondary: entry.ip || '' }
  return { primary: entry.ip || 'System', secondary: '' }
}

const activityTime = (entry: AuditEntry) => relativeTime(entry.logged_at)

const showDetail = ref(false)
const viewingDetail = ref<AuditEntry | null>(null)

const openDetail = (entry: AuditEntry) => {
  viewingDetail.value = entry
  showDetail.value = true
}

// Flattens args into the top level and drops empties, so the dialog is a plain,
// complete key/value dump of the raw audit record - no per-type formatting to maintain.
const detailEntries = computed(() => {
  if (!viewingDetail.value) return []
  const { args, ...rest } = viewingDetail.value as AuditEntry & { args?: Record<string, unknown> }
  return Object.entries({ ...rest, ...args })
    .filter(([, value]) => value !== null && value !== undefined && value !== '')
    .map(([key, value]) => ({
      key,
      value: typeof value === 'object' ? JSON.stringify(value) : String(value),
    }))
})

const route = useRoute()
const router = useRouter()
const { activities, loading, loadingMore, error, hasMore, load, loadMore } = useActivities()
const { sites, load: loadSites } = useSites()

const siteOptions = computed(() => [
  { label: 'All sites', value: '' },
  ...sites.value.map((site) => ({ label: site.name, value: site.name })),
])

const activityTable = computed(() => ({
  columns: [
    { key: 'activity', label: 'Activity', cellClass: 'flex items-center gap-3' },
    ...(props.siteName ? [] : [{ key: 'resource', label: 'Resource' }]),
    { key: 'actor', label: 'Triggered by' },
    { key: 'time', label: 'Date/time', class: 'whitespace-nowrap' },
    { key: 'actions', label: '' },
  ],

  rows: activities.value
    .filter((entry) => entry.type !== 'session')
    .map((entry, index) => {
      const actor = activityActor(entry)
      return {
        id: `${entry.logged_at}-${index}`,
        entry,
        activity: activityLabel(entry),
        resource: activityResourceLabel(entry) || '-',
        actor: actor.secondary ? `${actor.primary} (${actor.secondary})` : actor.primary,
        time: activityTime(entry),
        timeAt: fmtDateTime(entry.logged_at),
      }
    }),
}))

const typeFilter = ref('')

const siteFilter = computed(
  () => props.siteName || (typeof route.query.site === 'string' ? route.query.site : ''),
)
const siteFilterModel = computed({
  get: () => siteFilter.value,
  set: (value: string) =>
    router.replace(value ? { name: 'Activity', query: { site: value } } : { name: 'Activity' }),
})
const currentFilters = computed(() => ({
  type: typeFilter.value || undefined,
  site: siteFilter.value || undefined,
}))

const reload = () => load(currentFilters.value)

watch(siteFilter, reload)
onMounted(reload)
onMounted(() => {
  if (!props.siteName) loadSites()
})
</script>

<template>
  <div
    class="flex flex-col"
    :class="siteName ? '' : 'p-3 md:p-4 h-[calc(100vh-3rem)]'"
  >
    <div class="flex flex-wrap items-center gap-3 shrink-0">
      <Combobox
        v-model="typeFilter"
        class="w-48"
        :options="activityTypeOptions"
        @update:modelValue="reload"
      />
      <Combobox
        v-if="!siteName"
        v-model="siteFilterModel"
        class="w-48"
        :options="siteOptions"
        placeholder="All sites"
      />
      <Button :loading icon-left="lucide-refresh-cw" class="md:ml-auto" @click="reload">
        Refresh
      </Button>
    </div>

    <ListSkeleton v-if="loading" class="first:mt-4" />

    <ErrorMessage :message="'error'" v-else-if="error" class="mt-4" />

    <div v-else-if="activities.length" class="flex flex-col flex-1 mt-4 min-h-0 overflow-hidden">
      <Table v-bind="activityTable" height="flex-1">
        <template #activity="{ row }">
          <span
            class="place-items-center grid rounded-full size-6 shrink-0"
            :class="activityTypeMeta(row.entry).bg"
          >
            <span class="size-3.5" :class="activityTypeMeta(row.entry).icon" />
          </span>

          <span class="font-medium text-sm">{{ row.activity }}</span>
        </template>

        <template #time="{ row }">
          <Tooltip :text="row.timeAt"><span>{{ row.time }}</span></Tooltip>
        </template>

        <template #actions="{ row }">
          <div class="flex justify-end">
            <Dropdown :options="activityActions(row.entry)">
              <Button variant="ghost" icon="lucide-more-horizontal" />
            </Dropdown>
          </div>
        </template>
      </Table>

      <div v-if="hasMore" class="flex justify-end border-outline-gray-2 p-2 border-t shrink-0">
        <Button :loading="loadingMore" @click="loadMore(currentFilters)"> Load more </Button>
      </div>
    </div>

    <!-- empty state -->
    <div
      v-else
      class="flex flex-col justify-center items-center gap-3 mt-4 h-1/2 border border-dashed rounded-7
      border-outline-gray-2 py-10"
    >
      <div class="place-items-center grid bg-surface-gray-2 rounded-6 size-10">
        <span class="lucide-history size-5 text-ink-gray-5" />
      </div>

      <div class="flex flex-col items-center gap-1">
        <p class="font-semibold text-ink-gray-8 text-sm">No activity yet</p>
        <p class="max-w-xs text-ink-gray-5 text-p-sm text-center">
          Actions like logins, backups, and app changes will show up here once they happen.
        </p>
      </div>
    </div>
  </div>

  <Dialog v-model="showDetail" title="Activity details" size="md">
    <div class="space-y-2 max-h-96 overflow-y-auto">
      <div v-for="d in detailEntries" :key="d.key" class="flex gap-3 text-p-sm">
        <span class="w-28 shrink-0 text-ink-gray-5 capitalize">{{ d.key.replace(/_/g, ' ') }}</span>
        <span class="text-ink-gray-8 break-all">{{ d.value }}</span>
      </div>
    </div>
  </Dialog>
</template>
