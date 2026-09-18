<script setup lang="ts">
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { Alert, Button, Dialog, LoadingText } from 'frappe-ui'
import { markdownToHTML } from 'frappe-ui/markdown'
import DOMPurify from 'dompurify'

import { tasksApi } from '@/api/tasks'

interface Props {
  taskId: string
}

const props = defineProps<Props>()
const show = defineModel({ type: Boolean, default: false })

const text = ref('')
// frappe-ui renders the markdown; sanitize because LLM output is model-generated.
const html = computed(() => DOMPurify.sanitize(markdownToHTML(text.value || '')))
const streaming = ref(false)
const error = ref('')
let source = null

const close = () => {
  if (source) {
    source.close()
    source = null
  }
  streaming.value = false
}

// Answers are cached per task, so reopening replays the previous one instantly.
// `refresh` asks the model again and replaces it.
const start = ({ refresh = false } = {}) => {
  close()
  text.value = ''
  error.value = ''
  streaming.value = true
  source = new EventSource(tasksApi.debugUrl(props.taskId, refresh))
  source.onmessage = (message) => {
    let event
    try {
      event = JSON.parse(message.data)
    } catch {
      return
    }
    if (event.type === 'delta') {
      text.value += event.text
    } else if (event.type === 'done') {
      close()
    } else if (event.type === 'error') {
      error.value = event.message || 'AI debugging failed.'
      close()
    }
  }
  source.onerror = () => {
    if (source?.readyState !== EventSource.CLOSED) return
    if (!text.value) error.value = 'Could not reach the AI assistant.'
    close()
  }
}

watch(show, (open) => (open ? start() : close()))
onBeforeUnmount(close)
</script>

<template>
  <Dialog v-model="show" title="Debug with AI Assistant" size="2xl">
    <div class="space-y-3">
      <LoadingText
        v-if="streaming && !text"
        text="Analyzing the failure…"
        class="justify-center py-10"
      />

      <Alert
        v-if="error"
        class="border border-outline-gray-2"
        theme="red"
        title="Couldn't debug this task"
        :dismissible="false"
      >
        <template #description>{{ error }}</template>
      </Alert>

      <div
        v-if="text"
        class="bg-surface-gray-2 p-4 rounded-6 max-h-[60vh] overflow-y-auto prose prose-sm dark:prose-invert max-w-none"
      >
        <span v-html="html" />
        <span
          v-if="streaming"
          class="inline-block bg-ink-gray-6 ml-0.5 w-2 h-4 align-text-bottom animate-pulse"
        />
      </div>

      <div v-if="text || error" class="flex justify-end">
        <Button
          icon-left="lucide-refresh-cw"
          :loading="streaming"
          @click="start({ refresh: true })"
        >
          Regenerate
        </Button>
      </div>
    </div>
  </Dialog>
</template>
