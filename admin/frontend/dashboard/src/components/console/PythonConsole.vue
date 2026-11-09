<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, toRef, watch } from 'vue'
import { Badge, Button, Dialog, TabButtons } from 'frappe-ui'

import PythonCodeEditor from '@/components/console/PythonCodeEditor.vue'
import ResizableEditor from '@/components/console/ResizableEditor.vue'

import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'
import { useConsoleCompletions } from '@/composables/console/useConsoleCompletions'
import type { ConsoleErrorEvent, ConsoleExitEvent, ConsoleOutputEvent } from '@/types/siteConsole'
import { getOutputLines, type OutputTone } from '@/utils/consoleOutput'
import { copyText } from '@/utils/clipboard'
import { errorMessage } from '@/utils/error'
import { readEventStream } from '@/utils/eventStream'

type RunState = {
  label: string
  theme: 'gray' | 'blue' | 'green' | 'red' | 'amber'
}

const props = defineProps<{ site: string }>()
const site = toRef(props, 'site')
const code = ref('')
const mode = ref<'readonly' | 'readwrite'>('readonly')
const showConfirm = ref(false)
const runState = ref<RunState | null>(null)
const output = ref('')
const notice = ref('')
const outputElement = ref<HTMLElement | null>(null)
const isCopied = ref(false)
let controller: AbortController | null = null
const { source: completions } = useConsoleCompletions(site)

const isRunning = computed(() => runState.value?.theme === 'blue')
const outputLines = computed(() => getOutputLines(output.value))

const TONES: Record<OutputTone, string> = {
  muted: 'text-ink-gray-5',
  error: 'text-ink-red-5 font-medium',
  path: 'text-ink-blue-7',
  number: 'text-ink-amber-6',
  name: 'text-ink-green-6',
}

const modeOptions = [
  { label: 'Read-only', value: 'readonly' },
  { label: 'Read/Write', value: 'readwrite' },
]

const requestRun = () => {
  if (isRunning.value || !code.value.trim()) return
  if (mode.value === 'readwrite') showConfirm.value = true
  else runScript()
}

const confirmRun = () => {
  showConfirm.value = false
  runScript()
}

const runScript = async () => {
  runState.value = { label: 'Running', theme: 'blue' }
  output.value = ''
  notice.value = ''
  controller = new AbortController()
  try {
    const response = await sitesApi.runConsoleScript(
      site.value,
      code.value,
      mode.value === 'readwrite',
      controller.signal,
    )
    if (!response.ok || !response.body) throw new Error(apiErrorMessage(await response.json()))
    await readEventStream(response.body, onEvent)
    if (isRunning.value) finish('Disconnected', 'amber', 'The connection closed before the script finished.')
  } catch (caught) {
    if (controller?.signal.aborted) finish('Stopped', 'amber')
    else finish('Failed', 'red', errorMessage(caught, 'Could not run the script.'))
  } finally {
    controller = null
  }
}

const onEvent = (payload: unknown) => {
  const event = payload as ConsoleOutputEvent | ConsoleErrorEvent | ConsoleExitEvent
  if ('output' in event) write(event.output)
  else if ('error' in event) finish('Failed', 'red', event.error)
  else if (event.timed_out) finish('Timed out', 'red', 'The script ran too long and was stopped.')
  else if (event.exit_code === 0) finish('Executed', 'green')
  else finish(`Exit code ${event.exit_code}`, 'red')
}

// Keeps following the end while the reader is already there.
const write = async (text: string) => {
  const element = outputElement.value
  const isAtEnd = !element || element.scrollHeight - element.scrollTop - element.clientHeight < 24
  output.value += text
  await nextTick()
  if (isAtEnd && outputElement.value) outputElement.value.scrollTop = outputElement.value.scrollHeight
}

const finish = (label: string, theme: RunState['theme'], message = '') => {
  runState.value = { label, theme }
  if (message) notice.value = message
}

const copyOutput = async (event: MouseEvent) => {
  if (!(await copyText([output.value, notice.value].filter(Boolean).join('\n'), event.currentTarget))) return
  isCopied.value = true
  setTimeout(() => (isCopied.value = false), 1000)
}

const stopScript = () => controller?.abort()

watch(
  site,
  (site) => {
    stopScript()
    runState.value = null
    code.value = localStorage.getItem(`last_console_script_${site}`) || ''
  },
  { immediate: true },
)

watch(code, (value) => {
  localStorage.setItem(`last_console_script_${site.value}`, value)
})

onBeforeUnmount(stopScript)
</script>

<template>
  <main class="flex flex-col gap-3 p-3 md:p-4 h-[calc(100dvh-6.5rem)] md:h-[calc(100dvh-3rem)]">
    <ResizableEditor storage-key="console_python_editor_height">
      <PythonCodeEditor v-model="code" :completions="completions" @run="requestRun" />

      <template #footer>
        <div class="flex flex-wrap items-center gap-2 bg-surface-base px-2 py-2 border-t border-outline-gray-2">
          <TabButtons v-model="mode" :options="modeOptions" />

          <div class="flex gap-2 md:ml-auto">
            <Button v-if="isRunning" variant="outline" iconLeft="lucide-square" @click="stopScript"> Stop </Button>
            <Button
              variant="solid"
              iconLeft="lucide-play"
              :loading="isRunning"
              :disabled="!code.trim()"
              @click="requestRun"
            >
              Execute
            </Button>
          </div>
        </div>
      </template>
    </ResizableEditor>

    <div v-if="runState" class="flex flex-col flex-1 border rounded-4 border-outline-gray-2 min-h-48 overflow-hidden">
      <div class="flex items-center gap-2 bg-surface-base px-3 py-1.5 border-b border-outline-gray-2">
        <span class="text-ink-gray-6 text-xs">Output</span>
        <Badge :label="runState.label" :theme="runState.theme" />
        <Button
          class="ml-auto"
          variant="ghost"
          size="xs"
          :iconLeft="isCopied ? 'lucide-check' : 'lucide-copy'"
          @click="copyOutput"
        >
          {{ isCopied ? 'Copied' : 'Copy' }}
        </Button>
        <Button
          variant="ghost"
          size="xs"
          iconLeft="lucide-eraser"
          :disabled="isRunning"
          @click="runState = null"
        >
          Clear
        </Button>
      </div>
      <pre
        ref="outputElement"
        class="flex-1 bg-surface-gray-1 p-3 min-h-0 overflow-auto font-mono text-ink-gray-8 text-sm leading-relaxed whitespace-pre-wrap break-words"
      ><template v-for="(line, index) in outputLines" :key="index"><span v-for="(part, partIndex) in line" :key="partIndex" :class="part.tone && TONES[part.tone]">{{ part.text }}</span>{{ index < outputLines.length - 1 ? '\n' : '' }}</template><span v-if="notice" class="text-ink-gray-5">{{ output ? '\n' : '' }}{{ notice }}</span><span v-else-if="!output && !isRunning" class="text-ink-gray-5 italic">No output</span></pre>
    </div>
  </main>

  <Dialog v-model="showConfirm" title="Run in Read/Write mode">
    <p class="text-ink-gray-7 text-sm">
      This script will run in <strong>Read/Write</strong> mode and any changes will be committed to the database. Are
      you sure you want to continue?
    </p>

    <pre
      class="bg-surface-gray-1 mt-3 p-3 border rounded-4 border-outline-gray-2 max-h-40 overflow-auto font-mono text-ink-gray-8 text-sm leading-relaxed whitespace-pre-wrap break-words"
      >{{ code }}</pre>
    <template #actions>
      <div class="flex justify-end gap-2">
        <Button variant="outline" @click="showConfirm = false">Cancel</Button>
        <Button variant="solid" @click="confirmRun">Execute</Button>
      </div>
    </template>
  </Dialog>
</template>
