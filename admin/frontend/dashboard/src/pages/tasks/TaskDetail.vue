<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { Badge, Button, ErrorMessage, LoadingText } from 'frappe-ui'

import TaskDebugDialog from '@/components/tasks/TaskDebugDialog.vue'
import TaskSteps from '@/components/tasks/TaskSteps.vue'
import TaskStream from '@/components/tasks/TaskStream.vue'

import { apiErrorMessage } from '@/api/client'
import { tasksApi } from '@/api/tasks'
import { settingsApi } from '@/api/settings'
import { useBreadcrumbs } from '@/composables/common/useBreadcrumbs'
import { useTaskDetail } from '@/composables/tasks/useTaskDetail'

import {
  commandLabel,
  fmtDateTime,
  fmtDuration,
  isTaskActive,
  isTaskCancellable,
  redirectRouteOnSuccess,
  statusConfig,
  taskScope,
} from '@/utils/taskFormat'

const route = useRoute()
const router = useRouter()
const taskId = route.params.taskId

const { setBreadcrumbs } = useBreadcrumbs()
const { task, rawLines, loading, error, load } = useTaskDetail(taskId)

setBreadcrumbs([{ label: 'Tasks', route: { name: 'Tasks' } }])
watch(
  () => task.value?.command,
  (command) => {
    if (!command) return
    setBreadcrumbs([
      { label: 'Tasks', route: { name: 'Tasks' } },
      { label: commandLabel(command) },
    ])
  },
)

const actionError = ref('')
const showDebug = ref(false)
const aiConnected = ref(false)

const loadAiStatus = async () => {
  try {
    const data = await settingsApi.get()
    aiConnected.value = Boolean(data.llm?.provider && data.llm?.api_key_set)
  } catch {
    aiConnected.value = false
  }
}

const scope = computed(() => taskScope(task.value))
const scopeIcon = computed(() =>
  scope.value.route ? 'lucide-globe' : 'lucide-server',
)

const queuePosition = computed(() =>
  task.value.status === 'queued' && task.value.queue_position
    ? `#${task.value.queue_position} in queue`
    : '',
)

const startedAt = computed(() =>
  task.value.started_at ? fmtDateTime(task.value.started_at) : '',
)

const duration = computed(() => fmtDuration(task.value.duration_seconds))

const updateStatus = (event) => {
  if (!['queued', 'running'].includes(event.status)) return
  task.value.status = event.status
  task.value.queue_position = event.queue_position
  task.value.is_cancellable = event.is_cancellable
}

const handleDone = (success) => {
  load()
  if (!success) return
  const redirect = redirectRouteOnSuccess(task.value)
  if (redirect) router.push(redirect)
}

const cancelTask = async () => {
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

onMounted(() => {
  load()
  loadAiStatus()
})
</script>

<template>
  <LoadingText v-if="loading" class="p-3 md:p-4 justify-center py-12" />

  <ErrorMessage v-else-if="error" class="px-3 md:px-4 py-12" :message="error" />

  <div v-else-if="task" class="p-3 md:p-4 mx-auto max-w-3xl">
    <Teleport defer to="#header-badge">
      <Badge
        :label="statusConfig(task).label"
        :theme="statusConfig(task).theme"
      />
    </Teleport>

    <Teleport defer to="#header-actions">
      <div class="flex items-center gap-2">
        <Button
          :loading="loading"
          icon="lucide-refresh-cw"
          label="Refresh"
          tooltip="Refresh"
          @click="load"
        />
        <Button
          v-if="task.status === 'failed' && aiConnected"
          icon-left="lucide-sparkles"
          @click="showDebug = true"
        >
          Debug with AI
        </Button>

        <Button
          v-if="isTaskCancellable(task)"
          theme="red"
          icon-left="lucide-x"
          @click="cancelTask"
        >
          Cancel
        </Button>
      </div>
    </Teleport>

    <TaskDebugDialog v-model="showDebug" :task-id="taskId" />

    <div class="flex justify-between items-center gap-4 lg:mt-5 px-2 min-w-0">
      <RouterLink
        :to="scope.route || ''"
        class="group flex items-center gap-2 min-w-0 text-lg-medium text-ink-gray-9 no-underline"
      >
        <span class="size-4 text-ink-gray-5 shrink-0" :class="scopeIcon" />
        <span class="truncate">{{ scope.label }}</span>
        <span
          v-if="scope.route"
          class="opacity-0 group-hover:opacity-100 size-4 text-ink-gray-5 transition-opacity shrink-0 lucide-arrow-up-right"
        />
      </RouterLink>

      <div class="flex items-center gap-3 text-sm shrink-0">
        <span v-if="queuePosition" class="flex items-center gap-1.5 ">
          <span class="size-3.5 lucide-list-ordered" />
          {{ queuePosition }}
        </span>

        <span v-if="startedAt" class="flex items-center gap-1.5 text-ink-gray-7">
          <span class="size-3.5 lucide-clock" />
          {{ startedAt }}
        </span>

        <span v-if="duration" class="flex items-center gap-1.5 text-ink-gray-5">
          <span class="size-3.5 lucide-timer" />
          <span class="tabular-nums">{{ duration }}</span>
        </span>
      </div>
    </div>

    <ErrorMessage v-if="actionError" :message="actionError" class="mt-3" />

    <!-- Steps -->
    <div class="mt-3">
      <TaskStream
        v-if="isTaskActive(task)"
        :url="tasksApi.streamUrl(taskId)"
        :empty-text="task.status === 'queued' ? 'Waiting for this task to start…' : 'No output yet…'"
        v-slot="{ rawLines: streamedLines, streaming }"
        @status="updateStatus"
        @done="handleDone"
      >
        <TaskSteps :raw-lines="streamedLines" :streaming="streaming" :task-status="task.status" />
      </TaskStream>

      <TaskSteps v-else :raw-lines="rawLines" :task-status="task.status" />
    </div>
  </div>
</template>
