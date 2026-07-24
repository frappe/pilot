<template>
  <Dialog v-model="show" :options="{ title: 'Debug with AI Assistant', size: '2xl' }">
    <template #body-content>
      <div class="space-y-3">
        <div v-if="streaming && !text" class="flex justify-center py-10">
          <LoadingText text="Analyzing the failure…" />
        </div>
        <Alert v-if="error" theme="red" title="Couldn't debug this task" :dismissible="false">
          <template #description>{{ error }}</template>
        </Alert>
        <div
          v-if="text"
          class="bg-surface-gray-2 p-4 rounded-lg max-h-[60vh] overflow-y-auto prose prose-sm dark:prose-invert max-w-none"
        >
          <span v-html="html"></span>
          <span
            v-if="streaming"
            class="inline-block bg-ink-gray-6 ml-0.5 w-2 h-4 align-text-bottom animate-pulse"
          />
        </div>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { Alert, Dialog, LoadingText } from 'frappe-ui'
import { markdownToHTML } from 'frappe-ui/markdown'
import DOMPurify from 'dompurify'
import { tasksApi } from '@/api/tasks'

const props = defineProps({ taskId: { type: String, required: true } })
const show = defineModel({ type: Boolean, default: false })

const text = ref('')
// frappe-ui renders the markdown; sanitize because LLM output is model-generated.
const html = computed(() => DOMPurify.sanitize(markdownToHTML(text.value || '')))
const streaming = ref(false)
const error = ref('')
let source = null

function close() {
  if (source) {
    source.close()
    source = null
  }
  streaming.value = false
}

function start() {
  close()
  text.value = ''
  error.value = ''
  streaming.value = true
  source = new EventSource(tasksApi.debugUrl(props.taskId))
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
