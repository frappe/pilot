<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import {
  Badge,
  Button,
  Dialog,
  ErrorMessage,
  Skeleton,
  Spinner,
  Tooltip,
} from 'frappe-ui'
import AppIcon from '@/components/apps/AppIcon.vue'
import JobRow from '@/components/updates/JobRow.vue'
import LogView from '@/components/logs/LogView.vue'
import UpdateSection from '@/components/updates/UpdateSection.vue'
import UpdateStateBadge from '@/components/updates/UpdateStateBadge.vue'
import { useAppRegistry } from '@/composables/apps/useAppRegistry'
import { processLine } from '@/utils/ansi'
import { updatesApi, isActive, isResolved, needsAttention } from '@/api/updates'
import { useBreadcrumbs } from '@/composables/common/useBreadcrumbs'
import { fmtDateTime, fmtDuration } from '@/utils/taskFormat'
import { opTitle, patchSkipped, pendingActionLabel, siteStatus } from '@/utils/updateFormat'

interface Props {
  operationId: string
}

const props = defineProps<Props>()
const router = useRouter()
const { setBreadcrumbs } = useBreadcrumbs()

const op = ref(null)
const loading = ref(false)
const refreshing = ref(false)
const acting = ref(false)
const error = ref('')
const confirmSkip = ref(false)
const confirmRestore = ref(false)
let timer = null

const title = computed(() => opTitle(op.value))
const isAttention = computed(() => needsAttention(op.value))
const pending = computed(() => op.value?.pending_action || null)
const pendingLabel = computed(() => pendingActionLabel(pending.value))
const patchAlreadySkipped = computed(() => patchSkipped(op.value))

const durationSeconds = computed(() => {
  if (!op.value?.started_at) return null
  // Paused on a failure, waiting for the user: the clock is not running.
  if (!isResolved(op.value) && !isActive(op.value)) return null
  const end = op.value.finished_at ? new Date(op.value.finished_at).getTime() : Date.now()
  return Math.max(0, (end - new Date(op.value.started_at).getTime()) / 1000)
})

// Bench-wide chain tasks (update, revert apps, restart); site-bound ones live in the site tree.
const serverJobs = computed(() => (op.value?.task_logs || []).filter((log) => !log.site))

const alertTitle = computed(() =>
  op.value?.state === 'revert_failed' ? 'Restore failed' : 'This update needs attention',
)

const showOutput = ref(false)
const outputLines = computed(() =>
  (op.value?.diagnosis?.output_excerpt || '').split('\n').map(processLine),
)

const sitesCount = computed(() => {
  const sites = op.value?.sites || []
  if (sites.length > 1 && !isResolved(op.value)) {
    const migrated = sites.filter((site) =>
      ['success', 'recovered'].includes(site.migration_status),
    ).length
    return `${migrated}/${sites.length}`
  }
  return `${sites.length}`
})

const startedAt = computed(() =>
  op.value.started_at ? fmtDateTime(op.value.started_at) : '',
)

const duration = computed(() => fmtDuration(durationSeconds.value))

const openTaskLog = (log) => router.push({ name: 'TaskDetail', params: { taskId: log.id } })

const expandedSites = ref(new Set())

const toggleSiteJobs = (siteName) => {
  if (!siteJobs(siteName).length) return
  const expanded = new Set(expandedSites.value)
  if (!expanded.delete(siteName)) expanded.add(siteName)
  expandedSites.value = expanded
}

const siteJobs = (siteName) => {
  return (op.value?.task_logs || []).filter((log) => log.site === siteName)
}

const load = async () => {
  try {
    op.value = await updatesApi.detail(props.operationId)
    error.value = ''
    applyOpenDefaults()
    setBreadcrumbs([{ label: 'Updates', route: { name: 'Updates' } }, { label: title.value }])
  } catch (e) {
    error.value = e?.message || 'Could not load this update.'
  } finally {
    schedule()
  }
}

const refresh = async () => {
  refreshing.value = true
  try {
    await load()
  } finally {
    refreshing.value = false
  }
}

const schedule = () => {
  clearTimeout(timer)
  if (op.value && !isResolved(op.value) && (!isAttention.value || pending.value)) {
    timer = setTimeout(load, 3000)
  }
}

const runAction = async (action) => {
  acting.value = true
  try {
    op.value = (await action()).operation || op.value
    await load()
  } catch (e) {
    error.value = e?.message || 'Action failed.'
  } finally {
    acting.value = false
  }
}

const doRetry = () => runAction(() => updatesApi.retry(props.operationId))
const doRestore = () => {
  confirmRestore.value = false
  return runAction(() => updatesApi.restore(props.operationId))
}
const doSkip = () => {
  confirmSkip.value = false
  return runAction(() => updatesApi.bypassPatch(props.operationId, op.value.diagnosis.patch))
}

const shortSha = (sha) => sha?.slice(0, 7) || '—'

// Green sha = the checkout happened; gray = still just the plan.
const revisionHint = (app) => {
  const target = shortSha(app.updated_sha || app.target_sha)
  return app.updated_sha ? `Updated to ${target}` : `Will update to ${target}`
}

const anythingFailed = computed(
  () =>
    (op.value?.sites || []).some((site) => siteStatus(site).value === 'failed') ||
    serverJobs.value.some((job) => job.status === 'failed'),
)

// A settled run starts collapsed; anything unresolved or failed starts open.
const sitesOpen = ref(true)
const appsOpen = ref(true)
const serverOpen = ref(true)
let openDefaultsSet = false

const applyOpenDefaults = () => {
  if (openDefaultsSet || !op.value) return
  openDefaultsSet = true
  const settled = isResolved(op.value) && !anythingFailed.value
  serverOpen.value = !settled
  sitesOpen.value = !settled
  appsOpen.value = !settled
}

const siteCaption = (site) => {
  const status = siteStatus(site)
  if (status.value === 'pending') return ''
  if (status.value === 'success') return 'Migrated'
  return status.label
}

onMounted(async () => {
  useAppRegistry().load()
  loading.value = true
  try {
    await load()
  } finally {
    loading.value = false
  }
})
onUnmounted(() => clearTimeout(timer))
</script>

<template>
  <div class="p-3 md:p-4 mx-auto max-w-3xl">
    <div v-if="loading && !op" class="px-2">
      <div class="flex justify-between items-center py-2">
        <Skeleton class="rounded-4 w-44 h-4" />
        <Skeleton class="rounded-4 w-24 h-4" />
      </div>

      <div class="mt-6 p-1 border border-outline-gray-2 rounded-6">
        <div class="flex justify-between items-center px-2.5 py-2">
          <Skeleton class="rounded-4 w-24 h-4" />
          <Skeleton class="rounded-4 w-12 h-3" />
        </div>

        <div
          v-for="index in 3"
          :key="index"
          class="px-2.5 py-2"
          :style="{ opacity: 1 - index * 0.25 }"
        >
          <Skeleton class="rounded-4 w-48 h-4" />
        </div>
      </div>
    </div>

    <ErrorMessage v-else-if="error && !op" class="mt-4" :message="error" />

    <template v-else-if="op">
      <Teleport defer to="#header-badge">
        <Badge v-if="pending" theme="amber" :label="pendingLabel" />
        <UpdateStateBadge v-else :state="op.state" />
      </Teleport>

      <Teleport defer to="#header-actions">
        <Button
          icon="lucide-refresh-cw"
          label="Refresh"
          tooltip="Refresh"
          :loading="refreshing"
          @click="refresh"
        />
      </Teleport>

      <div class="flex items-center gap-3 mt-5 px-2 text-sm">
        <span v-if="startedAt" class="flex items-center gap-1.5 text-ink-gray-7">
          <span class="size-3.5 lucide-clock" />
          {{ startedAt }}
        </span>

        <span v-if="duration" class="flex items-center gap-1.5 ml-auto text-ink-gray-5">
          <span class="size-3.5 lucide-timer" />
          <span class="tabular-nums">{{ duration }}</span>
        </span>
      </div>

      <ErrorMessage v-if="error" class="mt-4" :message="error" />

      <!-- Unresolved failure -->
      <section v-if="isAttention" class="mt-4 overflow-hidden rounded-6 border border-outline-gray-2">
        <div class="p-4">
          <div class="flex items-center gap-2">
            <span class="lucide-alert-triangle size-4 shrink-0 text-ink-red-5" />
            <h2 class="font-medium text-ink-red-7">
              {{ alertTitle }}
            </h2>
          </div>

          <pre
            v-if="op.diagnosis?.message"
            class="mt-2 max-h-96 overflow-auto whitespace-pre-wrap break-words font-mono text-xs leading-5 text-ink-gray-8"
            >{{ op.diagnosis.message }}</pre>
          <p v-if="op.diagnosis?.patch" class="mt-2 text-p-sm text-ink-gray-7">
            Failing patch
            <code
              class="ml-1 rounded-4 bg-surface-gray-2 px-1.5 py-0.5 font-mono text-xs text-ink-gray-8"
            >
              {{ op.diagnosis.patch }}
            </code>
          </p>

          <p
            v-if="patchAlreadySkipped"
            class="mt-2 flex items-center gap-1 text-p-sm font-medium text-ink-green-6"
          >
            <span class="lucide-check size-4" />
            Patch skipped
          </p>

          <p class="mt-3 text-p-sm text-ink-gray-6">
            <template v-if="op.state === 'revert_failed'"
              >Fix the cause and run the restore again.</template
            >
            <template v-else>
              Fix the cause manually, then retry. Or restore everything back to its pre-update
              state.
            </template>
          </p>

          <!-- Links to the running action task so the user can watch it. -->
          <button
            v-if="pending"
            type="button"
            class="mt-4 flex items-center gap-2 px-2 py-1 rounded-4 transition-colors text-p-sm text-ink-gray-7 hover:bg-surface-gray-1"
            @click="openTaskLog({ id: pending.task_id })"
          >
            <Spinner size="md" class="text-ink-amber-6" />
            {{ pendingLabel }}
            <span class="lucide-square-terminal size-4" />
          </button>

          <!-- Skip patch leads: it is the cheapest recovery, ahead of a restore. -->
          <div v-else class="mt-4 flex flex-wrap gap-2">
            <Button
              v-if="op.state === 'needs_attention' && op.diagnosis?.patch && !patchAlreadySkipped"
              theme="red"
              :loading="acting"
              @click="confirmSkip = true"
            >
              Skip patch
            </Button>

            <Button
              v-if="op.state === 'needs_attention'"
              :loading="acting"
              @click="doRetry"
            >
              Retry update
            </Button>

            <Button
              v-if="op.can_restore"
              :loading="acting"
              @click="confirmRestore = true"
            >
              Restore backup
            </Button>
          </div>
        </div>

        <div
          v-if="op.diagnosis?.output_excerpt"
          class="border-t border-outline-gray-1 px-2.5 py-1.5"
        >
          <Button variant="ghost" @click="showOutput = !showOutput">
            {{ showOutput ? 'Hide error output' : 'Show error output' }}
            <template #suffix>
              <span
                class="size-4 transition-transform lucide-chevron-down"
                :class="showOutput ? 'rotate-180' : ''"
              />
            </template>
          </Button>

          <LogView v-if="showOutput" class="mt-1 mb-2" :lines="outputLines" wrap />
        </div>
      </section>

      <!-- Run order: apps update first, then sites migrate. -->
      <div
        v-if="op.sites?.length || op.apps?.length || serverJobs.length"
        class="mt-3 space-y-2"
      >
        <UpdateSection
          v-if="op.apps?.length"
          v-model:open="appsOpen"
          icon="lucide-box"
          title="Target apps"
          :count="op.apps.length"
        >
          <div
            v-for="app in op.apps"
            :key="app.name"
            class="flex items-center justify-between gap-4 px-2.5 py-2"
          >
            <div class="flex items-center gap-2 min-w-0 flex-1">
              <AppIcon :name="app.name" class="!rounded-1 size-5" initial-class="text-xs" />
              <p class="min-w-0 truncate">
                {{ app.name }}
              </p>
            </div>

            <Tooltip :text="revisionHint(app)">
              <component
                :is="app.compare_url ? 'a' : 'div'"
                :href="app.compare_url || undefined"
                target="_blank"
                rel="noopener noreferrer"
                class="flex shrink-0 items-center gap-2 font-mono text-xs text-ink-gray-6"
                :class="app.compare_url ? 'hover:text-ink-gray-8' : ''"
              >
                <span>{{ shortSha(app.sha) }}</span>
                <span class="lucide-arrow-right size-3.5 text-ink-gray-4" aria-hidden="true" />
                <span :class="app.updated_sha ? 'text-ink-green-6' : 'text-ink-gray-5'">
                  {{ shortSha(app.updated_sha || app.target_sha) }}
                </span>

                <span
                  v-if="app.compare_url"
                  class="lucide-external-link size-3.5 text-ink-gray-4"
                  aria-hidden="true"
                />
              </component>
            </Tooltip>
          </div>
        </UpdateSection>

        <!-- Bench-wide jobs, run before any site is touched. -->
        <UpdateSection
          v-if="serverJobs.length"
          v-model:open="serverOpen"
          icon="lucide-server"
          title="Server"
          :count="serverJobs.length"
        >
          <JobRow
            v-for="job in serverJobs"
            :key="job.id"
            :job="job"
            @click="openTaskLog(job)"
          />
        </UpdateSection>

        <UpdateSection
          v-if="op.sites?.length"
          v-model:open="sitesOpen"
          icon="lucide-globe"
          title="Sites"
          :count="sitesCount"
        >
          <div v-for="site in op.sites" :key="site.name">
            <div
              class="flex items-center gap-2 px-2.5 py-2 rounded-4 transition-colors"
              :class="siteJobs(site.name).length ? 'cursor-pointer hover:bg-surface-gray-1' : ''"
              @click="toggleSiteJobs(site.name)"
            >
              <span
                class="size-4 text-ink-gray-5 transition-transform shrink-0 lucide-chevron-right"
                :class="[
                  siteJobs(site.name).length ? '' : 'invisible',
                  expandedSites.has(site.name) ? 'rotate-90' : '',
                ]"
              />
              <p class="flex-1 min-w-0 truncate">
                {{ site.name }}
              </p>

              <span
                v-if="siteCaption(site)"
                class="flex items-center gap-1.5 text-sm shrink-0"
                :class="siteStatus(site).value === 'failed' ? 'text-ink-red-6' : 'text-ink-gray-5'"
              >
                <Spinner v-if="siteStatus(site).busy" size="sm" class="text-ink-amber-6" />
                {{ siteCaption(site) }}
              </span>
            </div>

            <div
              v-if="expandedSites.has(site.name)"
              class="mb-1 ml-4 pl-3 border-l border-outline-gray-2"
            >
              <JobRow
                v-for="job in siteJobs(site.name)"
                :key="job.id"
                :job="job"
                @click="openTaskLog(job)"
              />
            </div>
          </div>
        </UpdateSection>
      </div>

      <UpdateSection
        v-if="op.decisions?.length"
        class="mt-2"
        icon="lucide-skip-forward"
        title="Skipped patches"
        :count="op.decisions.length"
      >
        <div
          v-for="(decision, index) in op.decisions"
          :key="index"
          class="px-2.5 py-2 text-sm text-ink-gray-7"
        >
          <code class="rounded-4 bg-surface-gray-2 px-1 font-mono text-xs">{{ decision.patch }}</code>
          on
          <span class="font-medium text-ink-gray-8">{{ decision.site }}</span>
        </div>
      </UpdateSection>

      <Dialog v-model="confirmSkip" title="Skip this patch permanently?">
        <p class="text-p-sm text-ink-gray-6">
          Skipping marks
          <code class="rounded-4 bg-surface-gray-2 px-1 font-mono">{{ op.diagnosis?.patch }}</code>
          as completed for
          <b class="text-ink-gray-9">{{ op.failed_site }}</b> without running it. This cannot be
          undone, and the migration carries on from where it stopped.
        </p>

        <template #actions>
          <Button variant="solid" theme="red" :loading="acting" @click="doSkip"
            >Skip patch</Button
          >
        </template>
      </Dialog>

      <Dialog v-model="confirmRestore" title="Restore this update?">
        <p class="text-p-sm text-ink-gray-6">
          Apps return to their previous revisions, and migrated sites get their pre-update data
          back from the recovery backup. Sites that were not migrated yet are left untouched.
        </p>

        <template #actions>
          <Button variant="solid" theme="red" :loading="acting" @click="doRestore"
            >Restore backup</Button
          >
        </template>
      </Dialog>
    </template>
  </div>
</template>
