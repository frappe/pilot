<script setup lang="ts">
import { Badge, Button, Checkbox, Dialog, KeyboardShortcut } from 'frappe-ui'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { apiErrorMessage } from '@/api/client'
import { sitesApi } from '@/api/sites'
import ConsoleHistory from '@/components/console/ConsoleHistory.vue'
import PythonCodeEditor from '@/components/console/PythonCodeEditor.vue'
import ResizableEditor from '@/components/console/ResizableEditor.vue'
import { useConsoleCompletions } from '@/composables/console/useConsoleCompletions'
import type { AuditEntry } from '@/types/audit'
import type { ConsoleErrorEvent, ConsoleExitEvent, ConsoleOutputEvent } from '@/types/siteConsole'
import { copyText } from '@/utils/clipboard'
import { getOutputLines, type OutputTone } from '@/utils/consoleOutput'
import { errorMessage } from '@/utils/error'
import { readEventStream } from '@/utils/eventStream'

type RunState = {
  label: string
  theme: 'gray' | 'blue' | 'green' | 'red' | 'amber'
}

const site = defineModel<string>('site', { required: true })
const isHistoryOpen = defineModel<boolean>('history', { default: false })
const history = ref<InstanceType<typeof ConsoleHistory> | null>(null)
const duration = ref('')
let startedAt = 0
const code = ref('')
const isReadWrite = ref(false)
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

const requestRun = () => {
  if (isRunning.value || !code.value.trim()) return
  if (isReadWrite.value) showConfirm.value = true
  else runScript()
}

const formatDuration = (milliseconds: number) =>
  milliseconds < 1000 ? `${Math.round(milliseconds)} ms` : `${(milliseconds / 1000).toFixed(1)}s`

const confirmRun = () => {
  showConfirm.value = false
  runScript()
}

const runScript = async () => {
  runState.value = { label: 'Running', theme: 'blue' }
  output.value = ''
  notice.value = ''
  duration.value = ''
  startedAt = performance.now()
  controller = new AbortController()
  try {
    const response = await sitesApi.runConsoleScript(
      site.value,
      code.value,
      isReadWrite.value,
      controller.signal,
    )
    if (!response.ok || !response.body) throw new Error(apiErrorMessage(await response.json()))
    await readEventStream(response.body, onEvent)
    history.value?.load()

    if (isRunning.value)
      finish('Disconnected', 'amber', 'The connection closed before the script finished.')
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
  if (isAtEnd && outputElement.value)
    outputElement.value.scrollTop = outputElement.value.scrollHeight
}

const finish = (label: string, theme: RunState['theme'], message = '') => {
  runState.value = { label, theme }
  duration.value = formatDuration(performance.now() - startedAt)

  if (message) notice.value = message
}

const copyOutput = async (event: MouseEvent) => {
  if (
    !(await copyText([output.value, notice.value].filter(Boolean).join('\n'), event.currentTarget))
  )
    return
  isCopied.value = true
  setTimeout(() => (isCopied.value = false), 1000)
}

const stopScript = () => controller?.abort()

const loadRun = (run: AuditEntry) => {
  isReadWrite.value = Boolean(run.commit)

  if (run.site && run.site !== site.value) {
    localStorage.setItem(`last_console_script_${run.site}`, run.code ?? '')
    site.value = run.site
    return
  }

  stopScript()
  runState.value = null
  code.value = run.code ?? ''
}

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
  <div class="relative flex h-[calc(100dvh-6.5rem)] md:h-[calc(100dvh-3rem)]">
    <main class="flex flex-col flex-1 min-w-0">
      <ResizableEditor storage-key="console_python_editor_height" quiet>
        <PythonCodeEditor v-model="code" :completions="completions" @run="requestRun" />

        <template #footer>
          <footer class="flex flex-wrap items-center gap-2 bg-surface-gray-1 px-4 py-1.5">
            <Checkbox v-model="isReadWrite" size="xs" label="Read/Write" class="mr-auto" />

            <KeyboardShortcut
              v-if="!isRunning"
              combo="Mod+Enter"
              class="max-sm:hidden text-ink-gray-4"
            />

            <Button
              v-if="isRunning"
              size="xs"
              variant="outline"
              icon-left="lucide-square"
              @click="stopScript"
            >
              Stop
            </Button>

            <Button
              size="xs"
              variant="solid"
              icon-left="lucide-play"
              :loading="isRunning"
              :disabled="!code.trim()"
              @click="requestRun"
            >
              Execute
            </Button>
          </footer>
        </template>
      </ResizableEditor>

      <section class="flex flex-col flex-1 min-h-0">
        <header class="flex items-center gap-3 px-4 pt-1 h-11 shrink-0">
          <h2 class="text-ink-gray-8 text-base-medium">Output</h2>
          <Badge v-if="runState" :label="runState.label" :theme="runState.theme" />
          <span v-if="duration && !isRunning" class="tabular-nums text-ink-gray-5 text-sm">
            {{ duration }}
          </span>

          <Button
            class="ml-auto"
            variant="ghost"
            :icon-left="isCopied ? 'lucide-check' : 'lucide-copy'"
            :disabled="!runState"
            @click="copyOutput"
          >
            {{ isCopied ? 'Copied' : 'Copy' }}
          </Button>

          <Button
            class="-ml-2"
            variant="ghost"
            icon-left="lucide-eraser"
            :disabled="!runState || isRunning"
            @click="runState = null"
          >
            Clear
          </Button>
        </header>

        <div
          v-if="!runState"
          class="flex flex-col flex-1 justify-center items-center gap-1.5 pb-10 text-center"
        >
          <p class="text-ink-gray-7 text-base-medium">Nothing run yet</p>
          <p class="text-ink-gray-5 text-sm">Ctrl Enter runs the script</p>
        </div>

        <pre
          v-else
          ref="outputElement"
          class="flex-1 px-4 pt-1 pb-6 min-h-0 overflow-auto font-mono text-ink-gray-8 text-sm leading-relaxed whitespace-pre-wrap break-words"
        ><template v-for="(line, index) in outputLines" :key="index"><span v-for="(part, partIndex) in line" :key="partIndex" :class="part.tone && TONES[part.tone]">{{ part.text }}</span>{{ index < outputLines.length - 1 ? '\n' : '' }}</template><span v-if="notice" class="text-ink-gray-5">{{ output ? '\n' : '' }}{{ notice }}</span><span v-else-if="!output && !isRunning" class="text-ink-gray-5 italic">No output</span></pre>
      </section>
    </main>

    <Transition
      enter-active-class="transition duration-200 ease-out motion-reduce:transition-none"
      enter-from-class="opacity-0 translate-x-6"
      leave-active-class="transition duration-150 ease-in motion-reduce:transition-none"
      leave-to-class="opacity-0 translate-x-6"
    >
      <ConsoleHistory
        v-if="isHistoryOpen"
        ref="history"
        :site="site"
        class="max-sm:absolute max-sm:inset-0"
        @pick="loadRun"
        @close="isHistoryOpen = false"
      />
    </Transition>
  </div>

  <Dialog v-model="showConfirm" title="Run in Read/Write mode">
    <p class="text-ink-gray-7 text-p-sm">
      Changes this script makes will be saved to the database.
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
