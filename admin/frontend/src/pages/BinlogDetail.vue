<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { Button, LoadingText } from 'frappe-ui'
import LucideChevronLeft from '~icons/lucide/chevron-left'
import LucideChevronRight from '~icons/lucide/chevron-right'

const route = useRoute()
const logName = route.params.name

const events = ref([])
const loading = ref(true)
const error = ref('')
const limit = ref(200)
const offset = ref(0)

// Colours per event type (Catppuccin Mocha)
const TYPE_COLOR = {
  Query:        '#89b4fa', // blue
  Table_map:    '#cba6f7', // mauve
  Write_rows:   '#a6e3a1', // green
  Update_rows:  '#f9e2af', // yellow
  Delete_rows:  '#f38ba8', // red
  Xid:          '#89dceb', // sky
  Rotate:       '#fab387', // peach
  Stop:         '#585b70', // subtext0
  Format_desc:  '#74c7ec', // sapphire
}

function typeColor(t) {
  return TYPE_COLOR[t] ?? '#cdd6f4'
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams({ limit: limit.value, offset: offset.value })
    const res = await fetch(`/api/database/binlogs/${logName}?${params}`)
    if (!res.ok) throw new Error(`${res.status}`)
    const d = await res.json()
    events.value = d.events
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function nextPage() { offset.value += limit.value; load() }
function prevPage() { offset.value = Math.max(0, offset.value - limit.value); load() }

onMounted(load)
</script>

<template>
  <div class="flex flex-col gap-3">
    <LoadingText v-if="loading" />

    <!-- Error -->
    <div v-else-if="error" class="rounded-lg px-4 py-3 font-mono text-sm" style="background:#1e1e2e;">
      <span style="color:#f38ba8;">Error: {{ error }}</span>
    </div>

    <template v-else>
      <!-- Terminal table -->
      <div
        class="overflow-auto rounded-lg font-mono text-sm leading-[1.6]"
        style="background:#1e1e2e; color:#cdd6f4; max-height:calc(100vh - 160px);"
      >
        <!-- Header row -->
        <div
          class="sticky top-0 flex gap-4 border-b px-4 py-2 text-xs font-semibold uppercase tracking-wider"
          style="background:#181825; color:#585b70; border-color:#313244;"
        >
          <span style="width:5rem; flex-shrink:0;">Pos</span>
          <span style="width:10rem; flex-shrink:0;">Type</span>
          <span style="width:5rem; flex-shrink:0;">End pos</span>
          <span class="flex-1">Info</span>
        </div>

        <!-- Empty state -->
        <div v-if="!events.length" class="px-4 py-3" style="color:#585b70;">
          No events in this range.
        </div>

        <!-- Event rows -->
        <div
          v-for="ev in events"
          :key="ev.pos"
          class="flex gap-4 px-4 py-0.5 hover:bg-white/[0.04]"
        >
          <span style="width:5rem; flex-shrink:0; color:#45475a;">{{ ev.pos }}</span>
          <span style="width:10rem; flex-shrink:0;" :style="`color:${typeColor(ev.event_type)}`">
            {{ ev.event_type }}
          </span>
          <span style="width:5rem; flex-shrink:0; color:#585b70;">{{ ev.end_log_pos }}</span>
          <span class="flex-1 break-all" style="color:#a6adc8;">{{ ev.info }}</span>
        </div>
      </div>

      <!-- Pagination -->
      <div class="flex items-center justify-between">
        <Button
          variant="outline"
          :prefix-icon="LucideChevronLeft"
          :disabled="offset === 0"
          @click="prevPage"
        >Previous</Button>
        <span class="font-mono text-xs text-ink-gray-4">
          {{ offset + 1 }}–{{ offset + events.length }}
        </span>
        <Button
          variant="outline"
          :suffix-icon="LucideChevronRight"
          :disabled="events.length < limit"
          @click="nextPage"
        >Next</Button>
      </div>
    </template>
  </div>
</template>
