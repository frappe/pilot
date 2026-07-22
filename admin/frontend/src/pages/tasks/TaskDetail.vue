<template>
  <UpdatesAvailableButton />

  <div v-if="loading" class="flex justify-center py-12">
    <LoadingText />
  </div>
  <div v-else-if="error" class="py-12">
    <ErrorMessage :message="error" />
  </div>
  <div v-else-if="task" class="mx-auto max-w-3xl">
    <!-- Header -->
    <div class="flex justify-between items-center gap-3">
      <div class="flex items-center gap-2 min-w-0">
        <Button variant="subtle" size="sm" class="shrink-0" icon="lucide-arrow-left" @click="router.push({ name: 'Tasks' })" />
        <h1 class="flex-1 min-w-0 font-semibold text-ink-gray-9 text-xl truncate">{{ commandLabel(task.command) }}</h1>
        <Badge class="shrink-0" :label="statusConfig(task).label" :theme="statusConfig(task).theme" variant="subtle" size="md" />
      </div>
      <div class="flex items-center gap-2 shrink-0">
        <Button variant="subtle" size="sm" :loading="loading" icon="lucide-refresh-cw" @click="load" />
        <Button v-if="isTaskActive(task)" variant="subtle" size="sm" theme="red" icon-left="lucide-x"
          @click="cancelTask">
          Cancel
        </Button>
      </div>
    </div>

    <!-- Metadata -->
    <div class="gap-4 grid grid-cols-2 bg-surface-elevation-1 mt-4 px-0 py-4 rounded-xl"
      :class="metadata.length > 3 ? 'sm:grid-cols-4' : 'sm:grid-cols-3'">
      <div v-for="item in metadata" :key="item.label">
        <p class="text-ink-gray-4 text-xs">{{ item.label }}</p>
        <p class="mt-1 text-ink-gray-8 text-sm truncate">{{ item.value }}</p>
      </div>
    </div>

    <!-- Steps -->
    <div class="mt-4">
      <TaskStream v-if="isTaskActive(task)" :url="tasksApi.streamUrl(taskId)"
        :empty-text="task.status === 'queued' ? 'Waiting for this task to start…' : 'No output yet…'"
        v-slot="{ rawLines: streamedLines, streaming }" @status="updateStatus" @done="load">
        <TaskSteps :raw-lines="streamedLines" :streaming="streaming" :task-status="task.status" />
      </TaskStream>
      <TaskSteps v-else :raw-lines="rawLines" :task-status="task.status" />
    </div>
  </div>

  <ErrorMessage v-if="actionError" :message="actionError" class="mt-3" />
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Badge, Button, ErrorMessage, LoadingText } from 'frappe-ui'
import UpdatesAvailableButton from '@/components/common/UpdatesAvailableButton.vue'
import { apiErrorMessage } from '@/api/client'
import { tasksApi } from '@/api/tasks'
import { useBreadcrumbs } from '@/composables/common/useBreadcrumbs'
import { useTaskDetail } from '@/composables/tasks/useTaskDetail'
import { commandLabel, fmtDateTime, fmtDuration, isTaskActive, siteLabel, statusConfig } from '@/utils/taskFormat'

const route = useRoute()
const router = useRouter()
const taskId = route.params.taskId

const { setBreadcrumbs } = useBreadcrumbs()
const { task, rawLines, loading, error, load } = useTaskDetail(taskId)

setBreadcrumbs([{ label: 'Tasks', route: { name: 'Tasks' } }, { label: taskId }])

const actionError = ref('')

const metadata = computed(() => {
  const items = [
    { label: 'Started', value: fmtDateTime(task.value.started_at) },
    { label: 'Finished', value: task.value.finished_at ? fmtDateTime(task.value.finished_at) : '-' },
    { label: 'Duration', value: fmtDuration(task.value.duration_seconds) || '-' },
  ]
  if (task.value.status === 'queued' && task.value.queue_position) {
    items.unshift({ label: 'Queue position', value: `#${task.value.queue_position}` })
  }
  const site = siteLabel(task.value)
  if (site !== 'Server-level') items.unshift({ label: 'Site', value: site })
  return items
})

function updateStatus(event) {
  if (!['queued', 'running'].includes(event.status)) return
  task.value.status = event.status
  task.value.queue_position = event.queue_position
}

async function cancelTask() {
  actionError.value = ''
  try {
    const response = await tasksApi.cancel(taskId)
    if (!response.ok) {
      const result = await response.json()
      actionError.value = apiErrorMessage(result, 'Failed to cancel task')
      return
    }
    load()
  } catch (caught) {
    actionError.value = caught.message || 'Failed to cancel task'
  }
}

onMounted(load)
</script>
