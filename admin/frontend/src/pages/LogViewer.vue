<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { Button, FormControl } from 'frappe-ui'
import TerminalOutput from '../components/TerminalOutput.vue'
import { processLine } from '../utils/ansi.js'
import LucideRefreshCw from '~icons/lucide/refresh-cw'
import LucideDownload from '~icons/lucide/download'
import LucideRadio from '~icons/lucide/radio'

const route = useRoute()
const filename = route.params.filename

const lines = ref([])
const loading = ref(true)
const error = ref('')
const search = ref(route.query.search || '')
const linesCount = ref(Number(route.query.lines) || 200)
const liveMode = ref(false)
const terminal = ref(null)
let es = null

async function load() {
  loading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams({ lines: linesCount.value })
    if (search.value) params.set('search', search.value)
    const res = await fetch(`/api/logs/${filename}?${params}`)
    if (!res.ok) throw new Error(`${res.status}`)
    const d = await res.json()
    lines.value = d.lines.map(processLine)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function startLive() {
  liveMode.value = true
  lines.value = []
  es = new EventSource(`/api/logs/${filename}/stream`)
  es.onmessage = (e) => {
    lines.value.push(processLine(e.data))
    if (lines.value.length > 2000) lines.value.shift()
    terminal.value?.scrollToBottom()
  }
  es.onerror = () => stopLive()
}

function stopLive() {
  liveMode.value = false
  if (es) { es.close(); es = null }
  load()
}

onMounted(load)
onUnmounted(() => { if (es) es.close() })
</script>

<template>
  <div class="flex h-full flex-col gap-3">
    <!-- Toolbar -->
    <div class="flex flex-wrap items-center gap-2">
      <FormControl
        type="text"
        v-model="search"
        placeholder="Search…"
        class="w-48"
        @keyup.enter="load"
      />
      <FormControl
        type="select"
        v-model="linesCount"
        class="w-36"
        :options="[
          { label: 'Last 100 lines', value: 100 },
          { label: 'Last 200 lines', value: 200 },
          { label: 'Last 500 lines', value: 500 },
          { label: 'Last 1000 lines', value: 1000 },
        ]"
        @change="load"
      />
      <Button variant="outline" :prefix-icon="LucideRefreshCw" :loading="loading" @click="load">
        Refresh
      </Button>
      <Button
        v-if="!liveMode"
        variant="outline"
        :prefix-icon="LucideRadio"
        @click="startLive"
      >
        Live tail
      </Button>
      <Button
        v-else
        variant="solid"
        theme="red"
        :prefix-icon="LucideRadio"
        @click="stopLive"
      >
        Stop
      </Button>
      <a :href="`/api/logs/${filename}/download`" class="ml-auto">
        <Button variant="ghost" :prefix-icon="LucideDownload">Download</Button>
      </a>
    </div>

    <!-- Terminal -->
    <div v-if="error" class="rounded-lg px-4 py-3 font-mono text-sm" style="background:#1e1e2e;">
      <span style="color:#f38ba8;">Error: {{ error }}</span>
    </div>
    <TerminalOutput
      v-else
      ref="terminal"
      :lines="lines"
      :streaming="liveMode"
      :empty-text="loading ? 'Loading…' : search ? 'No lines match your search.' : 'Log file is empty.'"
      max-height="calc(100vh - 180px)"
    />

    <div v-if="lines.length" class="text-xs" style="color:#585b70;">
      {{ lines.length }} line{{ lines.length !== 1 ? 's' : '' }}
    </div>
  </div>
</template>
