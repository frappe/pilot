<script setup lang="ts">
import { Button, Skeleton, TextInput } from 'frappe-ui'
import { computed, onMounted, ref } from 'vue'

import { auditApi } from '@/api/audit'
import type { AuditEntry } from '@/types/audit'
import { errorMessage } from '@/utils/error'

type Run = { entry: AuditEntry; count: number; title: string }

defineProps<{ site: string }>()
const emit = defineEmits<{ pick: [entry: AuditEntry]; close: [] }>()

const entries = ref<AuditEntry[]>([])
const isLoading = ref(true)
const error = ref('')
const picked = ref('')
const search = ref('')

const dayFormat = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short' })
const timeFormat = new Intl.DateTimeFormat(undefined, {
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

const dayLabel = (value: string) => {
  const today = new Date().setHours(0, 0, 0, 0)
  const days = Math.round((today - new Date(value).setHours(0, 0, 0, 0)) / 86400000)

  if (days === 0) return 'Today'
  if (days === 1) return 'Yesterday'

  return dayFormat.format(new Date(value))
}

const toRun = (entry: AuditEntry): Run => {
  const title = (entry.code ?? '')
    .split('\n')
    .filter((line) => !line.trim().startsWith('#'))
    .join(' ')
    .replace(/\s+/g, ' ')
    .replace(/([([{]) /g, '$1')
    .replace(/,? ([)\]}])/g, '$1')
    .trim()

  return { entry, count: 1, title }
}

const isRepeat = (run: Run, entry: AuditEntry) =>
  run.entry.code === entry.code &&
  run.entry.site === entry.site &&
  run.entry.commit === entry.commit &&
  dayLabel(run.entry.logged_at) === dayLabel(entry.logged_at)

const runs = computed(() => {
  const needle = search.value.trim().toLowerCase()
  const result: Run[] = []

  for (const entry of entries.value) {
    const last = result.at(-1)

    if (needle && !`${entry.code}\n${entry.site}`.toLowerCase().includes(needle)) continue

    if (last && isRepeat(last, entry)) last.count += 1
    else result.push(toRun(entry))
  }

  return result
})

const days = computed(() => {
  const result: { label: string; runs: Run[] }[] = []

  for (const run of runs.value) {
    const label = dayLabel(run.entry.logged_at)
    const last = result.at(-1)

    if (last?.label === label) last.runs.push(run)
    else result.push({ label, runs: [run] })
  }

  return result
})

const pick = (entry: AuditEntry) => {
  picked.value = entry.logged_at
  emit('pick', entry)
}

const load = async () => {
  isLoading.value = true
  error.value = ''

  try {
    entries.value = (await auditApi.list({ type: 'console_script', limit: 50 })).data
  } catch (caught) {
    error.value = errorMessage(caught, 'Could not load history.')
  } finally {
    isLoading.value = false
  }
}

onMounted(load)

defineExpose({ load })
</script>

<template>
  <aside
    class="flex flex-col bg-surface-base border-l border-outline-gray-2 w-full sm:w-[360px] shrink-0"
  >
    <header class="flex items-center gap-2 pr-2 pl-4 h-11 shrink-0">
      <h2 class="text-ink-gray-8 text-base-medium">History</h2>
      <Button
        class="ml-auto"
        variant="ghost"
        icon="lucide-x"
        label="Close history"
        @click="emit('close')"
      />
    </header>

    <TextInput v-model="search" class="mx-3 mb-2 shrink-0" placeholder="Search runs">
      <template #prefix>
        <span class="size-4 text-ink-gray-4 lucide-search" aria-hidden="true" />
      </template>
    </TextInput>

    <div class="flex-1 px-2 pb-10 min-h-0 overflow-y-auto">
      <template v-if="isLoading">
        <div v-for="index in 8" :key="index" class="flex items-center gap-3 px-2 h-8">
          <Skeleton class="rounded-4 w-9 h-3" />
          <Skeleton class="flex-1 rounded-4 h-3" />
        </div>
      </template>

      <p v-else-if="error" class="px-2 py-4 text-ink-gray-6 text-sm">{{ error }}</p>

      <p v-else-if="!entries.length" class="px-2 py-4 text-ink-gray-5 text-sm">
        No scripts run yet
      </p>

      <p v-else-if="!runs.length" class="px-2 py-4 text-ink-gray-5 text-sm">No scripts match</p>

      <section v-for="day in days" :key="day.label" class="[&:not(:first-child)]:mt-2">
        <h3 class="flex items-center px-2 h-8 text-ink-gray-6 text-sm-medium">{{ day.label }}</h3>

        <button
          v-for="run in day.runs"
          :key="run.entry.logged_at"
          type="button"
          class="flex items-center gap-3 px-2 rounded-4 w-full h-8 text-left transition-colors"
          :class="picked === run.entry.logged_at ? 'bg-surface-gray-2' : 'hover:bg-surface-gray-1'"
          @click="pick(run.entry)"
        >
          <span class="tabular-nums text-ink-gray-5 text-xs shrink-0">
            {{ timeFormat.format(new Date(run.entry.logged_at)) }}
          </span>
          <code class="flex-1 font-mono text-ink-gray-7 text-xs truncate">{{ run.title }}</code>
          <span v-if="run.entry.site !== site" class="max-w-24 text-ink-gray-5 text-xs truncate">
            {{ run.entry.site }}
          </span>
          <span v-if="run.count > 1" class="tabular-nums text-ink-gray-5 text-xs">
            ×{{ run.count }}
          </span>
          <span
            v-if="run.entry.commit"
            class="bg-surface-amber-5 rounded-full size-1.5 shrink-0"
            title="Read/Write"
          />
        </button>
      </section>
    </div>
  </aside>
</template>
