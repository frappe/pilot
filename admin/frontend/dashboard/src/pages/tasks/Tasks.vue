<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Badge, Button, ErrorMessage, Select, TabButtons, Tooltip } from 'frappe-ui'

import EmptyState from '@/components/common/EmptyState.vue'
import ListSkeleton from '@/components/common/ListSkeleton.vue'
import Table from '@/components/common/Table.vue'
import StickyToolbar from '@/components/common/StickyToolbar.vue'

import { useIsMobile } from '@/composables/common/useIsMobile'
import { useTasks } from '@/composables/tasks/useTasks'

import {
  commandLabel,
  siteLabel,
  statusConfig,
  TASK_TYPES,
  fmtDateTime,
  taskDuration,
  taskLastRun,
  taskType,
} from '@/utils/taskFormat'
import { taskDetailRoute } from '@/utils/taskRoute'

const route = useRoute()
const router = useRouter()
const isMobile = useIsMobile()
const { tasks, loading, error, load } = useTasks()

const filterOptions = [
  { label: 'All', value: 'all' },
  { label: 'Queued', value: 'queued' },
  { label: 'Running', value: 'running' },
  { label: 'Failed', value: 'failed' },
  { label: 'Succeeded', value: 'success' },
]
const STATUS_VALUES = filterOptions.map((option) => option.value)

// All three filters live in the URL so filtered views are shareable.
const statusFilter = computed(() => {
  const value = typeof route.query.status === 'string' ? route.query.status : 'all'
  return STATUS_VALUES.includes(value) ? value : 'all'
})
const siteFilter = computed(() => (typeof route.query.site === 'string' ? route.query.site : ''))
const typeFilter = computed(() => (typeof route.query.type === 'string' ? route.query.type : ''))

const visibleTasks = computed(() =>
  tasks.value.filter(
    (task) =>
      (!siteFilter.value || siteLabel(task) === siteFilter.value) &&
      (!typeFilter.value || taskType(task) === typeFilter.value),
  ),
)

const columns = [
  { label: 'Task', key: 'title', class: 'w-1/3' },
  { label: 'Site', key: 'site' },
  { label: 'Status', key: 'badge' },
  { label: 'Duration', key: 'duration' },
  { label: 'Last run', key: 'timing' },
]

const rows = computed(() =>
  visibleTasks.value.map((task) => ({
    id: task.task_id,
    title: commandLabel(task.command),
    site: siteLabel(task),
    badge: statusConfig(task),
    duration: taskDuration(task),
    timing: taskLastRun(task),
    timingAt: fmtDateTime(task.started_at || task.queued_at),
  })),
)

const getRowRoute = (row) => taskDetailRoute(row.id)

// "Other" is a fallback for unknown commands; listed only once one exists.
const typeOptions = computed(() => {
  const present = new Set(tasks.value.map(taskType))
  return [
    { label: 'All types', value: '', icon: 'lucide-shapes' },
    ...TASK_TYPES.filter(
      ({ value }) => value !== 'other' || present.has('other') || typeFilter.value === 'other',
    ),
  ]
})

// Built from the loaded tasks; a site arriving via the URL is kept even
// when nothing matches, so the trigger still names what is filtering.
const siteOptions = computed(() => {
  const sites = new Set(tasks.value.map(siteLabel))
  if (siteFilter.value) sites.add(siteFilter.value)
  return [
    { label: 'All sites', value: '' },
    ...[...sites].sort().map((site) => ({ label: site, value: site })),
  ]
})

// Patch, not replace: changing one filter must not clear the other.
const setFilterQuery = (patch) => {
  const query = { ...route.query, ...patch }
  for (const key of Object.keys(query)) if (!query[key]) delete query[key]
  router.replace({ name: 'Tasks', query })
}

const onSiteChange = (site) => setFilterQuery({ site })
const onTypeChange = (type) => setFilterQuery({ type })

// An empty list means something different when a filter is on - saying "no tasks
// yet" there would be a lie.
const isFiltered = computed(
  () => statusFilter.value !== 'all' || Boolean(siteFilter.value) || Boolean(typeFilter.value),
)

const onFilterChange = (value) => {
  setFilterQuery({ status: value === 'all' ? '' : value })
  load(value)
}

onMounted(() => load(statusFilter.value))
</script>

<template>
  <div class="px-3 md:px-4">
    <StickyToolbar class="flex flex-wrap gap-2">
      <TabButtons
        class="w-full sm:w-auto"
        :size="isMobile ? 'md' : 'sm'"
        :options="filterOptions"
        :modelValue="statusFilter"
        @update:modelValue="onFilterChange"
      />

      <Select
        :model-value="typeFilter"
        :options="typeOptions"
        :size="isMobile ? 'md' : 'sm'"
        side="bottom"
        align="start"
        @update:model-value="onTypeChange"
      />

      <Select
        :model-value="siteFilter"
        :options="siteOptions"
        :size="isMobile ? 'md' : 'sm'"
        side="bottom"
        align="start"
        class="flex-1 sm:flex-none min-w-0"
        @update:model-value="onSiteChange"
      />

      <Button
        class="ml-auto"
        :size="isMobile ? 'md' : 'sm'"
        icon="lucide-refresh-cw"
        label="Refresh"
        tooltip="Refresh"
        :loading="loading"
        @click="load(statusFilter)"
      />
    </StickyToolbar>

    <ListSkeleton v-if="loading" />

    <template v-else-if="error">
      <ErrorMessage :message="error" />
    </template>

    <Table v-else-if="rows.length" :columns="columns" :rows="rows">
      <template #title="{ row }">
        <router-link :to="getRowRoute(row)">{{ row.title }}</router-link>
      </template>

      <template #timing="{ row }">
        <Tooltip :text="row.timingAt"><span>{{ row.timing }}</span></Tooltip>
      </template>

      <template #badge="{ row }">
        <Badge v-if="row.badge" :label="row.badge.label" :theme="row.badge.theme" />
      </template>
    </Table>

    <EmptyState
      v-else
      icon="lucide-list-checks"
      :title="isFiltered ? 'No matching tasks' : 'No tasks yet'"
      :description="
        isFiltered
          ? 'No background jobs match the filters you have applied.'
          : 'Background jobs - backups, deploys, migrations and more - appear here as they run.'
      "
    />
  </div>
</template>
