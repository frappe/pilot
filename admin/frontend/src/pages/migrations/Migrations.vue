<template>
  <div class="mx-auto max-w-3xl">
    <div class="flex justify-between items-center gap-3">
      <div>
        <h1 class="font-semibold text-ink-gray-9 text-xl">Migrations</h1>
        <p class="mt-1 text-ink-gray-5 text-p-base hidden sm:block">
          App updates and site migrations, with recovery.
        </p>
      </div>
      <Button variant="subtle" size="sm" :loading="loading" icon-left="lucide-refresh-cw" @click="load">
        Refresh
      </Button>
    </div>

    <div v-if="loading && !operations.length" class="flex justify-center mt-16">
      <LoadingText />
    </div>
    <ErrorMessage v-else-if="error" class="mt-4" :message="error" />

    <div v-else-if="operations.length" class="bg-surface-elevation-1 mt-4 divide-outline-gray-1 divide-y overflow-hidden">
      <RouterLink v-for="op in operations" :key="op.id"
        :to="{ name: 'MigrationDetail', params: { operationId: op.id } }"
        class="flex items-center gap-3 py-3 no-underline transition-colors">
        <MigrationStateBadge :state="op.state" />
        <div class="flex-1 min-w-0">
          <span class="font-medium text-ink-gray-9 text-base truncate">{{ kindLabel(op.kind) }}</span>
          <p class="mt-0.5 text-ink-gray-5 text-p-sm truncate">
            {{ siteSummary(op) }}<template v-if="op.started_at"> · {{ fmtDate(op.started_at) }}</template>
          </p>
        </div>
        <span class="lucide-chevron-right size-4 text-ink-gray-4 shrink-0" />
      </RouterLink>
    </div>

    <p v-else class="mt-16 text-ink-gray-5 text-sm text-center">No migrations yet.</p>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { Button, ErrorMessage, LoadingText } from 'frappe-ui'
import { migrationsApi } from '@/api/migrations'
import MigrationStateBadge from '@/components/migrations/MigrationStateBadge.vue'
import { kindLabel, siteSummary, fmtDate } from '@/utils/migrationFormat'

const operations = ref([])
const loading = ref(false)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [current, history] = await Promise.all([
      migrationsApi.current().catch(() => null),
      migrationsApi.list({ limit: 50 }),
    ])
    const rows = history.data || []
    // Pin the active/unresolved operation at the top (it is also in history).
    operations.value = current ? [current, ...rows.filter((op) => op.id !== current.id)] : rows
  } catch (e) {
    error.value = e?.message || 'Could not load migrations.'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
