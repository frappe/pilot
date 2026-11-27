<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { type RouteLocationRaw, RouterLink, useRoute, useRouter } from 'vue-router'
import { Badge, Button, ErrorMessage, LoadingText } from 'frappe-ui'

import TaskDebugDialog from '@/components/tasks/TaskDebugDialog.vue'
import TaskSteps from '@/components/tasks/TaskSteps.vue'
import TaskStream from '@/components/tasks/TaskStream.vue'

import { apiErrorMessage } from '@/api/client'
import { tasksApi } from '@/api/tasks'
import { settingsApi } from '@/api/settings'
import { useBreadcrumbs } from '@/composables/common/useBreadcrumbs'
import { useTaskDetail } from '@/composables/tasks/useTaskDetail'
import { useTaskSteps } from '@/composables/tasks/useTaskSteps'
import type { StatusEvent } from '@/types/tasks'
import { errorMessage } from '@/utils/error'

import {
  commandLabel,
  fmtDateTime,
  fmtDuration,
  isTaskActive,
  isTaskCancellable,
  redirectRouteOnSuccess,
  statusConfig,
  taskInputs,
  taskScope,
} from '@/utils/taskFormat'

const route = useRoute()
const router = useRouter()
const taskId = computed(() =>
  typeof route.params.taskId === 'string' ? route.params.taskId : '',
)

const { setBreadcrumbs } = useBreadcrumbs()
const { task, rawLines, loading, error, load } = useTaskDetail(taskId)

setBreadcrumbs([{ label: 'Tasks', route: { name: 'Tasks' } }])
watch(
  () => task.value?.command,
  (command) => {
    if (!command) return
    setBreadcrumbs([{ label: 'Tasks', route: { name: 'Tasks' } }, { label: commandLabel(command) }])
  },
)

const actionError = ref('')
const showDebug = ref(false)
const retrying = ref(false)
const aiConnected = ref(false)

const loadAiStatus = async () => {
  try {
    const data = await settingsApi.get()
    aiConnected.value = Boolean(data.llm?.provider && data.llm?.api_key_set)
  } catch {
    aiConnected.value = false
  }
}

const scope = computed(() => (task.value ? taskScope(task.value) : null))

interface Fact {
  label: string
  icon: string
  value: string
  route?: RouteLocationRaw
}

const { stepSections } = useTaskSteps(rawLines, ref(false), task)

const waited = computed(() => {
  if (!task.value?.started_at) return ''

  const seconds = (Date.parse(task.value.started_at) - Date.parse(task.value.queued_at)) / 1000

  return fmtDuration(Math.max(seconds, 0), { precise: true })
})

const siteFact = computed<Fact[]>(() => {
  if (!scope.value?.route) return []

  const { label, route } = scope.value

  return [{ label: 'Site', icon: 'lucide-pilot-site', value: label, route }]
})

const timeFacts = computed<Fact[]>(() => {
  if (!task.value) return []

  const { status, queue_position, started_at, duration_seconds } = task.value
  const started = { label: 'Started', icon: 'lucide-calendar', value: fmtDateTime(started_at) }

  if (status === 'queued' && queue_position) {
    return [started, { label: 'Queue', icon: 'lucide-list-ordered', value: `#${queue_position}` }]
  }

  const duration = fmtDuration(duration_seconds) || '-'

  return [started, { label: 'Duration', icon: 'lucide-timer', value: duration }]
})

const facts = computed<Fact[]>(() => {
  if (!task.value) return []

  const base = [...siteFact.value, ...taskInputs(task.value), ...timeFacts.value]
  const stepCount = isTaskActive(task.value) ? 0 : stepSections.value.length
  const steps = { label: 'Steps', icon: 'lucide-list-checks', value: String(stepCount) }
  const extras: Fact[] = [
    ...(stepCount ? [steps] : []),
    { label: 'Waited', icon: 'lucide-hourglass', value: waited.value || '-' },
  ]

  return [...base, ...extras.slice(0, Math.max(0, 4 - base.length))]
})

const failureMessage = computed(() => {
  if (!task.value?.failure) return ''

  const errorLine = [...rawLines.value].reverse().find((line) => line.startsWith('Error: '))

  return errorLine?.slice('Error: '.length) || task.value.failure.message
})

const updateStatus = (event: StatusEvent) => {
  if (!task.value) return
  if (event.status !== 'queued' && event.status !== 'running') return

  task.value.status = event.status
  task.value.queue_position = event.queue_position
  task.value.is_cancellable = event.is_cancellable
}

const handleDone = (success: boolean) => {
  load()
  if (!success || !task.value) return

  const redirect = redirectRouteOnSuccess(task.value)
  if (redirect) router.push(redirect)
}

const retrySite = async () => {
  actionError.value = ''
  retrying.value = true
  try {
    const next = await tasksApi.retry(taskId.value)
    await router.push({ name: 'TaskDetail', params: { taskId: next.task_id } })
  } catch (caught) {
    actionError.value = errorMessage(caught, 'Could not retry site creation')
  } finally {
    retrying.value = false
  }
}

const cancelTask = async () => {
  actionError.value = ''
  try {
    const response = await tasksApi.cancel(taskId.value)
    if (!response.ok) {
      const result = await response.json()
      actionError.value = apiErrorMessage(result, 'Failed to cancel task')
      return
    }
    load()
  } catch (caught) {
    actionError.value = errorMessage(caught, 'Failed to cancel task')
  }
}

onMounted(loadAiStatus)
watch(taskId, load, { immediate: true })
</script>

<template>
  <LoadingText v-if="loading" class="px-3 md:px-4 py-12 justify-center" />

  <ErrorMessage v-else-if="error" class="px-3 md:px-4 py-12" :message="error" />

  <div v-else-if="task" class="mx-auto p-3 md:p-4 xl:pt-8 pb-10 md:pb-10 max-w-[1000px]">
    <Teleport defer to="#header-badge">
      <Badge :label="statusConfig(task).label" :theme="statusConfig(task).theme" />
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
          v-if="task.command === 'new-site' && task.status === 'failed'"
          variant="solid"
          icon-left="lucide-refresh-cw"
          :loading="retrying"
          @click="retrySite"
        >
          Try Again
        </Button>

        <Button v-if="isTaskCancellable(task)" theme="red" icon-left="lucide-x" @click="cancelTask">
          Cancel
        </Button>
      </div>
    </Teleport>

    <TaskDebugDialog v-model="showDebug" :task-id="taskId" />

    <dl
      class="lg:flex gap-x-4 lg:gap-x-10 gap-y-2 grid grid-cols-2 sm:grid-cols-3 p-3 lg:px-4 lg:py-3.5 border border-outline-gray-1.5 rounded-5"
    >
      <div
        v-for="fact in facts"
        :key="fact.label"
        class="flex flex-col grow gap-1 lg:gap-1.5 min-w-0"
      >
        <dt class="flex items-center gap-1.5 text-ink-gray-5 text-sm">
          <span class="size-3.5 text-ink-gray-4" :class="fact.icon" />
          {{ fact.label }}
        </dt>

        <dd class="text-sm-medium text-ink-gray-8 truncate">
          <RouterLink v-if="fact.route" :to="fact.route" class="hover:text-ink-gray-9">
            {{ fact.value }}
          </RouterLink>
          <template v-else>{{ fact.value }}</template>
        </dd>
      </div>
    </dl>

    <ErrorMessage v-if="actionError" :message="actionError" class="mt-4" />

    <!-- Steps -->
    <div class="mt-4">
      <TaskStream
        v-if="isTaskActive(task)"
        :url="tasksApi.streamUrl(taskId)"
        :empty-text="task.status === 'queued' ? 'Waiting for this task to start…' : 'No output yet…'"
        v-slot="{ rawLines: streamedLines, streaming }"
        @status="updateStatus"
        @done="handleDone"
      >
        <TaskSteps
          :raw-lines="streamedLines"
          :streaming="streaming"
          :task-status="task.status"
          :empty-text="task.status === 'queued' ? 'Waiting for this task to start…' : 'No output yet…'"
        />
      </TaskStream>

      <TaskSteps
        v-else
        :raw-lines="rawLines"
        :task-status="task.status"
        :finished-at="task.finished_at"
        :failure="failureMessage"
      />
    </div>
  </div>
</template>
