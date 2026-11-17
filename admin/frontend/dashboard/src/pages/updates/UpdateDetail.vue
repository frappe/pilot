<script setup lang="ts">
import { Alert, Badge, Button, Dialog, ErrorMessage, Skeleton, Spinner, Tooltip } from 'frappe-ui'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { isActive, isResolved, needsAttention, updatesApi } from '@/api/updates'
import AppIcon from '@/components/apps/AppIcon.vue'
import FloatingConsole from '@/components/updates/FloatingConsole.vue'
import LogView from '@/components/logs/LogView.vue'
import UpdateFlow from '@/components/updates/UpdateFlow.vue'
import UpdateStateBadge from '@/components/updates/UpdateStateBadge.vue'
import { useAppRegistry } from '@/composables/apps/useAppRegistry'
import { useBreadcrumbs } from '@/composables/common/useBreadcrumbs'
import type { MigrationAccepted, MigrationSummary } from '@/types/migrations'
import { processLine } from '@/utils/ansi'
import { errorMessage } from '@/utils/error'
import { fmtDateTime, fmtDuration } from '@/utils/taskFormat'
import { opTitle, patchSkipped, pendingActionLabel, skippedPatches } from '@/utils/updateFormat'

interface Props {
  operationId: string
}

const props = defineProps<Props>()
const router = useRouter()
const { setBreadcrumbs } = useBreadcrumbs()

const op = ref<MigrationSummary | null>(null)
const loading = ref(false)
const refreshing = ref(false)
const acting = ref(false)
const error = ref('')
const confirmSkip = ref(false)
const confirmRestore = ref(false)
const confirmDismiss = ref(false)
const confirmStop = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined

const title = computed(() => opTitle(op.value))
const isAttention = computed(() => needsAttention(op.value))
const pending = computed(() => op.value?.pending_action || null)
const pendingLabel = computed(() => pendingActionLabel(pending.value))
const patchAlreadySkipped = computed(() => patchSkipped(op.value))
const skipped = computed(() => skippedPatches(op.value))

const lastFinished = (operation: MigrationSummary) =>
  Math.max(0, ...(operation.chain || []).map((link) => (link.finished_at ? Date.parse(link.finished_at) : 0)))

const durationSeconds = computed(() => {
  const operation = op.value

  if (!operation?.started_at) return null

  const isPaused = !isResolved(operation) && !isActive(operation)
  const end = operation.finished_at
    ? Date.parse(operation.finished_at)
    : isPaused
      ? lastFinished(operation)
      : Date.now()

  if (!end) return null

  return Math.max(0, (end - Date.parse(operation.started_at)) / 1000)
})

const alertTitle = computed(() => {
  if (op.value?.state === 'revert_failed') return 'Restore failed'

  const phase = op.value?.diagnosis?.phase

  if (phase === 'backing_up') return 'Backup failed'
  if (phase === 'update') return 'Updating apps failed'
  if (phase === 'migrate' && op.value?.failed_site)
    return `Migration failed on ${op.value.failed_site}`

  return 'This update needs attention'
})

const showsFailure = computed(
  () =>
    isAttention.value ||
    (['reverted', 'dismissed'].includes(op.value?.state || '') && !!op.value?.diagnosis),
)

const showOutput = ref(false)
const outputLines = computed(() =>
  (op.value?.diagnosis?.output_excerpt || '').split('\n').map(processLine),
)

const { titleMap } = useAppRegistry()
const showAllApps = ref(false)
const isConsoleOpen = ref(false)

const shownApps = computed(() => {
  const apps = op.value?.apps || []

  return showAllApps.value ? apps : apps.slice(0, 8)
})

const facts = computed(() => {
  const sites = op.value?.sites || []

  return [
    { label: 'Started', icon: 'lucide-calendar', value: startedAt.value, note: '' },
    {
      label: 'Duration',
      icon: 'lucide-timer',
      value: duration.value || '-',
      note: '',
    },
    { label: 'Sites', icon: 'lucide-globe', value: String(sites.length), note: '' },
    {
      label: 'Backups',
      icon: 'lucide-database-backup',
      value: op.value?.safeguards_disabled ? 'Off' : `${backupsTaken.value} of ${sites.length}`,
      note: '',
    },
  ]
})

const backupsTaken = computed(
  () => (op.value?.sites || []).filter((site) => site.backup_status === 'backed_up').length,
)

const startedAt = computed(() => (op.value?.started_at ? fmtDateTime(op.value.started_at) : ''))

const duration = computed(() => fmtDuration(durationSeconds.value))

const openTaskLog = (log: { id: string }) =>
  router.push({ name: 'TaskDetail', params: { taskId: log.id } })

const load = async () => {
  try {
    op.value = await updatesApi.detail(props.operationId)
    error.value = ''
    setBreadcrumbs([{ label: 'Updates', route: { name: 'Updates' } }, { label: title.value }])
  } catch (caught) {
    error.value = errorMessage(caught, 'Could not load this update.')
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

const runAction = async (action: () => Promise<MigrationAccepted>) => {
  acting.value = true

  try {
    op.value = (await action()).operation || op.value
    await load()
  } catch (caught) {
    error.value = errorMessage(caught, 'Action failed.')
  } finally {
    acting.value = false
  }
}

const doRetry = () => runAction(() => updatesApi.retry(props.operationId))
const doRestore = () => {
  confirmRestore.value = false

  return runAction(() => updatesApi.restore(props.operationId))
}

const doDismiss = () => {
  confirmDismiss.value = false

  return runAction(() => updatesApi.dismiss(props.operationId))
}

const doStop = () => {
  confirmStop.value = false

  return runAction(() => updatesApi.stop(props.operationId))
}

const doSkip = () => {
  confirmSkip.value = false

  const patch = op.value?.diagnosis?.patch

  if (!patch) return

  return runAction(() => updatesApi.bypassPatch(props.operationId, patch))
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
  <div>
    <template v-if="loading && !op">
      <div class="flex flex-col gap-8 mx-auto p-3 md:p-4 md:pt-8 max-w-[1000px]">
        <Skeleton class="rounded-5 h-[68px]" />
        <Skeleton v-for="index in 4" :key="index" class="rounded-5 h-16" />
      </div>
    </template>

    <ErrorMessage v-else-if="error && !op" class="m-4" :message="error" />

    <template v-else-if="op">
      <Teleport defer to="#header-badge">
        <Badge v-if="pending" theme="amber" :label="pendingLabel" />
        <UpdateStateBadge v-else :state="op.state" />
      </Teleport>

      <Teleport defer to="#header-actions">
        <Button
          v-if="op.can_stop"
          theme="red"
          icon-left="lucide-square"
          :loading="acting"
          @click="confirmStop = true"
        >
          Stop update
        </Button>

        <Button
          :variant="isConsoleOpen ? 'subtle' : 'ghost'"
          icon-left="lucide-square-terminal"
          @click="isConsoleOpen = !isConsoleOpen"
        >
          Console
        </Button>

        <Button
          icon="lucide-refresh-cw"
          label="Refresh"
          tooltip="Refresh"
          :loading="refreshing"
          @click="refresh"
        />
      </Teleport>

      <div class="space-y-8 mx-auto p-3 md:p-4 md:pt-8 pb-10 md:pb-10 max-w-[1000px]">
        <dl
          class="flex flex-wrap items-stretch gap-y-3 px-5 py-3.5 border border-outline-gray-1.5 rounded-5 divide-x divide-outline-gray-1.5"
        >
          <div
            v-for="fact in facts"
            :key="fact.label"
            class="flex flex-col grow gap-1.5 px-5 first:pl-0"
          >
            <dt class="flex items-center gap-1.5 text-ink-gray-5 text-sm">
              <span class="size-3.5 text-ink-gray-4 shrink-0" :class="fact.icon" aria-hidden="true" />
              {{ fact.label }}
            </dt>
            <dd class="tabular-nums text-ink-gray-8 text-base-medium">{{ fact.value }}</dd>
          </div>

          <div v-if="op.apps.length" class="flex flex-col grow gap-1.5 px-5 first:pl-0">
            <dt class="flex items-center gap-1.5 text-ink-gray-5 text-sm">
              <span class="size-3.5 text-ink-gray-4 lucide-package shrink-0" aria-hidden="true" />
              Apps
            </dt>
            <dd class="flex flex-wrap items-center gap-1 h-4">
              <Tooltip v-for="app in shownApps" :key="app.name" :text="titleMap[app.name] || app.name">
                <AppIcon :name="app.name" :label="titleMap[app.name] || app.name" shape="circle" size="xs" />
              </Tooltip>

              <Button
                v-if="op.apps.length > 8"
                size="xs"
                variant="ghost"
                :label="showAllApps ? 'Show less' : `${op.apps.length - 8} more`"
                @click="showAllApps = !showAllApps"
              />
            </dd>
          </div>
        </dl>

        <ErrorMessage v-if="error" :message="error" />

        <FloatingConsole
          v-if="isConsoleOpen"
          :site="op.failed_site || op.sites[0]?.name || ''"
          :sites="op.sites.map((site) => site.name)"
          @close="isConsoleOpen = false"
        />

        <section>
          <h2 class="mb-4 text-ink-gray-8 text-base-medium">Stages</h2>

          <UpdateFlow :op="op">
            <template #failure>
            <Alert
              v-if="showsFailure"
              class="mt-4 first:mt-0"
              theme="red"
              :title="alertTitle"
              :dismissible="isAttention && !pending"
              @dismiss="confirmDismiss = true"
            >
              <template #description>
                <p v-if="op.diagnosis?.message">{{ op.diagnosis.message }}</p>

                <p v-if="op.diagnosis?.patch" class="mt-1 font-mono text-xs">
                  {{ op.diagnosis.patch }}{{ patchAlreadySkipped ? ' · skipped' : '' }}
                </p>

                <template v-if="op.diagnosis?.output_excerpt">
                  <Button
                    class="-ml-2 mt-2"
                    size="sm"
                    variant="ghost"
                    :icon-right="showOutput ? 'lucide-chevron-up' : 'lucide-chevron-down'"
                    @click="showOutput = !showOutput"
                  >
                    {{ showOutput ? 'Hide error output' : 'Show error output' }}
                  </Button>

                  <LogView v-if="showOutput" class="bg-transparent mt-2 border border-outline-gray-2" :lines="outputLines" wrap dense />
                </template>
              </template>

              <template v-if="isAttention" #actions>
                <div class="flex items-center gap-2">
                  <Button
                    v-if="pending"
                    size="xs"
                    variant="subtle"
                    icon-right="lucide-square-terminal"
                    @click="openTaskLog({ id: pending.task_id })"
                  >
                    <template #prefix><Spinner size="xs" /></template>
                    {{ pendingLabel }}
                  </Button>

                  <template v-else>
                    <Button
                      v-if="op.state === 'needs_attention'"
                      size="xs"
                      variant="solid"
                      icon-left="lucide-rotate-ccw"
                      :loading="acting"
                      @click="doRetry"
                    >
                      Retry
                    </Button>

                    <Button
                      v-if="op.state === 'needs_attention' && op.diagnosis?.patch && !patchAlreadySkipped"
                      size="xs"
                      variant="subtle"
                      icon-left="lucide-skip-forward"
                      :loading="acting"
                      @click="confirmSkip = true"
                    >
                      Skip patch
                    </Button>

                    <Button
                      size="xs"
                      variant="subtle"
                      icon-left="lucide-square-terminal"
                      @click="isConsoleOpen = true"
                    >
                      Open console
                    </Button>

                    <Button
                      v-if="op.can_restore"
                      size="xs"
                      variant="subtle"
                      icon-left="lucide-history"
                      :loading="acting"
                      @click="confirmRestore = true"
                    >
                      Restore backup
                    </Button>
                  </template>
                </div>
              </template>
            </Alert>
            </template>
          </UpdateFlow>
        </section>

        <section v-if="skipped.length">
          <h2 class="flex items-center gap-2 mb-4 text-ink-gray-8 text-base-medium">
            Skipped patches
            <span class="tabular-nums text-ink-gray-5 text-sm">{{ skipped.length }}</span>
          </h2>

          <ul class="border border-outline-gray-2 rounded-5 divide-y divide-outline-gray-1">
            <li
              v-for="(decision, index) in skipped"
              :key="index"
              class="flex items-center gap-3 px-4 h-11 text-base"
            >
              <code class="flex-1 min-w-0 font-mono text-ink-gray-7 text-xs truncate">
                {{ decision.patch }}
              </code>

              <span class="text-ink-gray-5">{{ decision.site }}</span>
            </li>
          </ul>
        </section>
      </div>

      <Dialog
        v-model="confirmSkip"
        title="Skip this patch?"
        :message="`${op.diagnosis?.patch} is marked as done on ${op.failed_site} without running. This can't be undone.`"
        :actions="[{ label: 'Skip patch', variant: 'solid', theme: 'red', onClick: doSkip }]"
      />

      <Dialog
        v-model="confirmRestore"
        title="Restore backup?"
        message="Apps go back to their old versions, and migrated sites get their data back from the backup."
        :actions="[{ label: 'Restore backup', variant: 'solid', theme: 'red', onClick: doRestore }]"
      />

      <Dialog
        v-model="confirmDismiss"
        title="Dismiss this update?"
        message="Pilot restarts services and deletes the backup. Sites stay as they are now."
        :actions="[{ label: 'Dismiss', variant: 'solid', theme: 'red', onClick: doDismiss }]"
      />

      <Dialog
        v-model="confirmStop"
        title="Stop this update?"
        message="No app or site changed yet. Pilot stops the backup, deletes the backups of this update and takes the sites out of maintenance mode."
        :actions="[{ label: 'Stop update', variant: 'solid', theme: 'red', onClick: doStop }]"
      />
    </template>
  </div>
</template>
