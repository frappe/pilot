<template>
  <!-- Site selector on the right of the header -->
  <Teleport defer to="#header-actions">
    <FormControl type="select" v-model="selectedSite" :options="siteOptions"
      class="w-28 sm:w-44 max-w-[140px] sm:max-w-[180px]" />
  </Teleport>

  <!-- No site selected -->
  <div v-if="!selectedSite" class="flex flex-col justify-center items-center gap-2 text-center" style="height: 75vh;">
    <span class="size-8 text-ink-gray-3 lucide-database" />
    <p class="text-ink-gray-5 text-sm">Select a site from the dropdown to get started.</p>
  </div>

  <div v-else class="flex flex-col gap-3">
    <!-- Editor card -->
    <div class="border rounded-lg border-outline-gray-2 overflow-hidden transition-colors">
      <div class="h-44 sm:h-[220px]">
        <SQLCodeEditor ref="editorRef" v-model="query" :schema="schema" :db-type="selectedSiteDbType" @run="runQuery" />
      </div>
      <div
        class="flex flex-wrap justify-between items-center gap-2 bg-surface-base px-2 py-2 border-t border-outline-gray-2">
        <div class="flex flex-wrap items-center gap-2">
          <div class="sm:hidden">
            <FormControl type="select" v-model="modeStr" :options="modeOptions" />
          </div>
          <div class="hidden sm:block">
            <TabButtons v-model="modeStr" :options="modeOptions" />
          </div>
          <div class="hidden sm:block">
            <Button variant="outline" size="sm" iconLeft="lucide-table" :disabled="!schema.length"
              @click="showSchema = true">
              Tables
              <template v-if="schema.length" #suffix>
                <span class="text-ink-gray-4 text-xs">{{ schema.length }}</span>
              </template>
            </Button>
          </div>
        </div>
        <div class="flex items-center gap-3 ml-auto">
          <Button variant="solid" size="sm" iconLeft="lucide-play" :loading="running"
            :disabled="!selectedSite || !query.trim()" @click="runQuery">
            Execute
          </Button>
        </div>
      </div>
    </div>

    <!-- Error -->
    <Alert v-if="error" theme="red" title="Query failed" :dismissible="false">
      <template #description>
        <p class="font-mono text-xs break-words whitespace-pre-wrap">{{ error }}</p>
      </template>
    </Alert>

    <!-- Results -->
    <template v-if="results.length && !error">
      <!-- Query tabs (only when multiple statements) -->
      <div v-if="results.length > 1" class="overflow-x-auto hover-scrollbar">
        <TabButtons v-model="activeTab" type="underline" :options="tabOptions" />
      </div>

      <div v-if="currentResult" class="border rounded-lg border-outline-gray-2 overflow-hidden">
        <!-- Write query success (no result set) -->
        <div v-if="!currentResult.columns.length"
          class="flex justify-center items-center gap-2 py-8 text-ink-gray-6 text-sm">
          <span class="size-4 text-ink-green-4 lucide-check-circle" />
          Query executed successfully
          <span v-if="currentResult.affected_rows != null">· {{ currentResult.affected_rows }} row(s) affected</span>
        </div>

        <template v-else>
          <SimpleTable :columns="resultColumns" :rows="paginatedRowObjects" :show-index="true"
            :index-offset="(page - 1) * perPage" max-height="320px" :mono="false" truncate show-null
            empty-text="No rows returned." :bordered="false" />

          <!-- Table footer -->
          <div v-if="currentResult.row_count"
            class="flex flex-wrap justify-between items-center gap-2 bg-surface-base px-1 py-1 border-t border-outline-gray-2">
            <Button variant="ghost" size="xs" iconLeft="lucide-download" @click="exportCsv">
              Download as CSV
            </Button>
            <div class="flex items-center gap-3">
              <div class="hidden sm:flex items-center gap-1.5 pr-3 border-r-2 border-outline-gray-2">
                <span class="text-ink-gray-5 text-xs">Per Page</span>
                <FormControl type="select" v-model="perPage" class="max-w-16" :options="pageOptions" />
              </div>
              <span class="hidden sm:inline tabular-nums text-ink-gray-5 text-xs whitespace-nowrap">
                {{ rowRange }} of {{ currentResult.row_count }} rows
                <span v-if="currentResult.truncated">(truncated)</span>
              </span>
              <div class="flex items-center gap-1">
                <Button variant="ghost" size="xs" iconLeft="lucide-arrow-left" :disabled="page <= 1"
                  @click="page--">Prev</Button>
                <Button variant="ghost" size="xs" iconRight="lucide-arrow-right" :disabled="page >= totalPages"
                  @click="page++">Next</Button>
              </div>
            </div>
          </div>
        </template>
      </div>

      <!-- View SQL Query -->
      <div v-if="currentResult">
        <button class="flex items-center gap-1.5 text-ink-gray-5 hover:text-ink-gray-8 text-xs transition-colors"
          @click="showSql = !showSql">
          <span class="size-3" :class="showSql ? 'lucide-chevron-down' : 'lucide-chevron-right'" />
          View SQL Query
        </button>
        <pre v-if="showSql"
          class="bg-surface-gray-1 mt-1.5 px-3 py-2 border rounded-lg border-outline-gray-2 overflow-x-auto text-ink-gray-7 text-xs break-words whitespace-pre-wrap"
          style="font-family: ui-monospace, SFMono-Regular, monospace;">{{ currentResult.query }}</pre>
      </div>
    </template>
  </div>

  <!-- Tables schema browser -->
  <SQLSchemaDialog v-model="showSchema" :schema="schema" @preview="previewTable" />

  <!-- Confirm read/write execution -->
  <Dialog v-model="showConfirm" :options="{ title: 'Run in Read/Write mode', size: 'lg' }">
    <template #body-content>
      <p class="text-ink-gray-7 text-sm">
        This query will run in <strong>Read/Write</strong> mode and any changes will be committed to the database.
        Are you sure you want to continue?
      </p>
      <pre
        class="bg-surface-gray-1 mt-3 px-3 py-2 border rounded-lg border-outline-gray-2 max-h-40 overflow-y-auto text-ink-gray-7 text-xs break-words whitespace-pre-wrap"
        style="font-family: ui-monospace, SFMono-Regular, monospace;">{{ pendingQuery }}</pre>
      <div class="flex justify-end gap-2 mt-4">
        <Button variant="outline" @click="showConfirm = false">Cancel</Button>
        <Button variant="solid" @click="confirmRunQuery">Execute</Button>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Alert, Button, Dialog, FormControl, TabButtons } from 'frappe-ui'
import SQLCodeEditor from '@/components/database/SQLCodeEditor.vue'
import SQLSchemaDialog from '@/components/database/SQLSchemaDialog.vue'
import SimpleTable from '@/components/common/SimpleTable.vue'
import { apiErrorMessage } from '@/api/client'
import { databaseApi } from '@/api/database'

// ── State ─────────────────────────────────────────────────────────────────────

const route = useRoute()
const router = useRouter()

const sites = ref([])
const selectedSite = ref(route.query.site || '')
const query = ref('')
const modeStr = ref('readonly')
const readOnly = computed(() => modeStr.value === 'readonly')
const running = ref(false)
const results = ref([])
const activeTab = ref(0)
const error = ref('')
const showSql = ref(false)
const showSchema = ref(false)
const schema = ref([])
const editorRef = ref(null)

const siteOptions = computed(() => [
  { label: 'Select site', value: '' },
  ...sites.value.map((s) => ({ label: s.name, value: s.name })),
])

const selectedSiteDbType = computed(
  () => sites.value.find((s) => s.name === selectedSite.value)?.db_type || 'mariadb',
)

const modeOptions = [
  { label: 'Read-only', value: 'readonly' },
  { label: 'Read/Write', value: 'readwrite' },
]

const currentResult = computed(() => results.value[activeTab.value] || null)

const tabOptions = computed(() =>
  results.value.map((r, i) => ({ label: `Query ${i + 1}`, value: i })),
)

// ── Pagination ────────────────────────────────────────────────────────────────

const page = ref(1)
const perPage = ref(10)
const pageOptions = [
  { label: '10', value: 10 },
  { label: '25', value: 25 },
  { label: '50', value: 50 },
  { label: '100', value: 100 },
]

const resultColumns = computed(() =>
  currentResult.value ? currentResult.value.columns.map((c) => ({ key: c, label: c })) : [],
)

const paginatedRowObjects = computed(() => {
  if (!currentResult.value) return []
  const { columns, rows } = currentResult.value
  return rows
    .slice((page.value - 1) * perPage.value, page.value * perPage.value)
    .map((row) => Object.fromEntries(columns.map((col, i) => [col, row[i]])))
})

const totalPages = computed(() =>
  currentResult.value ? Math.ceil(currentResult.value.row_count / perPage.value) : 0,
)

const rowRange = computed(() => {
  if (!currentResult.value) return ''
  const start = (page.value - 1) * perPage.value + 1
  const end = Math.min(page.value * perPage.value, currentResult.value.row_count)
  return currentResult.value.row_count ? `${start}–${end}` : '0–0'
})

// ── Query execution ───────────────────────────────────────────────────────────

const showConfirm = ref(false)
const pendingQuery = ref('')

function runQuery() {
  const raw = editorRef.value?.getQueryToRun() ?? query.value
  if (!raw?.trim()) return
  if (!readOnly.value) {
    pendingQuery.value = raw
    showConfirm.value = true
    return
  }
  executeQuery(raw)
}

function confirmRunQuery() {
  showConfirm.value = false
  executeQuery(pendingQuery.value)
}

// MariaDB quotes identifiers with backticks; Postgres and SQLite use the
// standard double-quote (MariaDB treats double quotes as a string literal
// unless ANSI_QUOTES is set, so backticks aren't a safe cross-engine default).
function quoteIdentifier(name, dbType) {
  return dbType === 'mariadb' ? `\`${name}\`` : `"${name}"`
}

function previewTable(tableName) {
  modeStr.value = 'readonly'
  query.value = `SELECT * FROM ${quoteIdentifier(tableName, selectedSiteDbType.value)} LIMIT 100;`
  executeQuery(query.value)
}

async function executeQuery(raw) {
  if (!selectedSite.value || !raw?.trim()) return
  const statements = raw.split(';').map((s) => s.trim()).filter(Boolean)
  if (!statements.length) return

  running.value = true
  error.value = ''
  results.value = []
  activeTab.value = 0
  page.value = 1
  showSql.value = false

  try {
    const executed = []
    for (const stmt of statements) {
      const data = await databaseApi.execute(selectedSite.value, stmt, readOnly.value)
      if (data.error) throw new Error(apiErrorMessage(data, 'Query failed.'))
      executed.push({ ...data, query: stmt })
    }
    results.value = executed
    if (!readOnly.value) refreshSchema()
  } catch (e) {
    error.value = e.message || 'Query failed'
  } finally {
    running.value = false
  }
}

// ── CSV export ────────────────────────────────────────────────────────────────

function exportCsv() {
  if (!currentResult.value) return
  const { columns, rows } = currentResult.value
  const escape = (v) => {
    if (v === null) return ''
    const s = String(v)
    return s.includes(',') || s.includes('"') || s.includes('\n') ? `"${s.replace(/"/g, '""')}"` : s
  }
  const csv = [columns.join(','), ...rows.map((r) => r.map(escape).join(','))].join('\n')
  const a = Object.assign(document.createElement('a'), {
    href: URL.createObjectURL(new Blob([csv], { type: 'text/csv' })),
    download: `${selectedSite.value}-query.csv`,
  })
  a.click()
  URL.revokeObjectURL(a.href)
}

// ── Schema ────────────────────────────────────────────────────────────────────

async function refreshSchema() {
  if (!selectedSite.value) return
  try {
    const data = await databaseApi.schema(selectedSite.value)
    if (!data.error) schema.value = data
  } catch {
    // keep last known schema on failure
  }
}

// ── Watchers ──────────────────────────────────────────────────────────────────

watch(selectedSite, (site) => {
  if ((route.query.site || '') !== site) {
    router.replace({ path: route.path, query: site ? { site } : {} })
  }
  query.value = site ? localStorage.getItem(`last_sql_query_${site}`) || '' : ''
  results.value = []
  error.value = ''
  schema.value = []
  if (!site) return
  refreshSchema()
}, { immediate: true })

watch(query, (value) => {
  if (selectedSite.value) localStorage.setItem(`last_sql_query_${selectedSite.value}`, value)
})

watch(activeTab, () => { page.value = 1 })
watch(perPage, () => { page.value = 1 })

// ── Lifecycle ─────────────────────────────────────────────────────────────────

onMounted(async () => {
  try {
    sites.value = await databaseApi.sites()
    if (!selectedSite.value && sites.value.length === 1) selectedSite.value = sites.value[0].name
  } catch { }
})
</script>
