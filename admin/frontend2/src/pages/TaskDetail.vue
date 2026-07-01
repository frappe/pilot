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
        <Button v-if="task.status === 'running'" variant="subtle" size="sm" theme="red" icon="lucide-square"
          @click="killTask" />
      </div>
    </div>

    <!-- Metadata -->
    <div class="gap-4 grid grid-cols-2 sm:grid-cols-4 bg-surface-elevation-1 mt-4 px-0 py-4 rounded-xl">
      <div v-for="item in metadata" :key="item.label">
        <p class="text-ink-gray-4 text-xs">{{ item.label }}</p>
        <p class="mt-1 text-ink-gray-8 text-sm truncate">{{ item.value }}</p>
      </div>
    </div>

    <!-- Steps -->
    <div class="mt-4">
      <TaskStream v-if="task.status === 'running'" :url="tasksApi.streamUrl(taskId)"
        v-slot="{ rawLines: streamedLines, streaming }" @done="load">
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
import UpdatesAvailableButton from '@/components/UpdatesAvailableButton.vue'
import { tasksApi } from '@/api/tasks'
import { useBreadcrumbs } from '@/composables/useBreadcrumbs'
import { useTaskDetail } from '@/composables/useTaskDetail'
import { commandLabel, fmtDateTime, fmtDuration, statusConfig } from '@/utils/taskFormat'

const route = useRoute()
const router = useRouter()
const taskId = route.params.taskId

const { setBreadcrumbs } = useBreadcrumbs()
const { task, rawLines, loading, error, load } = useTaskDetail(taskId)

setBreadcrumbs([{ label: 'Tasks', route: { name: 'Tasks' } }, { label: taskId }])

const actionError = ref('')

const metadata = computed(() => [
  { label: 'Started', value: fmtDateTime(task.value.started_at) },
  { label: 'Finished', value: task.value.finished_at ? fmtDateTime(task.value.finished_at) : '—' },
  { label: 'Duration', value: fmtDuration(task.value.duration_seconds) || 'Running…' },
  { label: 'Task ID', value: task.value.task_id },
])

async function killTask() {
  actionError.value = ''
  try {
    const result = await tasksApi.kill(taskId)
    if (!result.ok) actionError.value = result.error || 'Failed to kill task'
    else load()
  } catch (caught) {
    actionError.value = caught.message || 'Failed to kill task'
  }
}

onMounted(load)
</script>
