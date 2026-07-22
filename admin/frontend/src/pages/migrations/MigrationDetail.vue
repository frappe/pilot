<template>
  <div class="mx-auto max-w-3xl">
    <div v-if="loading && !op" class="flex justify-center py-12">
      <LoadingText />
    </div>
    <ErrorMessage v-else-if="error && !op" class="mt-4" :message="error" />

    <template v-else-if="op">
      <!-- Header -->
      <div class="flex justify-between items-center gap-3">
        <div class="flex items-center gap-2 min-w-0">
          <Button class="shrink-0" variant="subtle" size="sm" icon="lucide-arrow-left"
            @click="router.push({ name: 'Migrations' })" />
          <h1 class="flex-1 min-w-0 font-semibold text-ink-gray-9 text-xl truncate">{{ title }}</h1>
          <MigrationStateBadge class="shrink-0" :state="op.state" />
        </div>
        <Button variant="subtle" size="sm" icon="lucide-refresh-cw" :loading="refreshing" @click="refresh" />
      </div>

      <!-- Metadata -->
      <div class="gap-4 grid grid-cols-2 bg-surface-elevation-1 mt-4 px-0 py-4 rounded-xl sm:grid-cols-5">
        <div v-for="item in metadata" :key="item.label">
          <p class="text-xs text-ink-gray-4">{{ item.label }}</p>
          <button v-if="item.taskId" type="button" class="mt-1 block truncate text-sm text-ink-gray-8 hover:underline"
            @click="openTaskLog({ id: item.taskId })">
            {{ item.value }}
          </button>
          <p v-else class="mt-1 truncate text-sm text-ink-gray-8">{{ item.value }}</p>
        </div>
      </div>

      <ErrorMessage v-if="error" class="mt-4" :message="error" />

      <!-- Unresolved failure -->
      <section v-if="isAttention" class="mt-4 overflow-hidden rounded-xl border border-outline-red-2">
        <div class="flex items-start gap-3 bg-surface-red-1 p-4 sm:p-5">
          <span class="lucide-alert-triangle mt-0.5 size-5 shrink-0 text-ink-red-6" />
          <div class="min-w-0 flex-1">
            <h2 class="font-semibold text-sm">This migration needs attention</h2>
            <p v-if="op.diagnosis?.message" class="mt-1 text-p-sm leading-5 text-ink-red-8">
              {{ op.diagnosis.message }}
            </p>
            <p v-if="op.diagnosis?.patch" class="mt-2 text-p-sm text-ink-gray-7">
              Failing patch
              <code class="ml-1 rounded bg-surface-red-2 px-1.5 py-0.5 font-mono text-xs text-ink-red-9">
                {{ op.diagnosis.patch }}
              </code>
            </p>
            <p v-if="patchAlreadySkipped" class="mt-2 flex items-center gap-1 text-p-sm font-medium text-ink-green-7">
              <span class="lucide-check size-4" />
              Patch Skipped
            </p>
            <p class="mt-3 text-p-sm text-ink-gray-6">
              <template v-if="op.state === 'revert_failed'">Fix the cause and run the restore again.</template>
              <template v-else>
                Fix the cause manually, then retry. Or restore everything back to its pre-update state.
              </template>
            </p>

            <div class="mt-4 flex flex-wrap gap-2">
              <Button v-if="op.state === 'needs_attention' && op.diagnosis?.patch && !patchAlreadySkipped"
                variant="solid" theme="red" :loading="acting" @click="confirmSkip = true">
                Skip patch
              </Button>
              <Button v-if="op.state === 'needs_attention'" variant="outline" theme="gray" :loading="acting"
                @click="doRetry">
                Retry migration
              </Button>
              <Button v-if="op.can_restore" variant="outline" theme="gray" :loading="acting"
                @click="confirmRestore = true">
                Restore backup
              </Button>
            </div>
          </div>
        </div>

        <details v-if="op.diagnosis?.output_excerpt" class="border-t border-outline-red-2 bg-surface-elevation-1">
          <summary
            class="cursor-pointer px-4 py-3 text-p-sm font-medium text-ink-gray-7 hover:bg-surface-gray-1 sm:px-5">
            Show error output
          </summary>
          <pre
            class="m-3 max-h-64 overflow-auto rounded-lg bg-surface-gray-9 p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap text-ink-gray-1 sm:m-4">
    {{ op.diagnosis.output_excerpt }}</pre>
        </details>
      </section>

      <!-- Sites -->
      <section class="mt-4 overflow-hidden rounded-xl border border-outline-gray-2">
        <div class="flex items-center justify-between border-b border-outline-gray-1 px-4 py-3.5 sm:px-5">
          <div class="flex items-center gap-2">
            <span class="lucide-server size-4 text-ink-gray-5" />
            <h2 class="text-base font-semibold text-ink-gray-8">Sites</h2>
          </div>
          <span class="text-p-sm text-ink-gray-5">{{ countLabel(op.sites?.length, 'site') }}</span>
        </div>

        <div v-if="op.sites?.length" class="divide-y divide-outline-gray-1">
          <div v-for="site in op.sites" :key="site.name"
            class="flex items-center justify-between gap-4 px-4 py-3 sm:px-5">
            <RouterLink :to="{ name: 'SiteDetail', params: { name: site.name } }"
              class="min-w-0 flex-1 truncate font-medium text-ink-gray-9 text-sm no-underline hover:text-ink-gray-7">
              {{ site.name }}
            </RouterLink>
            <div class="flex shrink-0 items-center gap-1.5">
              <span v-if="siteStatus(site).busy" class="lucide-loader-circle size-3.5 animate-spin text-ink-amber-7" />
              <Badge :theme="badgeTone(siteStatus(site).tone)" variant="subtle" :label="siteStatus(site).label" />
              <Dropdown :options="siteLogOptions(site.name)" placement="bottom-end">
                <template #default="{ open }">
                  <Button variant="ghost" size="sm" :active="open" tooltip="Site jobs">
                    <span class="lucide-list-checks size-4" />
                  </Button>
                </template>
              </Dropdown>
            </div>
          </div>
        </div>
        <p v-else class="px-5 py-8 text-center text-p-sm text-ink-gray-5">No sites are part of this migration.</p>
      </section>

      <!-- Apps -->
      <section v-if="op.apps?.length" class="mt-4 overflow-hidden rounded-xl border border-outline-gray-2">
        <div class="flex items-center justify-between border-b border-outline-gray-1 px-4 py-3.5 sm:px-5">
          <div class="flex items-center gap-2">
            <span class="lucide-git-branch size-4 text-ink-gray-5" />
            <h2 class="text-base font-semibold text-ink-gray-8">Target apps</h2>
          </div>
          <span class="text-p-sm text-ink-gray-5">{{ countLabel(op.apps.length, 'app') }}</span>
        </div>

        <div class="divide-y divide-outline-gray-1">
          <div v-for="app in op.apps" :key="app.name" class="flex items-center justify-between gap-4 px-4 py-3 sm:px-5">
            <p class="min-w-0 flex-1 truncate font-medium text-ink-gray-9 text-sm">{{ app.name }}</p>
            <div class="flex shrink-0 items-center gap-2 font-mono text-xs text-ink-gray-6">
              <span>{{ shortSha(app.sha) }}</span>
              <span class="lucide-arrow-right size-3.5 text-ink-gray-4" aria-hidden="true" />
              <span :class="app.updated_sha ? 'text-ink-green-7' : 'text-ink-gray-5'">
                {{ shortSha(app.updated_sha || app.target_sha) }}
              </span>
              <a v-if="app.compare_url" :href="app.compare_url" target="_blank" rel="noopener noreferrer"
                class="lucide-external-link size-3.5 text-ink-gray-4 hover:text-ink-gray-7" aria-label="Open diff" />
            </div>
          </div>
        </div>
      </section>

      <!-- User decisions -->
      <section v-if="op.decisions?.length" class="mt-4 overflow-hidden rounded-xl border border-outline-gray-2">
        <div class="flex items-center gap-2 border-b border-outline-gray-1 px-4 py-3.5 sm:px-5">
          <span class="lucide-gavel size-4 text-ink-gray-5" />
          <h2 class="text-base font-semibold text-ink-gray-8">Decisions</h2>
        </div>
        <div class="divide-y divide-outline-gray-1">
          <div v-for="(decision, index) in op.decisions" :key="index" class="px-4 py-3 text-sm text-ink-gray-7 sm:px-5">
            Skipped patch
            <code class="rounded bg-surface-gray-2 px-1 font-mono text-xs">{{ decision.patch }}</code>
            on <span class="font-medium text-ink-gray-8">{{ decision.site }}</span>
          </div>
        </div>
      </section>

      <!-- Skip patch confirmation -->
      <Dialog v-model="confirmSkip" :options="{ title: 'Skip this patch permanently?' }">
        <template #body-content>
          <p class="text-p-sm text-ink-gray-6">
            Skipping marks
            <code class="rounded bg-surface-gray-2 px-1 font-mono">{{ op.diagnosis?.patch }}</code>
            as completed for <b class="text-ink-gray-9">{{ op.failed_site }}</b> without running it. This cannot be
            undone. Retry the migration afterwards to continue.
          </p>
        </template>
        <template #actions>
          <div class="flex flex-row justify-end">
              <Button variant="solid" theme="red" :loading="acting" @click="doSkip">Skip patch</Button>
          </div>
        </template>
      </Dialog>

      <!-- Restore confirmation -->
      <Dialog v-model="confirmRestore" :options="{ title: 'Restore this update?' }">
        <template #body-content>
          <p class="text-p-sm text-ink-gray-6">
            Apps return to their previous revisions, and migrated sites get their pre-update data back from the
            recovery backup. Sites that were not migrated yet are left untouched.
          </p>
        </template>
        <template #actions>
          <Button variant="solid" theme="red" :loading="acting" @click="doRestore">Restore backup</Button>
        </template>
      </Dialog>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Badge, Button, Dialog, Dropdown, ErrorMessage, LoadingText } from 'frappe-ui'
import { migrationsApi, isActive, isResolved, needsAttention } from '@/api/migrations'
import { useBreadcrumbs } from '@/composables/common/useBreadcrumbs'
import { fmtDateTime, fmtDuration } from '@/utils/taskFormat'
import { opTitle, patchSkipped, siteStatus } from '@/utils/migrationFormat'

const props = defineProps({ operationId: { type: String, required: true } })
const router = useRouter()
const { setBreadcrumbs } = useBreadcrumbs()

const op = ref(null)
const loading = ref(false)
const refreshing = ref(false)
const acting = ref(false)
const error = ref('')
const confirmSkip = ref(false)
const confirmRestore = ref(false)
// Set after an action queues a task; keeps polling through the attention -> active
// transition, which the backend applies only when the task starts running.
const awaitingTransition = ref(false)
let timer = null

const title = computed(() => opTitle(op.value))
const isAttention = computed(() => needsAttention(op.value))
const patchAlreadySkipped = computed(() => patchSkipped(op.value))

const durationSeconds = computed(() => {
  if (!op.value?.started_at) return null
  // Paused on a failure, waiting for the user: the clock is not running.
  if (!isResolved(op.value) && !isActive(op.value)) return null
  const end = op.value.finished_at ? new Date(op.value.finished_at).getTime() : Date.now()
  return Math.max(0, (end - new Date(op.value.started_at).getTime()) / 1000)
})

// The 'update' phase runs once per operation, so a single chain entry identifies it;
// 'restore' is the task_ids role the restore/revert action is queued under (api/migrations.js).
const updateTaskId = computed(() => op.value?.chain?.find((entry) => entry.command === 'update')?.task_id)
const revertTaskId = computed(() => op.value?.task_ids?.restore)

const metadata = computed(() => [
  { label: 'Started', value: fmtDateTime(op.value.started_at) },
  { label: 'Finished', value: op.value.finished_at ? fmtDateTime(op.value.finished_at) : '-' },
  { label: 'Duration', value: fmtDuration(durationSeconds.value) || '-' },
  { label: 'Update Task', value: updateTaskId.value ? 'View task' : '-', taskId: updateTaskId.value },
  { label: 'Revert Task', value: revertTaskId.value ? 'View task' : '-', taskId: revertTaskId.value },
])

const openTaskLog = (log) => router.push({ name: 'TaskDetail', params: { taskId: log.id } })

function siteLogOptions(siteName) {
  const logs = (op.value?.task_logs || []).filter((log) => log.site === siteName)
  if (!logs.length) return [{ label: 'No tasks yet', disabled: true }]
  return logs.map((log) => ({
    label: log.label,
    icon: 'lucide-square-terminal',
    onClick: () => openTaskLog(log),
  }))
}

async function load() {
  try {
    op.value = await migrationsApi.detail(props.operationId)
    error.value = ''
    setBreadcrumbs([{ label: 'Migrations', route: { name: 'Migrations' } }, { label: title.value }])
  } catch (e) {
    error.value = e?.message || 'Could not load this migration.'
  } finally {
    schedule()
  }
}

async function refresh() {
  refreshing.value = true
  try {
    await load()
  } finally {
    refreshing.value = false
  }
}

function schedule() {
  clearTimeout(timer)
  if (isActive(op.value)) awaitingTransition.value = false
  if (op.value && !isResolved(op.value) && (!isAttention.value || awaitingTransition.value)) {
    timer = setTimeout(load, 3000)
  }
}

async function runAction(action) {
  acting.value = true
  try {
    await action()
    awaitingTransition.value = true
    await load()
  } catch (e) {
    error.value = e?.message || 'Action failed.'
  } finally {
    acting.value = false
  }
}

const doRetry = () => runAction(() => migrationsApi.retry(props.operationId))
const doRestore = () => {
  confirmRestore.value = false
  return runAction(() => migrationsApi.restore(props.operationId))
}
const doSkip = () => {
  confirmSkip.value = false
  return runAction(() => migrationsApi.bypassPatch(props.operationId, op.value.diagnosis.patch))
}

const badgeTone = (tone) => (tone === 'orange' ? 'amber' : tone)
const countLabel = (count = 0, noun) => `${count} ${noun}${count === 1 ? '' : 's'}`
const shortSha = (sha) => sha?.slice(0, 7) || '—'

onMounted(async () => {
  loading.value = true
  try {
    await load()
  } finally {
    loading.value = false
  }
})
onUnmounted(() => clearTimeout(timer))
</script>
