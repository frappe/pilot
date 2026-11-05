<script setup lang="ts">
import type { TaskStatus } from '@frappe/cloud-sdk'
import { Alert, Button, Progress, toast } from 'frappe-ui'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { openExternal } from '../external'
import { useTranslation } from '../translation'

const __ = useTranslation()

interface Props {
  title: string
  verb: 'install' | 'uninstall'
  taskId: string
  task?: TaskStatus
  isUnreachable?: boolean
  /** Pilot's page for this task. Empty when the user can't open the server, such as on a trial. */
  logUrl?: string
  /** The app whose install runs ahead of this one, if any. */
  waitingFor?: string
}

const props = defineProps<Props>()
const emit = defineEmits<{ retry: []; dismiss: [] }>()

const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | undefined

onMounted(() => (timer = setInterval(() => (now.value = Date.now()), 1000)))
onBeforeUnmount(() => clearInterval(timer))

const isFailed = computed(() => ['failed', 'killed'].includes(props.task?.status || ''))
const isQueued = computed(() => !isFailed.value && !props.task?.started_at)
const steps = computed(() => props.task?.steps || [])
const currentStep = computed(() => steps.value.findLast((step) => step.status !== 'done'))

const elapsed = computed(() => {
  const started = Date.parse(props.task?.started_at || '')
  if (Number.isNaN(started) || isFailed.value) return ''

  const seconds = Math.max(0, Math.round((now.value - started) / 1000))
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
})

const headline = computed(() => {
  const title = [props.title]
  if (props.verb === 'uninstall') {
    if (isFailed.value) return __("Couldn't uninstall {0}", title)
    return isQueued.value ? __('Waiting to uninstall {0}', title) : __('Uninstalling {0}', title)
  }

  if (isFailed.value) return __("Couldn't install {0}", title)
  return isQueued.value ? __('Waiting to install {0}', title) : __('Installing {0}', title)
})

const subline = computed(() => {
  const ahead = (props.task?.queue_position || 1) - 1
  if (isQueued.value && props.waitingFor) return __('Starts after {0} finishes.', [props.waitingFor])
  if (isQueued.value) return ahead ? __('{0} tasks ahead of this one.', [ahead]) : __('Starts when the server is free.')
  if (currentStep.value) return currentStep.value.label
  return steps.value.length ? __('Finishing') : __('Starting')
})

const stepIcon = { done: 'lucide-circle-check text-ink-green-5', running: 'lucide-loader-circle animate-spin text-ink-gray-6', failed: 'lucide-circle-x text-ink-red-5' }

const copyDetails = async () => {
  try {
    await navigator.clipboard.writeText(`${headline.value}\n${props.task?.error || ''}\n${__('Task')}: ${props.taskId}`)
    toast.success(__('Copied. Share it with support.'))
  } catch {
    toast.error(__("Couldn't copy. Select the text and copy it."))
  }
}
</script>

<template>
  <section class="rounded-6 border p-4" :class="isFailed ? 'border-outline-red-2' : 'border-outline-gray-2'">
    <div class="flex items-start gap-3" role="status" aria-live="polite">
      <div class="min-w-0 flex-1">
        <p class="text-base-semibold text-ink-gray-8">{{ headline }}</p>
        <p class="mt-0.5 text-p-sm" :class="isFailed ? 'text-ink-red-6' : 'text-ink-gray-5'">{{ subline }}</p>
      </div>

      <span v-if="elapsed" class="shrink-0 text-sm tabular-nums text-ink-gray-5">{{ elapsed }}</span>
    </div>

    <ol v-if="steps.length" class="mt-4 space-y-2">
      <li v-for="step in steps" :key="step.key" class="text-sm text-ink-gray-8">
        <div class="flex items-center gap-2">
          <span :class="[stepIcon[step.status], 'size-4 shrink-0']" aria-hidden="true" />
          {{ step.label }}
        </div>

        <div v-if="step.status === 'running' && task?.activity" class="ml-6 mt-1.5 flex items-center gap-3">
          <span class="min-w-0 flex-1 truncate font-mono text-xs text-ink-gray-5" :title="task.activity">
            {{ task.activity }}
          </span>
          <template v-if="task.percent != null">
            <div class="w-36 shrink-0"><Progress size="sm" :value="task.percent" /></div>
            <span class="shrink-0 text-xs tabular-nums text-ink-gray-5">{{ task.percent }}%</span>
          </template>
        </div>
      </li>
    </ol>

    <Alert
      v-if="isUnreachable && !isFailed"
      class="mt-4"
      theme="amber"
      :title="__(`Can't check progress right now`)"
      :description="__('It keeps running on the server. We keep trying to reach it.')"
    />

    <p
      v-if="['install-app', 'uninstall-app'].includes(task?.command || '') && !isFailed"
      class="mt-4 flex items-start gap-2 text-p-sm text-ink-gray-6"
    >
      <span class="lucide-wrench mt-0.5 size-4 shrink-0" aria-hidden="true" />
      {{ __("Your site is in maintenance mode until this finishes. You can close Cloud Settings. It keeps running.") }}
    </p>

    <template v-if="isFailed">
      <p v-if="task?.error" class="mt-4 break-words rounded-4 bg-surface-red-1 p-3 font-mono text-p-xs text-ink-gray-8">
        {{ task.error }}
      </p>

      <div class="mt-4 flex flex-wrap gap-2">
        <Button variant="solid" icon-left="lucide-rotate-cw" :label="__('Try again')" @click="emit('retry')" />
        <Button
          v-if="logUrl"
          icon-right="lucide-arrow-up-right"
          :label="__('Open in Pilot')"
          @click="openExternal(logUrl)"
        />
        <Button v-else icon-left="lucide-copy" :label="__('Copy details for support')" @click="copyDetails" />
        <Button class="ml-auto" variant="ghost" :label="__('Dismiss')" @click="emit('dismiss')" />
      </div>
    </template>
  </section>
</template>
