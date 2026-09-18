<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Badge, Button, ErrorMessage, TabButtons, Tooltip } from 'frappe-ui'

import EmptyState from '@/components/common/EmptyState.vue'
import ListSkeleton from '@/components/common/ListSkeleton.vue'
import Table from '@/components/common/Table.vue'
import StickyToolbar from '@/components/common/StickyToolbar.vue'

import { useIsMobile } from '@/composables/common/useIsMobile'
import { updatesApi } from '@/api/updates'

import {
  matchesUpdateFilter,
  opTitle,
  pendingActionLabel,
  siteNames,
  sitesLabel,
  stateLabel,
  stateTone,
  UPDATE_FILTERS,
} from '@/utils/updateFormat'
import { relativeTime } from '@/utils/time'
import { fmtDateTime, fmtDuration } from '@/utils/taskFormat'

const route = useRoute()
const router = useRouter()
const isMobile = useIsMobile()

const operations = ref([])
const loading = ref(false)
const error = ref('')

const FILTER_VALUES = UPDATE_FILTERS.map((option) => option.value)

// In the URL like the tasks list, so a filtered view survives a reload and can
// be pasted to someone else.
const statusFilter = computed(() => {
  const value = typeof route.query.status === 'string' ? route.query.status : 'all'
  return FILTER_VALUES.includes(value) ? value : 'all'
})
const isFiltered = computed(() => statusFilter.value !== 'all')

// Filtered here rather than by the API: its status filter matches a single
// state, and every tab but Completed and Reverted covers several.
const visibleOperations = computed(() =>
  operations.value.filter((op) => matchesUpdateFilter(op, statusFilter.value)),
)

const onFilterChange = (value) => {
  const query = { ...route.query }
  if (value === 'all') delete query.status
  else query.status = value
  router.replace({ name: 'Updates', query })
}

const badge = (op) => {
  if (op.pending_action) return { label: pendingActionLabel(op.pending_action), theme: 'amber' }
  const tone = stateTone(op.state)
  return { label: stateLabel(op.state), theme: tone === 'orange' ? 'amber' : tone }
}

const columns = [
  { label: 'Update', key: 'title', class: 'w-1/3' },
  { label: 'Site', key: 'site' },
  { label: 'Status', key: 'badge' },
  { label: 'Duration', key: 'duration' },
  { label: 'Last run', key: 'timing' },
]

const rows = computed(() =>
  visibleOperations.value.map((op) => ({
    id: op.id,
    title: opTitle(op),
    site: sitesLabel(op),
    siteNames: siteNames(op),
    badge: badge(op),
    duration: duration(op),
    timing: relativeTime(op.started_at || op.created_at),
    timingAt: fmtDateTime(op.started_at || op.created_at),
  })),
)

const getRowRoute = (row) => ({ name: 'UpdateDetail', params: { operationId: row.id } })

const duration = (op) =>
  op.finished_at && op.started_at
    ? fmtDuration((new Date(op.finished_at) - new Date(op.started_at)) / 1000)
    : ''

const load = async () => {
  loading.value = true
  error.value = ''
  try {
    const [current, history] = await Promise.all([
      updatesApi.current().catch(() => null),
      updatesApi.list({ limit: 50 }),
    ])
    const past = history.data || []
    // Pin the active/unresolved operation at the top (it is also in history).
    operations.value = current ? [current, ...past.filter((op) => op.id !== current.id)] : past
  } catch (e) {
    error.value = e?.message || 'Could not load updates.'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="px-3 md:px-4">
    <Teleport v-if="isMobile" defer to="#header-actions">
      <Button
        :loading="loading"
        icon="lucide-refresh-cw"
        label="Refresh"
        tooltip="Refresh"
        @click="load"
      />
    </Teleport>

    <StickyToolbar :class="isMobile ? '' : 'flex items-center gap-2'">
      <TabButtons
        :size="isMobile ? 'md' : 'sm'"
        :options="UPDATE_FILTERS"
        :modelValue="statusFilter"
        @update:modelValue="onFilterChange"
      />
      <Button
        v-if="!isMobile"
        class="ml-auto"
        :loading="loading"
        icon="lucide-refresh-cw"
        label="Refresh"
        tooltip="Refresh"
        @click="load"
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

      <template #site="{ row }">
        <span :title="row.siteNames">{{ row.site }}</span>
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
      icon="lucide-git-pull-request-arrow"
      :title="isFiltered ? 'No matching updates' : 'No updates yet'"
      :description="
        isFiltered
          ? 'No updates are in this state right now.'
          : 'App updates across your sites appear here, with backup and recovery.'
      "
    />
  </div>
</template>
