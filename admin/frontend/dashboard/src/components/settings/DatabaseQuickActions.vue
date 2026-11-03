<script setup lang="ts">
import {
  Alert,
  Badge,
  Button,
  Dialog,
  ErrorMessage,
  Progress,
  Slider,
  Spinner,
  Switch,
  TextInput,
  Tooltip,
} from 'frappe-ui'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { databaseApi } from '@/api/database'
import BinlogSettingsDialog from '@/components/settings/BinlogSettingsDialog.vue'
import type { DatabaseActionCapabilities, DatabaseCapabilities } from '@/types/database'
import { errorMessage } from '@/utils/error'
import { openTaskDetailPage } from '@/utils/taskRoute'

// The five capability payloads share available/reason and differ past that, so one
// reader type keeps them and the unavailable fallback interchangeable.
interface ActionCapability {
  available: boolean
  reason: string
  requires_restart?: boolean
  enabled?: boolean | null
  current?: number | null
  min?: number | null
  max?: number | null
  recommended?: number | null
  current_mb?: number | null
  min_mb?: number | null
  max_mb?: number | null
  recommended_mb?: number | null
  dynamic_max_mb?: number | null
  unit?: 'MB'
}

interface ConfirmationBase {
  title: string
  buttonLabel: string
  message: string
  idempotencyKey: string
}

interface RestartConfirmation extends ConfirmationBase {
  action: 'restart'
}

interface PerformanceSchemaConfirmation extends ConfirmationBase {
  action: 'performance_schema'
  enabled: boolean
}

interface PendingSizing {
  action: 'innodb_buffer_pool_size' | 'max_connections'
  title: string
  inputLabel: string
  current: number | null
  recommended: number | null
  min: number | null
  max: number | null
  dynamicMax: number | null
  unit: string
  idempotencyKey: string
}

const router = useRouter()

const loading = ref(false)
const capabilities = ref<DatabaseCapabilities | null>(null)
const error = ref('')
const actionError = ref('')
const activeAction = ref('')
const binlogsOpen = ref(false)
const confirmation = ref<RestartConfirmation | PerformanceSchemaConfirmation | null>(null)
const sizingAction = ref<PendingSizing | null>(null)
const sizingValue = ref<number | string>('')

const confirmationOpen = computed({
  get: () => Boolean(confirmation.value),
  set: (value) => {
    if (!value && !activeAction.value) confirmation.value = null
  },
})
const confirmationTitle = computed(() => confirmation.value?.title || 'Database action')
const sizingValidationError = computed(() => {
  const pending = sizingAction.value
  if (!pending) return ''

  const value = sizingValue.value

  if (typeof value !== 'number' || !Number.isInteger(value)) return 'Enter a whole number.'
  if (
    (pending.min !== null && value < pending.min) ||
    (pending.max !== null && value > pending.max)
  ) {
    return `Enter a value between ${pending.min} and ${pending.max}.`
  }
  return ''
})
const sizingRequiresRestart = computed(() => {
  const pending = sizingAction.value
  if (pending?.action !== 'innodb_buffer_pool_size') return false

  return (
    pending.dynamicMax !== null &&
    Number.isFinite(pending.dynamicMax) &&
    typeof sizingValue.value === 'number' &&
    sizingValue.value > pending.dynamicMax
  )
})
const sizingUnchanged = computed(
  () => sizingAction.value !== null && sizingValue.value === sizingAction.value.current,
)
const showManagedMariaDBDisclaimer = computed(
  () =>
    capabilities.value !== null &&
    (capabilities.value.engine !== 'mariadb' || !capabilities.value.managed),
)
const action = (name: keyof DatabaseActionCapabilities): ActionCapability => {
  return (
    capabilities.value?.actions?.[name] || {
      available: false,
      reason: 'Database capabilities are unavailable.',
    }
  )
}

const formatMegabytes = (megabytes: number) => {
  if (megabytes < 1024) return { amount: String(megabytes), unit: 'MB' }

  return { amount: String(Number((megabytes / 1024).toFixed(1))), unit: 'GB' }
}

const cancelSizing = () => {
  if (activeAction.value) return

  sizingAction.value = null
  actionError.value = ''
}

const idempotencyKey = (actionName: string) => {
  const random = globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2)
  return `database-${actionName}-${Date.now()}-${random}`
}

const confirmRestart = () => {
  actionError.value = ''
  confirmation.value = {
    action: 'restart',
    title: 'Restart MariaDB',
    buttonLabel: 'Restart MariaDB',
    message:
      'Sites, web requests, workers, and scheduled jobs may briefly lose their database connection.',
    idempotencyKey: idempotencyKey('restart'),
  }
}

const confirmPerformanceSchema = (enabled: boolean) => {
  if (action('performance_schema').enabled === enabled) return
  actionError.value = ''
  confirmation.value = {
    action: 'performance_schema',
    enabled,
    title: `${enabled ? 'Enable' : 'Disable'} Performance Schema`,
    buttonLabel: `${enabled ? 'Enable' : 'Disable'} and restart`,
    message: `Pilot will ${enabled ? 'enable' : 'disable'} Performance Schema and restart MariaDB. Sites, web requests, workers, and scheduled jobs may briefly lose their database connection.`,
    idempotencyKey: idempotencyKey(`performance-${enabled ? 'on' : 'off'}`),
  }
}

const openSizingAction = (actionName: 'innodb_buffer_pool_size' | 'max_connections') => {
  actionError.value = ''
  memorySplitOpen.value = false
  const capability = action(actionName)
  if (!capability.available) return
  if (actionName === 'innodb_buffer_pool_size') {
    sizingValue.value = capability.current_mb ?? ''
    sizingAction.value = {
      action: actionName,
      title: 'Update InnoDB Buffer Pool Size',
      inputLabel: 'Buffer Pool size (MB)',
      current: capability.current_mb ?? null,
      recommended: capability.recommended_mb ?? null,
      min: capability.min_mb ?? null,
      max: capability.max_mb ?? null,
      dynamicMax: capability.dynamic_max_mb ?? null,
      unit: 'MB',
      idempotencyKey: idempotencyKey('innodb-buffer-pool-size'),
    }
    return
  }
  sizingValue.value = capability.current ?? ''
  sizingAction.value = {
    action: actionName,
    title: 'Update Max DB Connections',
    inputLabel: 'Maximum connections',
    current: capability.current ?? null,
    recommended: capability.recommended ?? null,
    min: capability.min ?? null,
    max: capability.max ?? null,
    dynamicMax: null,
    unit: '',
    idempotencyKey: idempotencyKey('max-connections'),
  }
}

const runConfirmedAction = async () => {
  if (!confirmation.value || activeAction.value) return
  const pending = confirmation.value
  activeAction.value = pending.action
  actionError.value = ''
  try {
    const task =
      pending.action === 'restart'
        ? await databaseApi.quickActions.restart(pending.idempotencyKey)
        : await databaseApi.quickActions.setPerformanceSchema(
            pending.enabled,
            pending.idempotencyKey,
          )
    confirmation.value = null
    openTaskDetailPage(router, task.task_id)
  } catch (e) {
    actionError.value = errorMessage(e, 'Could not start the database action.')
  } finally {
    activeAction.value = ''
  }
}

const runSizingAction = async () => {
  if (
    !sizingAction.value ||
    sizingValidationError.value ||
    sizingUnchanged.value ||
    activeAction.value
  )
    return
  const pending = sizingAction.value
  const size = sizingValue.value
  if (typeof size !== 'number') return

  activeAction.value = pending.action
  actionError.value = ''
  try {
    const task =
      pending.action === 'innodb_buffer_pool_size'
        ? await databaseApi.quickActions.setInnoDBBufferPoolSize(size, pending.idempotencyKey)
        : await databaseApi.quickActions.setMaxConnections(size, pending.idempotencyKey)
    sizingAction.value = null
    openTaskDetailPage(router, task.task_id)
  } catch (e) {
    actionError.value = errorMessage(e, 'Could not start the database action.')
  } finally {
    activeAction.value = ''
  }
}

const memoryShare = computed(() => capabilities.value?.actions.memory_share ?? null)
const memorySplitOpen = ref(false)
const memorySplitPercent = ref(50)
const memorySplitIdempotencyKey = ref('')

const formatGB = (mb: number) => `${(mb / 1024).toFixed(1)} GB`
const sharePercent = (share: number) => Math.round(share * 100)

const editStat = (edit: () => void) => {
  sizingAction.value = null
  memorySplitOpen.value = false
  actionError.value = ''

  edit()
}

const memoryBreakdown = computed(() => {
  const share = memoryShare.value

  if (share?.current == null || !share.total_memory_mb) return null

  const database = sharePercent(share.current)

  return {
    database,
    databaseSize: formatGB((share.total_memory_mb * database) / 100),
    appsSize: formatGB((share.total_memory_mb * (100 - database)) / 100),
  }
})

const stats = computed(() => {
  const bufferPool = action('innodb_buffer_pool_size')
  const connections = action('max_connections')
  const databaseLimit = memoryBreakdown.value
    ? ((memoryShare.value?.total_memory_mb ?? 0) * memoryBreakdown.value.database) / 100
    : 0

  const total = memoryShare.value?.total_memory_mb ?? 0

  return [
    {
      key: 'memory_share',
      label: 'Database memory',
      icon: 'lucide-memory-stick',
      available: Boolean(memoryShare.value?.available),
      amount: memoryShare.value?.available ? String(memoryPercent.value) : '-',
      unit: memoryShare.value?.available ? '%' : '',
      note: total ? `${formatGB((total * memoryPercent.value) / 100)} of ${formatGB(total)}` : '',
      percent: memoryBreakdown.value ? memoryPercent.value : null,
      detail: total ? `Apps get ${formatGB((total * (100 - memoryPercent.value)) / 100)}` : '',
      edit: openMemorySplit,
    },
    {
      key: 'innodb_buffer_pool_size',
      label: 'Buffer pool',
      icon: 'lucide-database',
      available: bufferPool.available,
      ...(bufferPool.current_mb == null
        ? { amount: '-', unit: '' }
        : formatMegabytes(bufferPool.current_mb)),
      note: databaseLimit ? `of ${formatGB(databaseLimit)}` : '',
      detail: bufferPool.dynamic_max_mb
        ? `No restart up to ${Object.values(formatMegabytes(bufferPool.dynamic_max_mb)).join(' ')}`
        : '',
      percent:
        databaseLimit && bufferPool.current_mb != null
          ? Math.min((bufferPool.current_mb / databaseLimit) * 100, 100)
          : null,
      edit: () => openSizingAction('innodb_buffer_pool_size'),
    },
    {
      key: 'max_connections',
      label: 'Max connections',
      icon: 'lucide-cable',
      available: connections.available,
      amount: connections.current == null ? '-' : String(connections.current),
      unit: '',
      note: connections.recommended == null ? '' : `${connections.recommended} advised`,
      detail: connections.requires_restart ? 'Needs a restart' : 'No restart needed',
      percent:
        connections.current != null && connections.recommended
          ? Math.min((connections.current / connections.recommended) * 100, 100)
          : null,
      edit: () => openSizingAction('max_connections'),
    },
  ]
})

const memoryPercent = computed(() => memoryBreakdown.value?.database ?? 0)

const memorySplitReadout = computed(() => {
  const total = memoryShare.value?.total_memory_mb ?? 0
  const database = memorySplitPercent.value
  const change = (from: number, to: number) => {
    const difference = ((to - from) * total) / 100 / 1024

    if (!difference) return null

    return {
      label: `${difference > 0 ? '+' : '−'}${Math.abs(difference).toFixed(1)} GB`,
      theme: difference > 0 ? ('green' as const) : ('red' as const),
    }
  }

  return [
    {
      label: 'Database',
      color: 'text-ink-gray-8',
      percent: database,
      size: formatGB((total * database) / 100),
      change: change(memoryPercent.value, database),
      detail: memorySplitStep.value
        ? `Buffer pool ${formatGB(memorySplitStep.value.innodb_buffer_pool_mb)}`
        : '',
    },
    {
      label: 'Apps',
      color: 'text-ink-gray-4',
      percent: 100 - database,
      size: formatGB((total * (100 - database)) / 100),
      change: change(100 - memoryPercent.value, 100 - database),
      detail: 'Web, workers, Redis',
    },
  ]
})

const editNote = computed(() => {
  const pending = sizingAction.value

  if (!pending) return { text: '', class: '' }

  const label = stats.value.find((stat) => stat.key === pending.action)?.label

  if (sizingValidationError.value)
    return { text: `${label} · use ${pending.min} to ${pending.max}`, class: 'text-ink-red-5' }
  if (actionError.value) return { text: actionError.value, class: 'text-ink-red-5' }
  if (sizingRequiresRestart.value)
    return { text: `${label} · restarts MariaDB`, class: 'text-ink-amber-6' }

  return { text: `${label} · ${pending.recommended} advised`, class: 'text-ink-gray-5' }
})

const memorySplitSlider = computed({
  get: () => [memorySplitPercent.value],
  set: ([percent]: number[]) => {
    memorySplitPercent.value = percent
  },
})

const memorySplitStep = computed(() => {
  const steps = memoryShare.value?.steps ?? []
  const distance = (share: number) => Math.abs(sharePercent(share) - memorySplitPercent.value)

  return steps.reduce<(typeof steps)[number] | null>(
    (closest, step) =>
      !closest || distance(step.share) < distance(closest.share) ? step : closest,
    null,
  )
})

const openMemorySplit = () => {
  const capability = memoryShare.value
  if (!capability?.available || capability.current == null) return
  actionError.value = ''
  memorySplitPercent.value = sharePercent(capability.current)
  memorySplitIdempotencyKey.value = idempotencyKey('memory-share')
  memorySplitOpen.value = true
}

const applyMemorySplit = async () => {
  if (activeAction.value) return
  activeAction.value = 'memory_share'
  actionError.value = ''
  try {
    const task = await databaseApi.quickActions.setMemoryShare(
      memorySplitPercent.value / 100,
      memorySplitIdempotencyKey.value,
    )
    memorySplitOpen.value = false
    openTaskDetailPage(router, task.task_id)
  } catch (e) {
    actionError.value = errorMessage(e, 'Could not change the memory split.')
  } finally {
    activeAction.value = ''
  }
}

const load = async () => {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    capabilities.value = await databaseApi.quickActions.capabilities()
  } catch (e) {
    capabilities.value = null
    error.value = errorMessage(e, 'Could not load database capabilities.')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <Teleport defer to="#settings-header-actions">
    <Tooltip text="Refresh database capabilities">
      <Button
        variant="ghost"
        icon="lucide-refresh-cw"
        :loading="loading"
        aria-label="Refresh database capabilities"
        @click="load"
      />
    </Tooltip>
  </Teleport>

  <div v-if="loading && !capabilities" class="flex justify-center items-center h-40">
    <Spinner size="lg" class="text-ink-gray-4" />
  </div>

  <div v-else class="flex flex-col gap-6">
    <ErrorMessage v-if="error" :message="error" />

    <Alert
      v-if="showManagedMariaDBDisclaimer"
      title="Database actions only work on MariaDB that Pilot manages."
      :dismissible="false"
    />

    <template v-if="capabilities">
      <div class="gap-4 grid lg:grid-cols-3">
        <section
          v-for="stat in stats"
          :key="stat.key"
          class="flex flex-col gap-3 dark:bg-surface-gray-1 border border-outline-gray-1 dark:border-transparent p-4 rounded-6"
        >
          <header class="flex justify-between items-center gap-2 h-5 text-sm">
            <h4 class="flex items-center gap-2 min-w-0 text-ink-gray-8">
              <span class="size-4 text-ink-gray-4 shrink-0" :class="stat.icon" aria-hidden="true" />

              <span
                v-if="sizingAction?.action === stat.key"
                class="truncate"
                :class="editNote.class"
                :title="editNote.text"
              >
                {{ editNote.text }}
              </span>

              <span v-else class="truncate">{{ stat.label }}</span>
            </h4>

            <Button
              v-if="stat.available && sizingAction?.action !== stat.key"
              variant="ghost"
              size="sm"
              class="!text-ink-gray-5 hover:!text-ink-gray-7 -my-1"
              icon="lucide-chevron-right"
              :aria-label="`Edit ${stat.label}`"
              :disabled="Boolean(activeAction)"
              @click="editStat(stat.edit)"
            />
          </header>

          <div v-if="sizingAction?.action === stat.key" class="flex items-center gap-1.5 h-7">
            <TextInput
              v-model.number="sizingValue"
              type="number"
              size="sm"
              :class="['flex-1 min-w-20', sizingAction.unit ? '[&_input]:!pe-20' : '[&_input]:!pe-14']"
              :min="sizingAction.min"
              :max="sizingAction.max"
              step="1"
              autocomplete="off"
              autofocus
              :aria-label="sizingAction.inputLabel"
              @keydown.enter="runSizingAction"
              @keydown.esc="cancelSizing"
            >
              <template v-if="sizingAction.max !== null" #suffix>
                <span class="text-ink-gray-5 text-xs whitespace-nowrap">
                  of {{ sizingAction.max }} {{ sizingAction.unit }}
                </span>
              </template>
            </TextInput>

            <Button
              variant="ghost"
              size="sm"
              icon="lucide-x"
              aria-label="Cancel"
              :disabled="Boolean(activeAction)"
              @click="cancelSizing"
            />

            <Button
              size="sm"
              icon="lucide-check"
              :aria-label="sizingRequiresRestart ? 'Update and restart' : 'Update'"
              :loading="Boolean(activeAction)"
              :disabled="Boolean(sizingValidationError) || sizingUnchanged"
              @click="runSizingAction"
            />
          </div>

          <span v-else class="flex justify-between items-baseline gap-2 h-7 leading-7 tabular-nums">
            <span class="flex items-baseline gap-1">
              <span class="text-xl-semibold text-ink-gray-9">{{ stat.amount }}</span>
              <span v-if="stat.unit" class="text-ink-gray-5 text-sm">{{ stat.unit }}</span>
            </span>

            <span class="text-ink-gray-5 text-sm truncate">{{ stat.note }}</span>
          </span>

          <Progress
            v-if="stat.percent !== null"
            :value="stat.percent"
            size="sm"
            class="mt-auto"
            :class="{ invisible: sizingAction?.action === stat.key }"
          />

          <span v-if="stat.detail" class="tabular-nums text-ink-gray-5 text-sm">
            {{ stat.detail }}
          </span>
        </section>
      </div>

      <div class="flex flex-col -mx-3 inset-dividers">
        <div
          class="flex justify-between items-center gap-3 px-3 py-3 rounded-5 transition-colors cursor-pointer hover:bg-surface-gray-2"
          @click="
            action('performance_schema').available &&
              !activeAction &&
              confirmPerformanceSchema(!action('performance_schema').enabled)
          "
        >
          <div class="flex flex-col gap-1 min-w-0">
            <span class="text-base text-ink-gray-8">Performance Schema</span>

            <span class="text-ink-gray-6 text-p-sm">
              Collect database instrumentation for deeper performance diagnostics.
            </span>
          </div>

          <Switch
            :model-value="Boolean(action('performance_schema').enabled)"
            :disabled="!action('performance_schema').available || Boolean(activeAction)"
            aria-label="Performance Schema"
            @click.stop
            @update:model-value="confirmPerformanceSchema"
          />
        </div>

        <div class="flex justify-between items-center gap-3 px-3 py-3">
          <div class="flex flex-col gap-1 min-w-0">
            <span class="text-base text-ink-gray-8">Binary logs</span>
            <span class="text-ink-gray-6 text-p-sm">Retention, size limit and purging.</span>
          </div>

          <Button
            label="Manage"
            :disabled="!action('manage_binlogs').available"
            @click="binlogsOpen = true"
          />
        </div>

        <div class="flex justify-between items-center gap-3 px-3 py-3">
          <div class="flex flex-col gap-1 min-w-0">
            <span class="text-base text-ink-gray-8">MariaDB service</span>

            <span class="text-ink-gray-6 text-p-sm">
              Restart the database service and verify that it accepts connections.
            </span>
          </div>

          <Button
            label="Restart"
            :disabled="!action('restart').available || Boolean(activeAction)"
            :loading="activeAction === 'restart'"
            @click="confirmRestart"
          />
        </div>

        <slot />
      </div>
    </template>
  </div>

  <BinlogSettingsDialog v-model="binlogsOpen" />

  <Dialog v-model="memorySplitOpen" title="Memory split" size="lg">
    <div v-if="memoryShare?.total_memory_mb" class="flex flex-col gap-5">
      <p class="-mt-2 text-ink-gray-6 text-p-sm">
        Share {{ formatGB(memoryShare.total_memory_mb) }} of server memory between MariaDB and your
        apps.
      </p>

      <div class="flex items-center gap-5 bg-surface-gray-1 p-4 rounded-6 tabular-nums">
        <div class="relative place-items-center grid size-24 shrink-0">
          <svg
            viewBox="0 0 36 36"
            class="absolute inset-0 -rotate-90"
            fill="none"
            stroke-width="2.5"
            aria-hidden="true"
          >
            <circle cx="18" cy="18" r="15.9155" class="stroke-ink-gray-3" />

            <circle
              cx="18"
              cy="18"
              r="15.9155"
              :stroke-dasharray="`${memorySplitPercent} 100`"
              stroke-linecap="round"
              class="stroke-ink-gray-8 transition-all"
            />
          </svg>

          <span class="flex flex-col items-center gap-0.5">
            <span class="text-base-medium text-ink-gray-9">{{ memorySplitPercent }}%</span>
            <span class="text-ink-gray-5 text-xs">memory</span>
          </span>
        </div>

        <span class="self-stretch border-l border-outline-gray-2" />

        <div class="flex flex-col flex-1 gap-3 min-w-0">
          <div class="flex gap-10">
            <template v-for="side in memorySplitReadout" :key="side.label">
              <div class="flex flex-col gap-1.5 whitespace-nowrap">
                <span class="flex items-center gap-1.5 text-ink-gray-5 text-sm">
                  <span class="bg-current rounded-full size-2 shrink-0" :class="side.color" />
                  {{ side.label }}
                  · {{ side.percent }}%
                </span>

                <span class="flex items-center gap-1.5">
                  <span class="text-base-medium text-ink-gray-9">{{ side.size }}</span>

                  <Badge
                    v-if="side.change"
                    class="fade-in !rounded-3"
                    :label="side.change.label"
                    :theme="side.change.theme"
                    size="sm"
                  />
                </span>

                <span class="text-ink-gray-5 text-sm">{{ side.detail }}</span>
              </div>
            </template>
          </div>

          <Slider
            v-model="memorySplitSlider"
            :min="sharePercent(memoryShare.min ?? 0.2)"
            :max="sharePercent(memoryShare.max ?? 0.8)"
            :step="5"
            aria-label="Database share of server memory"
          />
        </div>
      </div>

      <ErrorMessage v-if="actionError" :message="actionError" />

      <div class="flex justify-end gap-2">
        <Button
          variant="ghost"
          label="Cancel"
          :disabled="Boolean(activeAction)"
          @click="memorySplitOpen = false"
        />

        <Button
          variant="solid"
          :label="memoryShare?.requires_restart ? 'Apply and restart' : 'Apply'"
          :loading="activeAction === 'memory_share'"
          :disabled="sharePercent(memoryShare?.current ?? 0) === memorySplitPercent"
          @click="applyMemorySplit"
        />
      </div>
    </div>
  </Dialog>

  <Dialog v-model="confirmationOpen" :title="confirmationTitle" size="sm">
    <p v-if="confirmation?.message" class="text-ink-gray-7 text-p-sm">
      {{ confirmation.message }}
    </p>

    <ErrorMessage v-if="actionError" :message="actionError" class="mt-3" />

    <template #actions>
      <div class="flex justify-end gap-2">
        <Button variant="ghost" :disabled="Boolean(activeAction)" @click="confirmationOpen = false">
          Cancel
        </Button>

        <Button variant="solid" :loading="Boolean(activeAction)" @click="runConfirmedAction">
          {{ confirmation?.buttonLabel }}
        </Button>
      </div>
    </template>
  </Dialog>
</template>
