<template>
  <div class="mx-auto max-w-3xl">
    <!-- Header -->
    <div class="flex justify-between items-center gap-3">
      <div>
        <h1 class="font-semibold text-ink-gray-9 text-xl">Tasks</h1>
        <p class="mt-1 text-ink-gray-5 text-p-sm sm:hidden">Backups, deploys & more.</p>
        <p class="mt-1 text-ink-gray-5 text-p-base hidden sm:block">
          Background jobs - backups, deploys, migrations and more.
        </p>
      </div>
      <Button
        variant="subtle"
        size="sm"
        :loading="loading"
        icon-left="lucide-refresh-cw"
        @click="load(statusFilter)"
      >
        Refresh
      </Button>
    </div>

    <!-- Filter tabs -->
    <div class="mt-4">
      <TabButtons
        :options="filterOptions"
        :modelValue="statusFilter"
        @update:modelValue="onFilterChange"
      />
    </div>

    <!-- Site filter -->
    <div
      v-if="siteFilter"
      class="mt-4 flex items-center gap-2 rounded-lg bg-surface-blue-1 px-3 py-2"
    >
      <span class="lucide-filter size-4 text-ink-blue-7 shrink-0" />
      <p class="flex-1 min-w-0 text-p-sm text-ink-blue-8 truncate">
        Jobs linked to <span class="font-semibold">{{ siteFilter }}</span>, plus bench-level jobs
      </p>
      <Button variant="ghost" size="sm" icon="lucide-x" @click="clearSiteFilter" />
    </div>

    <div v-if="loading" class="flex justify-center mt-16">
      <LoadingText />
    </div>
    <div v-else-if="error" class="mt-4">
      <ErrorMessage :message="error" />
    </div>

    <div
      v-else-if="visibleTasks.length"
      class="bg-surface-elevation-1 mt-4 divide-outline-gray-1 divide-y overflow-hidden"
    >
      <RouterLink
        v-for="task in visibleTasks"
        :key="task.task_id"
        :to="taskDetailRoute(task.task_id)"
        class="flex items-center gap-3 py-3 no-underline transition-colors"
      >
        <!-- Status icon -->
        <span
          class="place-items-center grid rounded-full size-8 shrink-0"
          :class="statusConfig(task).iconBg"
        >
          <span class="size-4" :class="statusConfig(task).icon" />
        </span>

        <div class="flex-1 min-w-0">
          <span class="font-medium text-ink-gray-9 text-base truncate"
            >{{ commandLabel(task.command) }}</span
          >
          <p class="mt-0.5 text-ink-gray-5 text-p-sm truncate">
            {{ siteLabel(task) }}
            · {{ taskActivityLabel(task) }}
            <template v-if="task.status !== 'queued' && fmtDuration(task.duration_seconds)">
              · took {{ fmtDuration(task.duration_seconds) }}</template
            >
          </p>
        </div>

        <span class="lucide-chevron-right size-4 text-ink-gray-4 shrink-0" />
      </RouterLink>
    </div>

    <p v-else class="mt-16 text-ink-gray-5 text-sm text-center">No tasks found.</p>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button, ErrorMessage, LoadingText, TabButtons } from 'frappe-ui'
import { useTasks } from '@/composables/tasks/useTasks'
import {
  commandLabel,
  fmtDuration,
  siteLabel,
  statusConfig,
  taskActivityLabel,
} from '@/utils/taskFormat'
import { taskDetailRoute } from '@/utils/taskRoute'

const route = useRoute()
const router = useRouter()
const { tasks, loading, error, load } = useTasks()

const statusFilter = ref('all')

const filterOptions = [
  { label: 'All', value: 'all' },
  { label: 'Queued', value: 'queued' },
  { label: 'Running', value: 'running' },
  { label: 'Failed', value: 'failed' },
  { label: 'Succeeded', value: 'success' },
]

// ?site=<name> shows jobs linked to that site plus bench-level (unlinked) jobs.
const siteFilter = computed(() => (typeof route.query.site === 'string' ? route.query.site : ''))
const visibleTasks = computed(() => {
  if (!siteFilter.value) return tasks.value
  return tasks.value.filter((task) => [siteFilter.value, 'Server-level'].includes(siteLabel(task)))
})

function clearSiteFilter() {
  router.replace({ name: 'Tasks' })
}

function onFilterChange(value) {
  statusFilter.value = value
  load(value)
}

onMounted(() => load(statusFilter.value))
</script>
