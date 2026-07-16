<template>
  <UpdatesAvailableButton />

  <div class="mx-auto max-w-3xl">
    <!-- Header -->
    <div class="flex justify-between items-center gap-3">
      <div>
        <h1 class="font-semibold text-ink-gray-9 text-xl">Tasks</h1>
        <p class="mt-1 text-ink-gray-5 text-p-sm sm:hidden">Backups, deploys & more.</p>
        <p class="mt-1 text-ink-gray-5 text-p-base hidden sm:block">Background jobs - backups, deploys, migrations and more.</p>
      </div>
      <Button variant="subtle" size="sm" :loading="loading" icon-left="lucide-refresh-cw" @click="load(statusFilter)">
        Refresh
      </Button>
    </div>

    <!-- Filter tabs -->
    <div class="mt-4">
      <TabButtons :options="filterOptions" :modelValue="statusFilter" @update:modelValue="onFilterChange" />
    </div>

    <div v-if="loading" class="flex justify-center mt-16">
      <LoadingText />
    </div>
    <div v-else-if="error" class="mt-4">
      <ErrorMessage :message="error" />
    </div>

    <div v-else-if="tasks.length" class="bg-surface-elevation-1 mt-4 divide-outline-gray-1 divide-y overflow-hidden">
      <RouterLink v-for="task in tasks" :key="task.task_id" :to="taskDetailRoute(task.task_id)"
        class="flex items-center gap-3 py-3 no-underline transition-colors">
        <!-- Status icon -->
        <span class="place-items-center grid rounded-full size-8 shrink-0" :class="statusConfig(task).iconBg">
          <span class="size-4" :class="statusConfig(task).icon" />
        </span>

        <div class="flex-1 min-w-0">
          <span class="font-medium text-ink-gray-9 text-base truncate">{{ commandLabel(task.command) }}</span>
          <p class="mt-0.5 text-ink-gray-5 text-p-sm truncate">
            {{ siteLabel(task) }} · {{ taskActivityLabel(task) }}
            <template v-if="task.status !== 'queued' && fmtDuration(task.duration_seconds)"> · took {{ fmtDuration(task.duration_seconds)
            }}</template>
          </p>
        </div>

        <span class="lucide-chevron-right size-4 text-ink-gray-4 shrink-0" />
      </RouterLink>
    </div>

    <p v-else class="mt-16 text-ink-gray-5 text-sm text-center">No tasks found.</p>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { Button, ErrorMessage, LoadingText, TabButtons } from 'frappe-ui'
import UpdatesAvailableButton from '@/components/common/UpdatesAvailableButton.vue'
import { useTasks } from '@/composables/tasks/useTasks'
import { commandLabel, fmtDuration, siteLabel, statusConfig, taskActivityLabel } from '@/utils/taskFormat'
import { taskDetailRoute } from '@/utils/taskRoute'

const { tasks, loading, error, load } = useTasks()

const statusFilter = ref('all')

const filterOptions = [
  { label: 'All', value: 'all' },
  { label: 'Queued', value: 'queued' },
  { label: 'Running', value: 'running' },
  { label: 'Failed', value: 'failed' },
  { label: 'Succeeded', value: 'success' },
]

function onFilterChange(value) {
  statusFilter.value = value
  load(value)
}

onMounted(() => load(statusFilter.value))
</script>
