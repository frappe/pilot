<script setup lang="ts">
import { Badge, DateTimePicker, Dialog, ErrorMessage } from 'frappe-ui'
import { computed, ref, watch } from 'vue'

import EmptyState from '@/components/common/EmptyState.vue'
import Table from '@/components/common/Table.vue'
import DatabasePanel from '@/components/database/DatabasePanel.vue'

import { apiErrorMessage, hasApiError } from '@/api/client'
import { databaseApi } from '@/api/database'
import type { Deadlock, DeadlockReport, DeadlockTransaction } from '@/types/database'
import { errorMessage } from '@/utils/error'

interface Props {
  active?: boolean
  site?: string
  badge?: string
  showSite?: boolean
  siteByDatabase?: Record<string, string>
}

const props = withDefaults(defineProps<Props>(), {
  active: false,
  site: '',
  badge: '',
  showSite: false,
  siteByDatabase: () => ({}),
})

const report = ref<DeadlockReport | null>(null)
const loading = ref(false)
const error = ref('')
const opened = ref<Deadlock | null>(null)
const since = ref('')
const until = ref('')

// DateTimePicker gives browser-local 'YYYY-MM-DD HH:mm:ss'; the API takes UTC ISO and the log is server time.
const toIsoTime = (value: string) =>
  value ? new Date(value.replace(' ', 'T')).toISOString() : undefined
const localTime = (iso: string) => (iso ? new Date(iso).toLocaleString() : '—')

const columns = computed(() => [
  { key: 'time', label: 'Time', class: 'w-[16%]' },
  ...(props.showSite ? [{ key: 'site', label: 'Site', class: 'w-[18%]' }] : []),
  { key: 'table', label: 'Table', class: 'w-[16%]' },
  { key: 'victim', label: 'Rolled back', class: 'w-[25%]' },
  { key: 'winner', label: 'Went through' },
])

const victimOf = (deadlock: Deadlock) => deadlock.transactions.find((item) => item.victim)
const winnerOf = (deadlock: Deadlock) => deadlock.transactions.find((item) => !item.victim)

const rows = computed(() =>
  (report.value?.deadlocks ?? []).map((deadlock, index) => {
    const first = deadlock.transactions[0]
    return {
      id: index,
      deadlock,
      time: localTime(deadlock.time),
      site: props.siteByDatabase[first?.database] || first?.database || '—',
      table: [...new Set(deadlock.transactions.map((item) => item.table).filter(Boolean))].join(', ') || '—',
      victim: victimOf(deadlock)?.query || '—',
      winner: winnerOf(deadlock)?.query || '—',
    }
  }),
)

const emptyState = computed(() => {
  if (report.value && !report.value.log_path)
    return {
      title: 'No error log file',
      description: 'MariaDB writes its errors to the journal. Restart the database so they go to a file Pilot can read.',
    }
  if (report.value && !report.value.print_all_deadlocks)
    return {
      title: 'Deadlock logging is off',
      description: 'Set innodb_print_all_deadlocks to ON so InnoDB writes each deadlock to the error log.',
    }
  if (since.value || until.value)
    return { title: 'No deadlocks', description: 'None in this time range' }
  return { title: 'No deadlocks', description: 'None in the recent error log' }
})

const transactionLabel = (item: DeadlockTransaction) =>
  [`Transaction ${item.transaction_id}`, item.thread_id ? `thread ${item.thread_id}` : '']
    .filter(Boolean)
    .join(', ')

const load = async () => {
  loading.value = true
  error.value = ''
  try {
    const result = await databaseApi.deadlocks(props.site, {
      since: toIsoTime(since.value),
      until: toIsoTime(until.value),
    })
    if (hasApiError(result)) throw new Error(apiErrorMessage(result, 'Could not load deadlocks.'))
    report.value = result
  } catch (caught) {
    error.value = errorMessage(caught, 'Could not load deadlocks.')
  } finally {
    loading.value = false
  }
}

watch(
  () => props.site,
  () => report.value && load(),
)
</script>

<template>
  <DatabasePanel :active="active" :badge="badge" :loading="loading" @open="load" @refresh="load">
    <template #actions>
      <DateTimePicker
        v-model="since"
        class="w-44"
        placeholder="From"
        clearable
        @update:modelValue="load"
      />
      <DateTimePicker
        v-model="until"
        class="w-44"
        placeholder="To"
        clearable
        @update:modelValue="load"
      />
    </template>

    <ErrorMessage v-if="error" :message="error" />

    <Table v-else-if="rows.length" :columns="columns" :rows="rows" height="flex-1">
      <template #victim="{ row }">
        <button type="button" class="max-w-full text-left truncate" @click="opened = row.deadlock">
          {{ row.victim }}
        </button>
      </template>
      <template #winner="{ row }">
        <button type="button" class="max-w-full text-left truncate" @click="opened = row.deadlock">
          {{ row.winner }}
        </button>
      </template>
    </Table>

    <EmptyState
      v-else
      icon="lucide-swords"
      :title="emptyState.title"
      :description="emptyState.description"
      :bordered="false"
      class="flex-1 justify-center pb-16"
    />
  </DatabasePanel>

  <Dialog
    :model-value="Boolean(opened)"
    :title="`Deadlock at ${localTime(opened?.time ?? '')}`"
    size="2xl"
    @update:model-value="opened = null"
  >
    <div class="flex flex-col gap-4">
      <div v-for="item in opened?.transactions ?? []" :key="item.number" class="flex flex-col gap-2">
        <div class="flex items-center gap-2 text-ink-gray-8 text-sm">
          <span class="font-medium">{{ transactionLabel(item) }}</span>
          <Badge v-if="item.victim" label="Rolled back" theme="red" size="sm" />
        </div>
        <p class="text-ink-gray-5 text-sm">
          Waited for {{ item.lock_mode || 'a lock' }} on {{ item.table || '—' }}
          <template v-if="item.index">({{ item.index }})</template>
        </p>
        <pre
          class="bg-surface-gray-2 p-3 border rounded-6 border-outline-gray-1 text-ink-gray-7 text-sm whitespace-pre-wrap"
          >{{ item.query || '—' }}</pre
        >
      </div>
    </div>
  </Dialog>
</template>
